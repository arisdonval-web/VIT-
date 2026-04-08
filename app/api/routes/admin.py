# app/api/routes/admin.py
# VIT Sports Intelligence Network — v2.2.0 + v2.3.0
#
# v2.2.0 New Endpoints:
#   GET  /admin/models/status          — per-model status + error message
#   POST /admin/models/reload          — reload all or single model
#   GET  /admin/data-sources/status    — Football API + Odds API health check
#   POST /admin/matches/manual         — add a single fixture manually
#   POST /admin/upload/csv             — bulk upload fixtures via CSV
#
# v2.3.0 New Endpoints:
#   GET  /admin/accumulator/candidates — top picks per market type
#   POST /admin/accumulator/generate   — build top-10 accumulators
#   POST /admin/accumulator/send       — push an accumulator to Telegram

import asyncio
import csv
import io
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from itertools import combinations
from typing import List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import get_env
from app.services.market_utils import MarketUtils

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

orchestrator    = None
telegram_alerts = None

VERSION = "2.3.0"

COMPETITIONS = {
    "premier_league": "PL",
    "serie_a":        "SA",
    "la_liga":        "PD",
    "bundesliga":     "BL1",
    "ligue_1":        "FL1",
}


def set_orchestrator(orch):
    global orchestrator
    orchestrator = orch


def set_telegram_alerts(alerts):
    global telegram_alerts
    telegram_alerts = alerts


def _verify_key(api_key: str):
    auth_enabled = os.getenv("AUTH_ENABLED", "false").lower() == "true"
    if not auth_enabled:
        return
    expected = get_env("API_KEY", "dev_api_key_12345")
    if api_key != expected:
        raise HTTPException(status_code=403, detail="Invalid admin key")


# ======================================================================
# v2.2.0 — MODEL MANAGEMENT
# ======================================================================

@router.get("/models/status")
async def get_models_status(api_key: str = Query(...)):
    """
    Return per-model status, weight, and error message.
    Powers the Model Status Dashboard in the admin panel.
    """
    _verify_key(api_key)
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    status = orchestrator.get_model_status()
    return {
        "version": VERSION,
        "ready":   status.get("ready", 0),
        "total":   status.get("total", 12),
        "models":  status.get("models", []),
    }


class ReloadRequest(BaseModel):
    model_key: Optional[str] = None  # None = reload all


@router.post("/models/reload")
async def reload_models(body: ReloadRequest, api_key: str = Query(...)):
    """
    Reload all models or a single model by key.
    One-click fix for failed models.
    """
    _verify_key(api_key)
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    try:
        results = orchestrator.load_all_models()
        ready   = sum(1 for v in results.values() if v)
        return {
            "message": f"Reload complete: {ready}/{len(results)} models ready",
            "results": results,
            "ready":   ready,
            "total":   len(results),
        }
    except Exception as e:
        logger.error(f"Model reload failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ======================================================================
# v2.2.0 — DATA SOURCE HEALTH
# ======================================================================

@router.get("/data-sources/status")
async def data_sources_status(api_key: str = Query(...)):
    """
    Check connectivity to Football-Data.org and The Odds API.
    Shown as live/down/no-key badges in the admin panel.
    """
    _verify_key(api_key)

    football_key = os.getenv("FOOTBALL_DATA_API_KEY", "")
    odds_key     = os.getenv("ODDS_API_KEY", "") or os.getenv("THE_ODDS_API_KEY", "")

    results = {}

    # Football-Data.org
    if not football_key:
        results["football_data"] = {"status": "no_key", "message": "FOOTBALL_DATA_API_KEY not set"}
    else:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    "https://api.football-data.org/v4/competitions/PL",
                    headers={"X-Auth-Token": football_key},
                )
                if r.status_code == 200:
                    results["football_data"] = {"status": "live", "message": "Connected"}
                elif r.status_code == 403:
                    results["football_data"] = {"status": "error", "message": "Invalid API key"}
                elif r.status_code == 429:
                    results["football_data"] = {"status": "limited", "message": "Rate limited"}
                else:
                    results["football_data"] = {"status": "error", "message": f"HTTP {r.status_code}"}
        except Exception as e:
            results["football_data"] = {"status": "down", "message": str(e)}

    # The Odds API
    if not odds_key:
        results["odds_api"] = {"status": "no_key", "message": "ODDS_API_KEY not set"}
    else:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    "https://api.the-odds-api.com/v4/sports",
                    params={"apiKey": odds_key},
                )
                if r.status_code == 200:
                    remaining = r.headers.get("x-requests-remaining", "?")
                    results["odds_api"] = {"status": "live", "message": f"Connected — {remaining} requests remaining"}
                elif r.status_code == 401:
                    results["odds_api"] = {"status": "error", "message": "Invalid API key"}
                else:
                    results["odds_api"] = {"status": "error", "message": f"HTTP {r.status_code}"}
        except Exception as e:
            results["odds_api"] = {"status": "down", "message": str(e)}

    return {"sources": results, "checked_at": datetime.now(timezone.utc).isoformat()}


