# services/ml_service/models/model_orchestrator.py
# VIT Sports Intelligence Network — v2.1.0
# Fix: Market-implied probability fallback when models fail
# Fix: Accurate model count reporting
# Fix: Proper confidence scoring

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

VERSION = "2.1.0"


class ModelOrchestrator:
    """
    Orchestrates the full 12-model ML ensemble.

    v2.1.0 Changes:
    - Added market-implied probability fallback (eliminates flat 36.3% bug)
    - Added proper confidence scoring
    - Added model count tracking in prediction output
    - Added vig removal before edge calculation
    - Never returns flat 33/33/33 — always uses market odds as floor
    """

    def __init__(self):
        self.models: Dict[str, object] = {}
        self.model_meta: Dict[str, dict] = {}
        self.model_status: Dict[str, dict] = {}
        self.initialized: bool = False
        self._total_model_specs = 12

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load_all_models(self) -> Dict[str, bool]:
        """
        Instantiate all 12 models. Individual failures are caught and
        logged so the rest of the ensemble still runs.
        """
        results: Dict[str, bool] = {}

        model_specs = [
            ("poisson",     "model_1_poisson",     "PoissonGoalModel",
             {"model_name": "poisson_v3"},                        "Poisson"),
            ("xgboost",     "model_2_xgboost",     "XGBoostOutcomeClassifier",
             {"model_id": "xgb_v4"},                              "XGBoost"),
            ("lstm",        "model_3_lstm",         "LSTMMomentumNetworkModel",
             {"model_name": "lstm_v1"},                           "LSTM"),
            ("monte_carlo", "model_4_monte_carlo",  "MonteCarloEngine",
             {"model_name": "monte_carlo_v3"},                    "MonteCarlo"),
            ("ensemble",    "model_5_ensemble_agg", "EnsembleAggregator",
             {"model_name": "ensemble_v2"},                       "Ensemble"),
            ("transformer", "model_6_transformer",  "TransformerSequenceModel",
             {"model_name": "transformer_v2"},                    "Transformer"),
            ("gnn",         "model_7_gnn",          "GNNModel",
             {"model_name": "gnn_v2"},                            "GNN"),
            ("bayesian",    "model_8_bayesian",      "BayesianHierarchicalModel",
             {"model_name": "bayesian_v2"},                       "Bayesian"),
            ("rl_agent",    "model_9_rl_agent",      "RLPolicyAgent",
             {"model_name": "rl_agent_v2"},                       "RLAgent"),
            ("causal",      "model_10_causal",       "CausalInferenceModel",
             {"model_name": "causal_v2"},                         "Causal"),
            ("sentiment",   "model_11_sentiment",    "SentimentFusionModel",
             {"model_name": "sentiment_v2"},                      "Sentiment"),
            ("anomaly",     "model_12_anomaly",      "AnomalyRegimeDetectionModel",
             {"model_name": "anomaly_v2"},                        "Anomaly"),
        ]

        for key, module_suffix, class_name, kwargs, model_type in model_specs:
            try:
                mod = __import__(
                    f"services.ml_service.models.{module_suffix}",
                    fromlist=[class_name],
                )
                cls = getattr(mod, class_name)
                instance = cls(**kwargs)
                if key == "sentiment":
                    instance.openai_api_key = os.getenv("OPENAI_API_KEY", "")
                    if instance.openai_api_key:
                        logger.info("✅ sentiment: OpenAI GPT-4o-mini enhancement enabled")
                self.models[key] = instance
                weight = getattr(instance, "weight", 1.0)
                self.model_meta[key] = {
                    "model_name": kwargs.get("model_name", kwargs.get("model_id", key)),
                    "model_type": model_type,
                    "weight": float(weight),
                }
                self.model_status[key] = {"status": "ready", "error": None, "load_time_ms": 0}
                logger.info(f"✅ {key} ({model_type}): ready")
                results[key] = True
            except Exception as exc:
                logger.warning(f"⚠️  {key} ({model_type}): skipped — {exc}")
                self.model_status[key] = {"status": "failed", "error": str(exc)}
                results[key] = False

        ready = sum(results.values())
        self.initialized = True  # Always initialize — market fallback handles 0-model case
        logger.info(f"Orchestrator v{VERSION}: {ready}/{len(model_specs)} models ready")
        return results

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def num_models_ready(self) -> int:
        return sum(1 for s in self.model_status.values() if s["status"] == "ready")

    def get_model_status(self) -> Dict:
        """Return detailed model status for admin panel"""
        models_list = []
        ready = 0
        for name, info in self.model_status.items():
            if info["status"] == "ready":
                ready += 1
            meta = self.model_meta.get(name, {})
            models_list.append({
                "name": name,
                "model_type": meta.get("model_type", "unknown"),
                "status": info["status"],
                "weight": meta.get("weight", 1.0),
                "error": info.get("error"),
            })
        return {
            "ready": ready,
            "total": self._total_model_specs,
            "models": models_list,
            "version": VERSION,
        }

    # ------------------------------------------------------------------
    # Market-implied probability engine (v2.1.0 — eliminates 36.3% bug)
    # ------------------------------------------------------------------
    @staticmethod
    def _validate_odds(odds) -> float:
        """Ensure odds are within a sane range"""
        try:
            o = float(odds)
            return o if 1.01 <= o <= 100.0 else 2.0
        except (TypeError, ValueError):
            return 2.0

    @staticmethod
    def _market_implied_probs(home_odds: float, draw_odds: float, away_odds: float) -> Tuple[float, float, float]:
        """
        Remove bookmaker vig and return true market probabilities.
        This is the v2.1.0 fallback — always returns match-specific values,
        never flat 33/33/33.
        """
        h = 1.0 / home_odds
        d = 1.0 / draw_odds
        a = 1.0 / away_odds
        total = h + d + a
        if total <= 0:
            total = 1.0
        return h / total, d / total, a / total

    @staticmethod
    def _kelly_stake(model_prob: float, odds: float, max_stake: float = 0.05) -> float:
        """Fractional Kelly criterion, capped at max_stake"""
        b = odds - 1.0
        if b <= 0:
            return 0.0
        q = 1.0 - model_prob
        kelly = (b * model_prob - q) / b
        return float(min(max(kelly, 0.0), max_stake))

    @staticmethod
    def _confidence_from_agreement(model_probs: List[Dict], agg_home: float, agg_draw: float, agg_away: float) -> float:
        """
        Confidence = how tightly the active models agree with the ensemble.
        Returns 0.50–0.95 range.
        """
        if not model_probs:
            return 0.65  # Market-only baseline (better than 0.50)

        dominant = max(agg_home, agg_draw, agg_away)
        agreement_count = 0
        for m in model_probs:
            m_dominant = max(m.get("home_prob", 0), m.get("draw_prob", 0), m.get("away_prob", 0))
            if m_dominant == m.get("home_prob", 0) and agg_home == dominant:
                agreement_count += 1
            elif m_dominant == m.get("draw_prob", 0) and agg_draw == dominant:
                agreement_count += 1
            elif m_dominant == m.get("away_prob", 0) and agg_away == dominant:
                agreement_count += 1

        n = len(model_probs)
        agreement_rate = agreement_count / n if n > 0 else 0.5
        # Scale: 50% agreement → 0.60 confidence, 100% → 0.90
        return round(0.55 + (agreement_rate * 0.35), 3)

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------
    async def predict(self, features: Dict, match_id: str = "") -> Dict:
        """
        v2.1.0: Always returns meaningful predictions.

        Priority:
        1. Full ensemble (≥6 models active) blended 70/30 with market
        2. Partial ensemble (1–5 models) blended 50/50 with market
        3. Market-implied only (0 models) — still match-specific, never flat

        No more 36.3% for every match.
        """
        # --- Extract and validate market odds ---
        market_odds = features.get("market_odds", {})
        home_odds = self._validate_odds(market_odds.get("home", 2.30))
        draw_odds = self._validate_odds(market_odds.get("draw", 3.30))
        away_odds = self._validate_odds(market_odds.get("away", 3.10))

        # Market-implied vig-free probabilities (always match-specific)
        mkt_home, mkt_draw, mkt_away = self._market_implied_probs(home_odds, draw_odds, away_odds)

        # --- Run ensemble ---
        individual_results: List[dict] = []
        weighted_home = weighted_draw = weighted_away = 0.0
        weighted_over25 = weighted_btts = 0.0
        total_weight = 0.0
        successful_model_probs: List[Dict] = []

        for key, model in self.models.items():
            meta = self.model_meta.get(key, {})
            model_name = meta.get("model_name", key)
            model_type = meta.get("model_type", key)
            weight = meta.get("weight", 1.0)
            t0 = time.monotonic()

            try:
                result = model.predict(features)
                raw = await result if asyncio.iscoroutine(result) else result
                latency_ms = round((time.monotonic() - t0) * 1000, 1)

                home = float(raw.get("home_prob", raw.get("home", 0.0)))
                draw = float(raw.get("draw_prob", raw.get("draw", 0.0)))
                away = float(raw.get("away_prob", raw.get("away", 0.0)))

                s = home + draw + away
                if s > 0:
                    home /= s; draw /= s; away /= s
                else:
                    home = draw = away = 1 / 3

                over25 = float(raw.get("over_2_5_prob", 0.5))
                btts   = float(raw.get("btts_prob", 0.5))

                weighted_home  += home  * weight
                weighted_draw  += draw  * weight
                weighted_away  += away  * weight
                weighted_over25 += over25 * weight
                weighted_btts   += btts   * weight
                total_weight   += weight

                successful_model_probs.append({
                    "home_prob": home, "draw_prob": draw, "away_prob": away
                })

                individual_results.append({
                    "model_name":            model_name,
                    "model_type":            model_type,
                    "model_weight":          weight,
                    "supported_markets":     getattr(model, "supported_markets", []),
                    "home_prob":             round(home, 4),
                    "draw_prob":             round(draw, 4),
                    "away_prob":             round(away, 4),
                    "over_2_5_prob":         round(over25, 4),
                    "btts_prob":             round(btts, 4),
                    "home_goals_expectation": raw.get("home_goals_expectation"),
                    "away_goals_expectation": raw.get("away_goals_expectation"),
                    "dixon_coles_rho":        raw.get("dixon_coles_rho"),
                    "confidence":            raw.get("confidence", {}),
                    "latency_ms":            latency_ms,
                    "failed":                False,
                    "error":                 None,
                })

            except Exception as exc:
                latency_ms = round((time.monotonic() - t0) * 1000, 1)
                logger.warning(f"⚠️  {key} predict failed: {exc}")
                individual_results.append({
                    "model_name": model_name, "model_type": model_type,
                    "model_weight": weight, "supported_markets": [],
                    "failed": True, "error": str(exc), "latency_ms": latency_ms,
                })

        # --- Blend ensemble + market (v2.1.0 core fix) ---
        models_ready = self.num_models_ready()
        models_succeeded = len(successful_model_probs)

        if total_weight > 0 and models_succeeded >= 6:
            # Full ensemble: 70% model / 30% market
            ens_home = weighted_home / total_weight
            ens_draw = weighted_draw / total_weight
            ens_away = weighted_away / total_weight
            blend = 0.70
            data_source = "ensemble"
            logger.info(f"Full ensemble blend ({models_succeeded} models)")

        elif total_weight > 0 and models_succeeded >= 1:
            # Partial ensemble: 50% model / 50% market
            ens_home = weighted_home / total_weight
            ens_draw = weighted_draw / total_weight
            ens_away = weighted_away / total_weight
            blend = 0.50
            data_source = "partial_ensemble"
            logger.info(f"Partial ensemble blend ({models_succeeded} models)")

        else:
            # Market-implied only — still match-specific
            ens_home = ens_draw = ens_away = 0.0
            blend = 0.0
            data_source = "market_implied"
            logger.info("Market-implied fallback (no models available)")

        agg_home = blend * ens_home + (1 - blend) * mkt_home
        agg_draw = blend * ens_draw + (1 - blend) * mkt_draw
        agg_away = blend * ens_away + (1 - blend) * mkt_away

        # Renormalize
        s = agg_home + agg_draw + agg_away
        if s > 0:
            agg_home /= s; agg_draw /= s; agg_away /= s

        # Aggregated secondary markets
        agg_over25 = (weighted_over25 / total_weight) if total_weight > 0 else 0.5
        agg_btts   = (weighted_btts   / total_weight) if total_weight > 0 else 0.5

        # --- Edge & stake calculation ---
        edges = {
            "home": agg_home - mkt_home,
            "draw": agg_draw - mkt_draw,
            "away": agg_away - mkt_away,
        }
        best_outcome = max(edges, key=edges.get)
        best_edge    = edges[best_outcome]
        best_prob    = {"home": agg_home, "draw": agg_draw, "away": agg_away}[best_outcome]
        best_odds_val = {"home": home_odds, "draw": draw_odds, "away": away_odds}[best_outcome]

        if best_edge > 0.02:
            stake = self._kelly_stake(best_prob, best_odds_val)
            recommendation = best_outcome.upper()
        else:
            stake = 0.0
            recommendation = None

        # --- Confidence ---
        confidence = self._confidence_from_agreement(
            successful_model_probs, agg_home, agg_draw, agg_away
        )

        predictions = {
            "home_prob":          round(agg_home, 4),
            "draw_prob":          round(agg_draw, 4),
            "away_prob":          round(agg_away, 4),
            "over_2_5_prob":      round(agg_over25, 4),
            "under_2_5_prob":     round(1 - agg_over25, 4),
            "btts_prob":          round(agg_btts, 4),
            "no_btts_prob":       round(1 - agg_btts, 4),
            "confidence":         {"1x2": confidence, "over_under": 0.5, "btts": 0.5},
            "edge":               round(best_edge, 4),
            "best_outcome":       recommendation,
            "best_odds":          best_odds_val,
            "recommended_stake":  round(stake, 4),
            "data_source":        data_source,
            "models_used":        models_succeeded,
            "models_ready":       models_ready,
            "models_total":       self._total_model_specs,
            "market_home_prob":   round(mkt_home, 4),
            "market_draw_prob":   round(mkt_draw, 4),
            "market_away_prob":   round(mkt_away, 4),
            "vig_percentage":     round(
                ((1/home_odds + 1/draw_odds + 1/away_odds) - 1) * 100, 2
            ),
        }

        return {
            "match_id":           match_id,
            "status":             "success",
            "predictions":        predictions,
            "individual_results": individual_results,
            "models_used":        [r["model_name"] for r in individual_results if not r.get("failed")],
            "models_count":       models_succeeded,
            "version":            VERSION,
        }
