# services/ml_service/models/model_orchestrator.py
import asyncio
import logging
import time
from typing import Dict, List, Optional
from collections import Counter

# Import all 12 child models
from .model_1_poisson import PoissonGoalModel
from .model_2_xgboost import XGBoostOutcomeClassifier
from .model_3_lstm import LSTMMomentumNetwork
from .model_4_monte_carlo import MonteCarloEngine
from .model_5_ensemble_agg import EnsembleAggregator
from .model_6_transformer import TransformerSequenceModel
from .model_7_gnn import GraphNeuralNetworkModel
from .model_8_bayesian import BayesianHierarchicalModel
from .model_9_rl_agent import RLPolicyAgent
from .model_10_causal import CausalInferenceModel
from .model_11_sentiment import SentimentFusionModel
from .model_12_anomaly import AnomalyRegimeDetectionModel

logger = logging.getLogger(__name__)

class ModelOrchestrator:
    def __init__(self):
        # Explicitly register all 12 models into the VIT infrastructure
        self.models = {
            'poisson': PoissonGoalModel(),
            'xgboost': XGBoostOutcomeClassifier(model_id='xgb_v4'),
            'lstm': LSTMMomentumNetwork(),
            'monte_carlo': MonteCarloEngine(),
            'ensemble': EnsembleAggregator(),
            'transformer': TransformerSequenceModel(),
            'gnn': GraphNeuralNetworkModel(),
            'bayesian': BayesianHierarchicalModel(),
            'rl_agent': RLPolicyAgent(),
            'causal': CausalInferenceModel(),
            'sentiment': SentimentFusionModel(),
            'anomaly': AnomalyRegimeDetectionModel()
        }
        self.latencies = {}
        logger.info(f"Registered {len(self.models)} models in orchestrator")

    def num_models_ready(self) -> int:
        return sum(1 for m in self.models.values() if getattr(m, 'is_trained', False) or getattr(m, 'ready', False))

    async def predict(self, features: Dict, match_id: str) -> Dict:
        # Placeholder logic for the main VIT prediction loop
        return {"match_id": match_id, "status": "ready"}