# ======================================================================
# v2.2.0 — MANUAL MATCH ENTRY
# ======================================================================

class ManualMatchRequest(BaseModel):
    home_team:    str
    away_team:    str
    league:       str = "premier_league"
    kickoff_time: str                      # ISO string
    home_odds:    float = 2.30
    draw_odds:    float = 3.30
    away_odds:    float = 3.10


@router.post("/matches/manual")
async def add_manual_match(body: ManualMatchRequest, api_key: str = Query(...)):
    """
    Add a single fixture manually and run a prediction immediately.
    Used when the Football-Data API is down.
    """
    _verify_key(api_key)
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    if body.home_team.strip() == body.away_team.strip():
        raise HTTPException(status_code=422, detail="Home and away teams must differ")

    if not MarketUtils.validate_odds_dict({
        "home": body.home_odds, "draw": body.draw_odds, "away": body.away_odds
    }):
        raise HTTPException(status_code=422, detail="Invalid odds — must be between 1.01 and 100")

    features = {
        "home_team":   body.home_team.strip(),
        "away_team":   body.away_team.strip(),
        "league":      body.league,
        "market_odds": {"home": body.home_odds, "draw": body.draw_odds, "away": body.away_odds},
    }

    try:
        raw   = await orchestrator.predict(features, f"manual_{body.home_team}_{body.away_team}")
        preds = raw.get("predictions", {})
        best  = MarketUtils.determine_best_bet(
            preds.get("home_prob", 0.33),
            preds.get("draw_prob", 0.33),
            preds.get("away_prob", 0.33),
            body.home_odds, body.draw_odds, body.away_odds,
        )
        return {
            "status":      "ok",
            "home_team":   body.home_team,
            "away_team":   body.away_team,
            "predictions": preds,
            "best_bet":    best,
        }
    except Exception as e:
        logger.error(f"Manual match prediction failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ======================================================================
# v2.2.0 — CSV BULK UPLOAD
# ======================================================================

@router.post("/upload/csv")
async def upload_csv_fixtures(
    api_key: str = Query(...),
    file:    UploadFile = File(...),
):
    """
    Upload a CSV of fixtures and run batch predictions.

    Expected CSV columns (header required):
      home_team, away_team, league, kickoff_time, home_odds, draw_odds, away_odds

    Returns a prediction for each valid row.
    """
    _verify_key(api_key)
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=422, detail="File must be a .csv")

    content  = await file.read()
    text     = content.decode("utf-8", errors="replace")
    reader   = csv.DictReader(io.StringIO(text))

    REQUIRED = {"home_team", "away_team"}
    if reader.fieldnames is None or not REQUIRED.issubset(set(reader.fieldnames)):
        raise HTTPException(
            status_code=422,
            detail=f"CSV must contain columns: {', '.join(REQUIRED)}"
        )

    results = []
    errors  = []

    for i, row in enumerate(reader):
        home = row.get("home_team", "").strip()
        away = row.get("away_team", "").strip()

        if not home or not away:
            errors.append({"row": i + 2, "error": "Missing home_team or away_team"})
            continue

        try:
            home_odds = float(row.get("home_odds", 2.30))
            draw_odds = float(row.get("draw_odds", 3.30))
            away_odds = float(row.get("away_odds", 3.10))
        except ValueError:
            errors.append({"row": i + 2, "error": "Invalid odds values"})
            continue

        league = row.get("league", "premier_league").strip()

        features = {
            "home_team":   home,
            "away_team":   away,
            "league":      league,
            "market_odds": {"home": home_odds, "draw": draw_odds, "away": away_odds},
        }

        try:
            raw  = await orchestrator.predict(features, f"csv_{i}_{home}_{away}")
            pred = raw.get("predictions", {})
            best = MarketUtils.determine_best_bet(
                pred.get("home_prob", 0.33),
                pred.get("draw_prob", 0.33),
                pred.get("away_prob", 0.33),
                home_odds, draw_odds, away_odds,
            )
            results.append({
                "row":        i + 2,
                "home_team":  home,
                "away_team":  away,
                "league":     league,
                "kickoff":    row.get("kickoff_time", ""),
                "home_prob":  round(pred.get("home_prob", 0), 3),
                "draw_prob":  round(pred.get("draw_prob", 0), 3),
                "away_prob":  round(pred.get("away_prob", 0), 3),
                "edge":       round(best.get("edge", 0), 4),
                "stake":      round(best.get("kelly_stake", 0), 4),
                "best_side":  best.get("best_side"),
                "has_edge":   best.get("has_edge", False),
                "home_odds":  home_odds,
                "draw_odds":  draw_odds,
                "away_odds":  away_odds,
            })
        except Exception as e:
            errors.append({"row": i + 2, "error": str(e)})

    return {
        "processed": len(results),
        "errors":    len(errors),
        "results":   results,
        "error_details": errors,
    }


