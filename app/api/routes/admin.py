import asyncio
import json
import logging
import os
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.config import get_env
from app.services.market_utils import MarketUtils

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

orchestrator = None
telegram_alerts = None

COMPETITIONS = {
    "premier_league": "PL",
    "serie_a": "SA",
    "la_liga": "PD",
    "bundesliga": "BL1",
    "ligue_1": "FL1",
}


def set_orchestrator(orch):
    global orchestrator
    orchestrator = orch


def set_telegram_alerts(alerts):
    global telegram_alerts
    telegram_alerts = alerts


def _verify_key(api_key: str):
    expected = get_env("API_KEY", "dev_api_key_12345")
    if api_key != expected:
        raise HTTPException(status_code=403, detail="Invalid admin key")


async def _fetch_fixtures(count: int) -> list:
    football_key = os.getenv("FOOTBALL_DATA_API_KEY", "")
    fixtures = []

    async with httpx.AsyncClient(timeout=15) as client:
        for league, code in COMPETITIONS.items():
            if len(fixtures) >= count:
                break
            try:
                r = await client.get(
                    f"https://api.football-data.org/v4/competitions/{code}/matches",
                    headers={"X-Auth-Token": football_key},
                    params={"status": "SCHEDULED", "limit": 5},
                )
                if r.status_code == 200:
                    for m in r.json().get("matches", []):
                        fixtures.append({
                            "home_team": m["homeTeam"]["name"],
                            "away_team": m["awayTeam"]["name"],
                            "league": league,
                            "kickoff_time": m["utcDate"],
                            "market_odds": {},
                        })
                        if len(fixtures) >= count:
                            break
            except Exception as e:
                logger.warning(f"Fixture fetch failed for {league}: {e}")

    return fixtures[:count]


@router.get("/fixtures")
async def get_fixtures(
    api_key: str = Query(...),
    count: int = Query(default=10, le=25),
):
    _verify_key(api_key)
    fixtures = await _fetch_fixtures(count)
    return {"fixtures": fixtures, "total": len(fixtures)}


@router.get("/stream-predictions")
async def stream_predictions(
    api_key: str = Query(...),
    count: int = Query(default=10, le=20),
    force_alert: bool = Query(default=True),
):
    _verify_key(api_key)

    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialised")

    async def event_stream():
        from app.db.database import AsyncSessionLocal
        from app.db.models import Match, Prediction
        from app.services.alerts import BetAlert

        def sse(payload: dict) -> str:
            return f"data: {json.dumps(payload)}\n\n"

        yield sse({"type": "status", "message": "Fetching upcoming fixtures..."})

        fixtures = await _fetch_fixtures(count)

        if not fixtures:
            yield sse({"type": "error", "message": "No fixtures found from Football-Data API."})
            return

        yield sse({
            "type": "status",
            "message": f"Found {len(fixtures)} fixtures. Running ML ensemble...",
        })

        for idx, fixture in enumerate(fixtures):
            home = fixture["home_team"]
            away = fixture["away_team"]

            yield sse({
                "type": "progress",
                "current": idx + 1,
                "total": len(fixtures),
                "fixture": f"{home} vs {away}",
            })

            try:
                features = {
                    "home_team": home,
                    "away_team": away,
                    "league": fixture["league"],
                    "market_odds": fixture.get("market_odds", {}),
                }

                raw = await orchestrator.predict(features, f"{home}_vs_{away}_{idx}")
                preds = raw.get("predictions", {})

                home_prob = float(preds.get("home_prob", 0.34))
                draw_prob = float(preds.get("draw_prob", 0.33))
                away_prob = float(preds.get("away_prob", 0.33))
                over_25   = float(preds.get("over_2_5_prob", 0.5))
                btts      = float(preds.get("btts_prob", 0.5))

                home_odds = round(1 / home_prob, 2) if home_prob > 0 else 3.0
                draw_odds = round(1 / draw_prob, 2) if draw_prob > 0 else 3.5
                away_odds = round(1 / away_prob, 2) if away_prob > 0 else 3.0

                best_bet      = MarketUtils.determine_best_bet(
                    home_prob, draw_prob, away_prob,
                    home_odds, draw_odds, away_odds,
                )
                edge          = float(best_bet.get("edge", 0))
                stake         = float(min(best_bet.get("kelly_stake", 0.02), 0.05))
                best_side     = str(best_bet.get("best_side", "home"))
                consensus_prob = max(home_prob, draw_prob, away_prob)
                bet_odds      = (home_odds if best_side == "home"
                                 else draw_odds if best_side == "draw"
                                 else away_odds)

                kickoff_dt = datetime.fromisoformat(
                    fixture["kickoff_time"].replace("Z", "+00:00")
                ).replace(tzinfo=None)

                match_id = None
                async with AsyncSessionLocal() as db:
                    db_match = Match(
                        home_team=home,
                        away_team=away,
                        league=fixture["league"],
                        kickoff_time=kickoff_dt,
                    )
                    db.add(db_match)
                    await db.flush()

                    pred_obj = Prediction(
                        match_id=db_match.id,
                        home_prob=home_prob,
                        draw_prob=draw_prob,
                        away_prob=away_prob,
                        over_25_prob=over_25,
                        btts_prob=btts,
                        consensus_prob=consensus_prob,
                        final_ev=edge,
                        recommended_stake=stake,
                        confidence=0.5,
                        bet_side=best_side,
                        entry_odds=bet_odds,
                        raw_edge=edge,
                        normalized_edge=edge,
                        vig_free_edge=edge,
                    )
                    db.add(pred_obj)
                    await db.commit()
                    match_id = db_match.id

                alert_sent = False
                if telegram_alerts and telegram_alerts.enabled and (force_alert or edge > 0.02):
                    try:
                        alert = BetAlert(
                            match_id=match_id,
                            home_team=home,
                            away_team=away,
                            prediction=best_side,
                            probability=consensus_prob,
                            edge=edge,
                            stake=stake,
                            odds=bet_odds,
                            confidence=0.5,
                            kickoff_time=kickoff_dt,
                        )
                        alert_sent = await telegram_alerts.send_bet_alert(alert)
                    except Exception as e:
                        logger.warning(f"Telegram alert failed for {home} vs {away}: {e}")

                yield sse({
                    "type": "prediction",
                    "index": idx + 1,
                    "match_id": match_id,
                    "home_team": home,
                    "away_team": away,
                    "league": fixture["league"],
                    "kickoff": fixture["kickoff_time"][:10],
                    "home_prob": round(home_prob, 3),
                    "draw_prob": round(draw_prob, 3),
                    "away_prob": round(away_prob, 3),
                    "over_25": round(over_25, 3),
                    "btts": round(btts, 3),
                    "edge": round(edge, 4),
                    "stake": round(stake, 4),
                    "best_side": best_side,
                    "alert_sent": alert_sent,
                })

                await asyncio.sleep(3)

            except Exception as e:
                logger.error(f"Prediction failed for {home} vs {away}: {e}", exc_info=True)
                yield sse({
                    "type": "error",
                    "message": str(e),
                    "fixture": f"{home} vs {away}",
                    "index": idx + 1,
                })

        yield sse({"type": "done", "total": len(fixtures)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
