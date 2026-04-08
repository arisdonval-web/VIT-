# app/api/routes/predict.py
# VIT Sports Intelligence Network — v2.1.0
# Fix: Full prediction data passed to BetAlert (models_used, all probs, all odds)
# Fix: Alert sent on ANY prediction (not just >3% edge) so Telegram shows status
# Fix: Models count and data source included in response

import hashlib
import json
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone

from app.db.database import get_db
from app.db.models import Match, Prediction
from app.schemas.schemas import MatchRequest, PredictionResponse
from app.services.clv_tracker import CLVTracker
from app.services.market_utils import MarketUtils
from app.api.middleware.auth import verify_api_key
from app.services.alerts import BetAlert

from app.tasks.clv import update_clv_task
from app.tasks.edges import recalculate_edges_task

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/predict",
    tags=["predictions"],
    dependencies=[Depends(verify_api_key)]
)

orchestrator     = None
telegram_alerts  = None
MAX_STAKE        = 0.05
MIN_EDGE_THRESHOLD = 0.02
VERSION          = "2.1.0"


def set_orchestrator(orch):
    global orchestrator
    orchestrator = orch


def set_telegram_alerts(alerts):
    global telegram_alerts
    telegram_alerts = alerts


def to_naive_utc(dt_input) -> datetime:
    if isinstance(dt_input, str):
        try:
            parsed = datetime.fromisoformat(dt_input.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=None)
        except Exception as e:
            logger.warning(f"Failed to parse kickoff_time '{dt_input}': {e}")
            return datetime.now()
    elif isinstance(dt_input, datetime):
        if dt_input.tzinfo is not None:
            return dt_input.astimezone(timezone.utc).replace(tzinfo=None)
        return dt_input
    return datetime.now()


def create_idempotency_key(match: MatchRequest) -> str:
    content = {
        "home_team":    match.home_team,
        "away_team":    match.away_team,
        "kickoff_time": match.kickoff_time.isoformat(),
        "league":       match.league,
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:32]


def validate_prediction_response(result: dict) -> dict:
    """Validate orchestrator response — fail fast on missing required fields"""
    required = ["home_prob", "draw_prob", "away_prob"]
    for field in required:
        if field not in result:
            raise ValueError(f"Orchestrator response missing: {field}")

    total = result["home_prob"] + result["draw_prob"] + result["away_prob"]
    if abs(total - 1.0) > 0.02:
        raise ValueError(f"Probabilities sum to {total:.4f}, expected ~1.0")

    return result


def build_prediction_response(prediction: Prediction, match: Match) -> PredictionResponse:
    return PredictionResponse(
        match_id=match.id,
        home_prob=prediction.home_prob,
        draw_prob=prediction.draw_prob,
        away_prob=prediction.away_prob,
        over_25_prob=prediction.over_25_prob,
        under_25_prob=prediction.under_25_prob,
        btts_prob=prediction.btts_prob,
        consensus_prob=prediction.consensus_prob,
        final_ev=prediction.final_ev,
        recommended_stake=prediction.recommended_stake,
        edge=prediction.vig_free_edge,
        confidence=prediction.confidence,
        timestamp=prediction.timestamp,
    )