# ======================================================================
# v2.3.0 — ACCUMULATOR ENGINE
# ======================================================================

@router.get("/accumulator/candidates")
async def get_accumulator_candidates(
    api_key:         str   = Query(...),
    min_confidence:  float = Query(default=0.60),
    min_edge:        float = Query(default=0.01),
    count:           int   = Query(default=15, le=30),
):
    """
    Fetch upcoming fixtures and return top candidates for accumulators.
    Each candidate includes edge, confidence, and market type.
    """
    _verify_key(api_key)
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    fixtures = await _fetch_fixtures(count)
    candidates = []

    for fix in fixtures:
        home = fix["home_team"]
        away = fix["away_team"]
        mkt  = fix.get("market_odds", {})

        home_odds = float(mkt.get("home", 2.30))
        draw_odds = float(mkt.get("draw", 3.30))
        away_odds = float(mkt.get("away", 3.10))

        try:
            raw  = await orchestrator.predict({
                "home_team":   home,
                "away_team":   away,
                "league":      fix["league"],
                "market_odds": mkt,
            }, f"acc_{home}_{away}")
            pred = raw.get("predictions", {})
        except Exception as e:
            logger.warning(f"Prediction failed for {home} vs {away}: {e}")
            continue

        home_prob = pred.get("home_prob", 0.33)
        draw_prob = pred.get("draw_prob", 0.33)
        away_prob = pred.get("away_prob", 0.33)
        confidence = pred.get("confidence", {}).get("1x2", 0.60)
        models_used = pred.get("models_used", 0)
        data_source = pred.get("data_source", "market_implied")

        best = MarketUtils.determine_best_bet(
            home_prob, draw_prob, away_prob,
            home_odds, draw_odds, away_odds,
        )

        edge = best.get("edge", 0)
        best_side = best.get("best_side")
        best_odds_val = best.get("odds", 2.0)

        if best_side and confidence >= min_confidence and edge >= min_edge:
            candidates.append({
                "home_team":   home,
                "away_team":   away,
                "league":      fix["league"],
                "kickoff":     fix["kickoff_time"][:16],
                "best_side":   best_side,
                "best_odds":   round(best_odds_val, 2),
                "edge":        round(edge, 4),
                "confidence":  round(confidence, 3),
                "home_prob":   round(home_prob, 3),
                "draw_prob":   round(draw_prob, 3),
                "away_prob":   round(away_prob, 3),
                "models_used": models_used,
                "data_source": data_source,
                "home_odds":   home_odds,
                "draw_odds":   draw_odds,
                "away_odds":   away_odds,
            })

    # Sort by confidence × edge (highest expected value first)
    candidates.sort(key=lambda x: x["confidence"] * x["edge"], reverse=True)

    return {
        "candidates":  candidates[:count],
        "total_found": len(candidates),
        "filters": {"min_confidence": min_confidence, "min_edge": min_edge},
    }


