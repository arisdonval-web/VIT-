# services/ml_service/models/model_orchestrator.py
import asyncio
import logging
import os
import time
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class ModelOrchestrator:
    """
    Orchestrates the full 12-model ML ensemble.

    Responsibilities:
    - Load each model individually (failures are isolated, not fatal)
    - Run async predictions across all ready models
    - Aggregate probabilities via weighted average
    - Return structured output compatible with predict.py and admin.py
    """

    def __init__(self):
        self.models: Dict[str, object] = {}
        self.model_meta: Dict[str, dict] = {}   # {key: {model_name, model_type, weight}}
        self.model_status: Dict[str, dict] = {}
        self.initialized: bool = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load_all_models(self) -> Dict[str, bool]:
        """
        Instantiate all 12 models. Individual failures are caught and
        logged so the rest of the ensemble still runs.
        """
        results: Dict[str, bool] = {}

        # Each entry: (key, import_path, class_name, constructor_kwargs, model_type)
        model_specs = [
            ("poisson",      "model_1_poisson",      "PoissonGoalModel",
             {"model_name": "poisson_v3"},                          "Poisson"),
            ("xgboost",      "model_2_xgboost",      "XGBoostOutcomeClassifier",
             {"model_id": "xgb_v4"},                               "XGBoost"),
            ("lstm",         "model_3_lstm",          "LSTMMomentumNetworkModel",
             {"model_name": "lstm_v1"},                             "LSTM"),
            ("monte_carlo",  "model_4_monte_carlo",   "MonteCarloEngine",
             {"model_name": "monte_carlo_v3"},                     "MonteCarlo"),
            ("ensemble",     "model_5_ensemble_agg",  "EnsembleAggregator",
             {"model_name": "ensemble_v2"},                        "Ensemble"),
            ("transformer",  "model_6_transformer",   "TransformerSequenceModel",
             {"model_name": "transformer_v2"},                     "Transformer"),
            ("gnn",          "model_7_gnn",           "GNNModel",
             {"model_name": "gnn_v2"},                             "GNN"),
            ("bayesian",     "model_8_bayesian",      "BayesianHierarchicalModel",
             {"model_name": "bayesian_v2"},                        "Bayesian"),
            ("rl_agent",     "model_9_rl_agent",      "RLPolicyAgent",
             {"model_name": "rl_agent_v2"},                        "RLAgent"),
            ("causal",       "model_10_causal",       "CausalInferenceModel",
             {"model_name": "causal_v2"},                          "Causal"),
            ("sentiment",    "model_11_sentiment",    "SentimentFusionModel",
             {"model_name": "sentiment_v2"},                       "Sentiment"),
            ("anomaly",      "model_12_anomaly",      "AnomalyRegimeDetectionModel",
             {"model_name": "anomaly_v2"},                         "Anomaly"),
        ]

        for key, module_suffix, class_name, kwargs, model_type in model_specs:
            try:
                mod = __import__(
                    f"services.ml_service.models.{module_suffix}",
                    fromlist=[class_name],
                )
                cls = getattr(mod, class_name)
                instance = cls(**kwargs)
                # Wire OpenAI key into the sentiment model
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
                self.model_status[key] = {"status": "ready", "error": None}
                logger.info(f"✅ {key} ({model_type}): ready")
                results[key] = True
            except Exception as exc:
                logger.warning(f"⚠️  {key} ({model_type}): skipped — {exc}")
                self.model_status[key] = {"status": "failed", "error": str(exc)}
                results[key] = False

        ready = sum(results.values())
        self.initialized = ready > 0
        logger.info(f"Orchestrator: {ready}/{len(model_specs)} models ready")
        return results

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    def num_models_ready(self) -> int:
        return sum(1 for s in self.model_status.values() if s["status"] == "ready")

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------
    async def predict(self, features: Dict, match_id: str) -> Dict:
        if not self.initialized:
            return {"error": "Orchestrator not initialized", "status": "failed"}

        individual_results: List[dict] = []
        weighted_home = weighted_draw = weighted_away = 0.0
        weighted_over25 = weighted_btts = 0.0
        total_weight = 0.0

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

                # Normalise individual model output so probs sum to 1
                total = home + draw + away
                if total > 0:
                    home /= total
                    draw /= total
                    away /= total
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
                    "model_name":  model_name,
                    "model_type":  model_type,
                    "model_weight": weight,
                    "supported_markets": [],
                    "failed": True,
                    "error":  str(exc),
                    "latency_ms": latency_ms,
                })

        if total_weight == 0:
            return {
                "error": "All models failed",
                "status": "failed",
                "individual_results": individual_results,
            }

        # Final aggregated probabilities
        agg_home  = weighted_home  / total_weight
        agg_draw  = weighted_draw  / total_weight
        agg_away  = weighted_away  / total_weight
        agg_over25 = weighted_over25 / total_weight
        agg_btts   = weighted_btts   / total_weight

        # Re-normalise 1X2 to be exactly 1
        s = agg_home + agg_draw + agg_away
        if s > 0:
            agg_home /= s
            agg_draw /= s
            agg_away /= s

        predictions = {
            "home_prob":     round(agg_home,  4),
            "draw_prob":     round(agg_draw,  4),
            "away_prob":     round(agg_away,  4),
            "over_2_5_prob": round(agg_over25, 4),
            "btts_prob":     round(agg_btts,   4),
        }

        return {
            "match_id":         match_id,
            "status":           "success",
            "predictions":      predictions,
            "individual_results": individual_results,
            "models_used":      [r["model_name"] for r in individual_results if not r.get("failed")],
            "models_count":     len([r for r in individual_results if not r.get("failed")]),
        }