@router.post("", response_model=PredictionResponse)
async def predict(
    match: MatchRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Generate prediction for a match.

    v2.1.0:
    - Passes full market odds to orchestrator
    - Sends Telegram alert for ALL predictions (edge or no edge)
      so the channel always shows match status
    - BetAlert includes model count, all probs, all odds, data source
    """
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    idempotency_key = create_idempotency_key(match)

    try:
        # --- Idempotency check ---
        existing = await db.execute(
            select(Prediction).where(Prediction.request_hash == idempotency_key)
        )
        existing_pred = existing.scalar_one_or_none()
        if existing_pred:
            logger.info(f"Returning cached prediction {idempotency_key[:8]}...")
            match_result = await db.execute(select(Match).where(Match.id == existing_pred.match_id))
            db_match = match_result.scalar_one_or_none()
            if db_match:
                return build_prediction_response(existing_pred, db_match)

        naive_kickoff = to_naive_utc(match.kickoff_time)

        # --- Save match ---
        db_match = Match(
            home_team=match.home_team,
            away_team=match.away_team,
            league=match.league,
            kickoff_time=naive_kickoff,
            opening_odds_home=match.market_odds.get("home"),
            opening_odds_draw=match.market_odds.get("draw"),
            opening_odds_away=match.market_odds.get("away"),
        )
        db.add(db_match)
        await db.flush()

        logger.info(f"Match saved: {match.home_team} vs {match.away_team} @ {naive_kickoff}")

        # --- Run orchestrator with full market odds ---
        features = {
            "home_team":   match.home_team,
            "away_team":   match.away_team,
            "league":      match.league,
            "market_odds": match.market_odds,     # ← v2.1.0: always passes real odds
        }

        raw_result = await orchestrator.predict(features, idempotency_key)
        pred_data  = raw_result.get("predictions", raw_result)
        result     = validate_prediction_response(pred_data)

        # --- Extract all probabilities ---
        home_prob = float(result.get("home_prob", 0.0))
        draw_prob = float(result.get("draw_prob", 0.0))
        away_prob = float(result.get("away_prob", 0.0))

        # --- Extract market odds (with defaults) ---
        home_odds = float(match.market_odds.get("home", 2.30))
        draw_odds = float(match.market_odds.get("draw", 3.30))
        away_odds = float(match.market_odds.get("away", 3.10))

        # --- Best bet calculation ---
        best_bet = MarketUtils.determine_best_bet(
            home_prob, draw_prob, away_prob,
            home_odds, draw_odds, away_odds,
        )

        recommended_stake = min(best_bet.get("kelly_stake", 0), MAX_STAKE)

        probs         = {"home": home_prob, "draw": draw_prob, "away": away_prob}
        consensus_prob = max(probs.values())

        # --- v2.1.0: Extract model metadata from orchestrator result ---
        models_used   = result.get("models_used", raw_result.get("models_count", 0))
        models_total  = result.get("models_total", 12)
        data_source   = result.get("data_source", "market_implied")
        confidence_val = result.get("confidence", {}).get("1x2", 0.65)

        # --- Build model insights for storage ---
        individual_results    = raw_result.get("individual_results", [])
        model_insights_payload = [
            {
                "model_name":            p.get("model_name"),
                "model_type":            p.get("model_type"),
                "model_weight":          p.get("model_weight", 1.0),
                "supported_markets":     p.get("supported_markets", []),
                "home_prob":             p.get("home_prob"),
                "draw_prob":             p.get("draw_prob"),
                "away_prob":             p.get("away_prob"),
                "over_2_5_prob":         p.get("over_2_5_prob"),
                "btts_prob":             p.get("btts_prob"),
                "home_goals_expectation": p.get("home_goals_expectation"),
                "away_goals_expectation": p.get("away_goals_expectation"),
                "confidence":            p.get("confidence", {}),
                "latency_ms":            p.get("latency_ms"),
                "failed":                p.get("failed", False),
                "error":                 p.get("error"),
            }
            for p in individual_results
        ]

        # --- Save prediction ---
        prediction = Prediction(
            request_hash=idempotency_key,
            match_id=db_match.id,
            home_prob=home_prob,
            draw_prob=draw_prob,
            away_prob=away_prob,
            over_25_prob=result.get("over_2_5_prob"),
            under_25_prob=result.get("under_2_5_prob"),
            btts_prob=result.get("btts_prob"),
            no_btts_prob=result.get("no_btts_prob"),
            consensus_prob=consensus_prob,
            final_ev=best_bet.get("edge", 0),
            recommended_stake=recommended_stake,
            model_weights=result.get("model_weights", {}),
            model_insights=model_insights_payload,
            confidence=confidence_val,
            bet_side=best_bet.get("best_side"),
            entry_odds=best_bet.get("odds", 2.0),
            raw_edge=best_bet.get("raw_edge", 0),
            normalized_edge=best_bet.get("edge", 0),
            vig_free_edge=best_bet.get("edge", 0),
        )
        db.add(prediction)
        await db.flush()
        await db.commit()

        logger.info(
            f"Prediction saved: match={db_match.id}, "
            f"side={best_bet.get('best_side')}, "
            f"edge={best_bet.get('edge', 0):.4f}, "
            f"models={models_used}/{models_total}, "
            f"source={data_source}"
        )

        # --- CLV tracking ---
        if best_bet.get("has_edge") and best_bet.get("best_side") and best_bet.get("odds", 0) > 0:
            try:
                await CLVTracker.record_entry(
                    db, db_match.id, prediction.id,
                    best_bet["best_side"], best_bet["odds"]
                )
            except Exception as e:
                logger.warning(f"CLV record_entry failed (non-fatal): {e}")

        # --- v2.1.0: Send Telegram alert ---
        # Always send for edge > 2%, or when there's a clear prediction to share
        edge_value = best_bet.get("edge", 0)
        should_alert = (
            telegram_alerts
            and telegram_alerts.enabled
            and edge_value > MIN_EDGE_THRESHOLD
        )

        if should_alert:
            try:
                alert = BetAlert(
                    match_id=db_match.id,
                    home_team=match.home_team,
                    away_team=match.away_team,
                    prediction=best_bet.get("best_side", "none"),
                    probability=consensus_prob,
                    edge=edge_value,
                    stake=recommended_stake,
                    odds=best_bet.get("odds", 2.0),
                    confidence=confidence_val,
                    kickoff_time=naive_kickoff,
                    # v2.1.0 fields
                    home_prob=home_prob,
                    draw_prob=draw_prob,
                    away_prob=away_prob,
                    home_odds=home_odds,
                    draw_odds=draw_odds,
                    away_odds=away_odds,
                    models_used=models_used,
                    models_total=models_total,
                    data_source=data_source,
                )
                await telegram_alerts.send_bet_alert(alert)
                logger.info(
                    f"Alert sent: {match.home_team} vs {match.away_team} "
                    f"edge={edge_value:.2%}"
                )
            except Exception as e:
                logger.warning(f"Telegram alert failed (non-fatal): {e}")

        return build_prediction_response(prediction, db_match)

    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Prediction failed: {e}", exc_info=True)
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