class AccumulatorRequest(BaseModel):
    candidates:     List[dict]
    min_legs:       int   = 2
    max_legs:       int   = 6
    min_combined_edge: float = 0.0
    top_n:          int   = 10


def _correlation_penalty(legs: List[dict]) -> float:
    """
    Apply a correlation penalty for same-league matches.
    Same league in one acca = -1.5% per pair.
    """
    leagues = [leg["league"] for leg in legs]
    same_league_pairs = sum(
        1 for a, b in combinations(range(len(leagues)), 2)
        if leagues[a] == leagues[b]
    )
    return same_league_pairs * 0.015


@router.post("/accumulator/generate")
async def generate_accumulators(body: AccumulatorRequest, api_key: str = Query(...)):
    """
    Generate top-N accumulators from the provided candidates.

    For each combination:
    - Combined probability = product of individual probs
    - Fair combined odds   = 1 / combined_prob
    - Market combined odds = product of best_odds
    - Combined edge        = fair_odds - market_odds (as %)
    - Correlation penalty  = -1.5% per same-league pair
    """
    _verify_key(api_key)

    candidates = body.candidates
    if len(candidates) < body.min_legs:
        raise HTTPException(
            status_code=422,
            detail=f"Need at least {body.min_legs} candidates. Got {len(candidates)}."
        )

    accumulators = []

    for n_legs in range(body.min_legs, min(body.max_legs, len(candidates)) + 1):
        for combo in combinations(candidates, n_legs):
            legs = list(combo)

            # Combined probability
            combined_prob = 1.0
            for leg in legs:
                prob_map = {"home": leg["home_prob"], "draw": leg["draw_prob"], "away": leg["away_prob"]}
                combined_prob *= prob_map.get(leg["best_side"], 0.33)

            if combined_prob <= 0:
                continue

            # Combined market odds (what bookmaker would offer)
            combined_odds = 1.0
            for leg in legs:
                combined_odds *= leg["best_odds"]

            # Fair odds from model
            fair_odds = 1.0 / combined_prob

            # Edge = what we think it's worth vs what book offers
            combined_edge = (combined_prob - (1.0 / combined_odds))

            # Penalty for correlated legs
            penalty = _correlation_penalty(legs)
            adjusted_edge = combined_edge - penalty

            # Average confidence across legs
            avg_confidence = sum(leg["confidence"] for leg in legs) / len(legs)

            # Kelly for the accumulator
            b = combined_odds - 1
            p = combined_prob
            q = 1 - p
            kelly = max(0, (b * p - q) / b) if b > 0 else 0
            kelly = min(kelly, 0.03)  # Cap at 3% for accumulators

            if adjusted_edge >= body.min_combined_edge:
                accumulators.append({
                    "n_legs":          n_legs,
                    "legs":            legs,
                    "combined_prob":   round(combined_prob, 4),
                    "combined_odds":   round(combined_odds, 2),
                    "fair_odds":       round(fair_odds, 2),
                    "combined_edge":   round(combined_edge, 4),
                    "correlation_penalty": round(penalty, 4),
                    "adjusted_edge":   round(adjusted_edge, 4),
                    "avg_confidence":  round(avg_confidence, 3),
                    "kelly_stake":     round(kelly, 4),
                })

    # Sort by adjusted_edge descending
    accumulators.sort(key=lambda x: x["adjusted_edge"], reverse=True)
    top = accumulators[:body.top_n]

    return {
        "accumulators":    top,
        "total_generated": len(accumulators),
        "top_n":           body.top_n,
    }


