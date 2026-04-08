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
    auth_enabled = os.getenv("AUTH_ENABLED", "false").lower() == "true"
    if not auth_enabled:
        return
    expected = get_env("API_KEY", "dev_api_key_12345")
    if api_key != expected:
        raise HTTPException(status_code=403, detail="Invalid admin key")


async def _fetch_fixtures(count: int) -> list:
    """
    Fetch real upcoming fixtures from football-data.org (next 7 days),
    then enrich each one with live market odds from The Odds API.
    """
    from datetime import timezone, timedelta

    football_key = os.getenv("FOOTBALL_DATA_API_KEY", "")
    odds_key = os.getenv("ODDS_API_KEY", "") or os.getenv("THE_ODDS_API_KEY", "")

    now = datetime.now(timezone.utc)
    date_from = now.strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=7)).strftime("%Y-%m-%d")

    fixtures = []

    # ── 1. Fetch scheduled fixtures ───────────────────────────────────
    async with httpx.AsyncClient(timeout=20) as client:
        for league, code in COMPETITIONS.items():
            if len(fixtures) >= count:
                break
            try:
                r = await client.get(
                    f"https://api.football-data.org/v4/competitions/{code}/matches",
                    headers={"X-Auth-Token": football_key},
                    params={
                        "status": "SCHEDULED",
                        "dateFrom": date_from,
                        "dateTo": date_to,
                    },
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
                elif r.status_code == 429:
                    logger.warning(f"Football-Data rate limit hit for {league}")
                else:
                    logger.warning(f"Football-Data {r.status_code} for {league}: {r.text[:200]}")
            except Exception as e:
                logger.warning(f"Fixture fetch failed for {league}: {e}")

    # ── 2. Enrich with live odds ──────────────────────────────────────
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
                        "https://api.the-odds-api.com/v4/sports/{sport}/odds/".format(sport=sport),
                        params={
                            "apiKey": odds_key,
                            "regions": "eu",
                            "markets": "h2h",
                            "oddsFormat": "decimal",
                        },
                    )
                    if r.status_code == 200:
                        for event in r.json():
                            home = event.get("home_team", "")
                            away = event.get("away_team", "")
                            bookmakers = event.get("bookmakers", [])
                            if not bookmakers:
                                continue
                            # Use first available bookmaker's h2h market
                            for bk in bookmakers:
                                for mkt in bk.get("markets", []):
                                    if mkt.get("key") == "h2h":
                                        outcomes = {o["name"]: o["price"] for o in mkt.get("outcomes", [])}
                                        home_odds = outcomes.get(home, 0)
                                        draw_odds = outcomes.get("Draw", 0)
                                        away_odds = outcomes.get(away, 0)
                                        if home_odds and draw_odds and away_odds:
                                            odds_by_teams[(home.lower(), away.lower())] = {
                                                "home": home_odds,
                                                "draw": draw_odds,
                                                "away": away_odds,
                                            }
                                        break
                                if (home.lower(), away.lower()) in odds_by_teams:
                                    break
                    elif r.status_code == 401:
                        logger.warning("Odds API: invalid key")
                        break
                    elif r.status_code == 422:
                        logger.warning(f"Odds API: no odds for {sport}")
                except Exception as e:
                    logger.warning(f"Odds fetch failed for {league}: {e}")

        def _normalise(name: str) -> str:
            """Strip common suffixes so 'West Ham United FC' matches 'West Ham United'."""
            for suffix in [" FC", " AFC", " CF", " SC", " United", " City", " Town"]:
                name = name.replace(suffix, "")
            return name.strip().lower()

        norm_odds: dict = {
            (_normalise(h), _normalise(a)): odds
            for (h, a), odds in odds_by_teams.items()
        }

        for fixture in fixtures:
            h = fixture["home_team"]
            a = fixture["away_team"]
            key_exact = (h.lower(), a.lower())
            key_norm  = (_normalise(h), _normalise(a))
            if key_exact in odds_by_teams:
                fixture["market_odds"] = odds_by_teams[key_exact]
            elif key_norm in norm_odds:
                fixture["market_odds"] = norm_odds[key_norm]

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