class SendAccumulatorRequest(BaseModel):
    accumulator: dict
    channel_note: str = ""


@router.post("/accumulator/send")
async def send_accumulator_to_telegram(body: SendAccumulatorRequest, api_key: str = Query(...)):
    """
    Push a single accumulator to Telegram.
    """
    _verify_key(api_key)
    if telegram_alerts is None or not telegram_alerts.enabled:
        raise HTTPException(status_code=503, detail="Telegram alerts not enabled")

    acc = body.accumulator
    legs = acc.get("legs", [])

    legs_text = ""
    for i, leg in enumerate(legs, 1):
        side_labels = {"home": "HOME WIN", "draw": "DRAW", "away": "AWAY WIN"}
        side = side_labels.get(leg["best_side"], leg["best_side"].upper())
        legs_text += (
            f"  {i}. {leg['home_team']} vs {leg['away_team']}\n"
            f"     → {side} @ {leg['best_odds']:.2f} "
            f"(conf: {leg['confidence']:.0%})\n"
        )

    adj_edge = acc.get("adjusted_edge", 0)
    edge_emoji = "🔥🔥🔥" if adj_edge > 0.05 else ("🔥🔥" if adj_edge > 0.03 else "🔥")

    message = f"""<b>🎰 VIT ACCUMULATOR</b>
━━━━━━━━━━━━━━━━━━━━━

<b>🏆 {acc.get('n_legs', len(legs))}-Leg Accumulator</b>

<b>Selections:</b>
{legs_text.strip()}

<b>📊 Combined Odds:</b> {acc.get('combined_odds', 0):.2f}
<b>📈 Edge:</b> {adj_edge:+.2%} {edge_emoji}
<b>🎯 Avg Confidence:</b> {acc.get('avg_confidence', 0):.0%}
<b>💵 Suggested Stake:</b> {acc.get('kelly_stake', 0):.1%} of bankroll

{f'<i>{body.channel_note}</i>' if body.channel_note else ''}
━━━━━━━━━━━━━━━━━━━━━
<i>VIT Sports Intelligence v{VERSION}</i>"""

    from app.services.alerts import AlertPriority
    success = await telegram_alerts.send_message(message.strip(), AlertPriority.BET)
    return {"sent": success, "message_preview": message[:200]}


# ======================================================================
# EXISTING ENDPOINTS — preserved from v2.0
# ======================================================================

async def _fetch_fixtures(count: int) -> list:
    football_key = os.getenv("FOOTBALL_DATA_API_KEY", "")
    odds_key     = os.getenv("ODDS_API_KEY", "") or os.getenv("THE_ODDS_API_KEY", "")
    now          = datetime.now(timezone.utc)
    date_from    = now.strftime("%Y-%m-%d")
    date_to      = (now + timedelta(days=7)).strftime("%Y-%m-%d")
    fixtures     = []

    async with httpx.AsyncClient(timeout=20) as client:
        for league, code in COMPETITIONS.items():
            if len(fixtures) >= count:
                break
            try:
                r = await client.get(
                    f"https://api.football-data.org/v4/competitions/{code}/matches",
                    headers={"X-Auth-Token": football_key},
                    params={"status": "SCHEDULED", "dateFrom": date_from, "dateTo": date_to},
                )
                if r.status_code == 200:
                    for m in r.json().get("matches", []):
                        fixtures.append({
                            "home_team":    m["homeTeam"]["name"],
                            "away_team":    m["awayTeam"]["name"],
                            "league":       league,
                            "kickoff_time": m["utcDate"],
                            "market_odds":  {},
                        })
                        if len(fixtures) >= count:
                            break
                elif r.status_code == 429:
                    logger.warning(f"Football-Data rate limit hit for {league}")
            except Exception as e:
                logger.warning(f"Fixture fetch failed for {league}: {e}")

    ODDS_SPORT_MAP = {
        "premier_league": "soccer_epl",
        "la_liga":        "soccer_spain_la_liga",
        "bundesliga":     "soccer_germany_bundesliga",
        "serie_a":        "soccer_italy_serie_a",
        "ligue_1":        "soccer_france_ligue_one",
    }

    if odds_key and fixtures:
        leagues_needed = list({f["league"] for f in fixtures})
        odds_by_teams: dict = {}

        async with httpx.AsyncClient(timeout=20) as client:
            for league in leagues_needed:
                sport = ODDS_SPORT_MAP.get(league, "soccer_epl")
                try:
                    r = await client.get(
                        f"https://api.the-odds-api.com/v4/sports/{sport}/odds/",
                        params={"apiKey": odds_key, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"},
                    )
                    if r.status_code == 200:
                        for event in r.json():
                            home = event.get("home_team", "")
                            away = event.get("away_team", "")
                            for bk in event.get("bookmakers", []):
                                for mkt in bk.get("markets", []):
                                    if mkt.get("key") == "h2h":
                                        outcomes = {o["name"]: o["price"] for o in mkt.get("outcomes", [])}
                                        ho = outcomes.get(home, 0)
                                        do = outcomes.get("Draw", 0)
                                        ao = outcomes.get(away, 0)
                                        if ho and do and ao:
                                            odds_by_teams[(home.lower(), away.lower())] = {
                                                "home": ho, "draw": do, "away": ao
                                            }
                                        break
                                if (home.lower(), away.lower()) in odds_by_teams:
                                    break
                except Exception as e:
                    logger.warning(f"Odds fetch failed for {league}: {e}")

        def _norm(name):
            for s in [" FC", " AFC", " CF", " SC", " United", " City", " Town"]:
                name = name.replace(s, "")
            return name.strip().lower()

        norm_odds = {(_norm(h), _norm(a)): o for (h, a), o in odds_by_teams.items()}
        for fixture in fixtures:
            h = fixture["home_team"]
            a = fixture["away_team"]
            fixture["market_odds"] = (
                odds_by_teams.get((h.lower(), a.lower())) or
                norm_odds.get((_norm(h), _norm(a))) or {}
            )

    return fixtures[:count]


@router.get("/fixtures")
async def get_fixtures(api_key: str = Query(...), count: int = Query(default=10, le=25)):
    _verify_key(api_key)
    fixtures = await _fetch_fixtures(count)
    return {"fixtures": fixtures, "total": len(fixtures)}


@router.get("/stream-predictions")
async def stream_predictions(
    api_key:     str  = Query(...),
    count:       int  = Query(default=10, le=20),
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

        yield sse({"type": "status", "message": f"Found {len(fixtures)} fixtures. Running ML ensemble..."})

        for idx, fixture in enumerate(fixtures):
            home = fixture["home_team"]
            away = fixture["away_team"]
            mkt  = fixture.get("market_odds", {})

            yield sse({"type": "progress", "current": idx + 1, "total": len(fixtures), "fixture": f"{home} vs {away}"})

            try:
                home_odds = float(mkt.get("home", 2.30))
                draw_odds = float(mkt.get("draw", 3.30))
                away_odds = float(mkt.get("away", 3.10))

                features = {
                    "home_team":   home,
                    "away_team":   away,
                    "league":      fixture["league"],
                    "market_odds": mkt,
                }

                raw   = await orchestrator.predict(features, f"{home}_vs_{away}_{idx}")
                preds = raw.get("predictions", {})

                home_prob = float(preds.get("home_prob", 0.33))
                draw_prob = float(preds.get("draw_prob", 0.33))
                away_prob = float(preds.get("away_prob", 0.33))
                over_25   = float(preds.get("over_2_5_prob", 0.5))
                btts      = float(preds.get("btts_prob", 0.5))
                models_used = preds.get("models_used", 0)
                models_total = preds.get("models_total", 12)
                data_source  = preds.get("data_source", "market_implied")
                confidence   = preds.get("confidence", {}).get("1x2", 0.65)

                best          = MarketUtils.determine_best_bet(home_prob, draw_prob, away_prob, home_odds, draw_odds, away_odds)
                edge          = float(best.get("edge", 0))
                stake         = float(min(best.get("kelly_stake", 0), 0.05))
                best_side     = str(best.get("best_side", "home"))
                consensus_prob = max(home_prob, draw_prob, away_prob)
                bet_odds      = best.get("odds", home_odds)

                kickoff_dt = datetime.fromisoformat(fixture["kickoff_time"].replace("Z", "+00:00")).replace(tzinfo=None)

                match_id = None
                async with AsyncSessionLocal() as db:
                    db_match = Match(
                        home_team=home, away_team=away,
                        league=fixture["league"], kickoff_time=kickoff_dt,
                        opening_odds_home=home_odds,
                        opening_odds_draw=draw_odds,
                        opening_odds_away=away_odds,
                    )
                    db.add(db_match)
                    await db.flush()

                    pred_obj = Prediction(
                        match_id=db_match.id,
                        home_prob=home_prob, draw_prob=draw_prob, away_prob=away_prob,
                        over_25_prob=over_25, btts_prob=btts,
                        consensus_prob=consensus_prob,
                        final_ev=edge, recommended_stake=stake,
                        confidence=confidence,
                        bet_side=best_side, entry_odds=bet_odds,
                        raw_edge=edge, normalized_edge=edge, vig_free_edge=edge,
                    )
                    db.add(pred_obj)
                    await db.commit()
                    match_id = db_match.id

                alert_sent = False
                if telegram_alerts and telegram_alerts.enabled and (force_alert or edge > 0.02):
                    try:
                        alert = BetAlert(
                            match_id=match_id,
                            home_team=home, away_team=away,
                            prediction=best_side,
                            probability=consensus_prob,
                            edge=edge, stake=stake, odds=bet_odds,
                            confidence=confidence,
                            kickoff_time=kickoff_dt,
                            home_prob=home_prob, draw_prob=draw_prob, away_prob=away_prob,
                            home_odds=home_odds, draw_odds=draw_odds, away_odds=away_odds,
                            models_used=models_used, models_total=models_total,
                            data_source=data_source,
                        )
                        alert_sent = await telegram_alerts.send_bet_alert(alert)
                    except Exception as e:
                        logger.warning(f"Telegram alert failed: {e}")

                yield sse({
                    "type": "prediction", "index": idx + 1,
                    "match_id": match_id,
                    "home_team": home, "away_team": away,
                    "league": fixture["league"],
                    "kickoff": fixture["kickoff_time"][:10],
                    "home_prob": round(home_prob, 3),
                    "draw_prob": round(draw_prob, 3),
                    "away_prob": round(away_prob, 3),
                    "over_25": round(over_25, 3), "btts": round(btts, 3),
                    "edge": round(edge, 4), "stake": round(stake, 4),
                    "best_side": best_side, "alert_sent": alert_sent,
                    "models_used": models_used, "models_total": models_total,
                    "data_source": data_source, "confidence": round(confidence, 3),
                })
                await asyncio.sleep(3)

            except Exception as e:
                logger.error(f"Prediction failed: {home} vs {away}: {e}", exc_info=True)
                yield sse({"type": "error", "message": str(e), "fixture": f"{home} vs {away}", "index": idx + 1})

        yield sse({"type": "done", "total": len(fixtures)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
