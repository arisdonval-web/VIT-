# services/ml_service/models/model_2_xgboost.py
import numpy as np
import pickle
import logging
try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    xgb = None
from typing import Dict, List, Optional, Any, Tuple
from sklearn.model_selection import train_test_split

class XGBoostOutcomeClassifier:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self.model = None
        self.is_trained = False
        self.params = {
            'objective': 'multi:softprob',
            'num_class': 3,
            'eval_metric': 'mlogloss',
            'max_depth': 6,
            'learning_rate': 0.1,
            'n_estimators': 100,
            'reg_alpha': 1e-5,
            'reg_lambda': 1.0
        }

    def _prepare_data(self, matches: List[Dict]):
        X, y = [], []
        outcome_map = {'home': 0, 'draw': 1, 'away': 2}
        for m in matches:
            try:
                features = [
                    float(m.get('home_xg', 1.0)),
                    float(m.get('away_xg', 1.0)),
                    float(m.get('home_shots', 10)),
                    float(m.get('away_shots', 10))
                ]
                X.append(features)
                y.append(outcome_map.get(m.get('outcome', 'draw'), 1))
            except:
                continue
        return np.array(X), np.array(y)

    def train(self, matches: List[Dict]):
        if not matches: return
        X, y = self._prepare_data(matches)
        if len(X) < 10: return

        X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)

        self.model = xgb.XGBClassifier(**self.params)
        self.model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=False
        )
        self.is_trained = True

    def predict(self, match: Dict) -> Dict:
        if not self.is_trained:
            return {
                'home_prob': 0.45,
                'draw_prob': 0.27,
                'away_prob': 0.28,
                'supported_markets': ['match_odds', 'over_under', 'btts']
            }
        X, _ = self._prepare_data([match])
        probs = self.model.predict_proba(X)[0]
        return {
            'home_prob': float(probs[0]),
            'draw_prob': float(probs[1]),
            'away_prob': float(probs[2]),
            'supported_markets': ['match_odds', 'over_under', 'btts']
        }

    def save(self, path: str):
        with open(path, 'wb') as f:
            pickle.dump(self, f)

    @classmethod
    def load(cls, path: str):
        with open(path, 'rb') as f:
            return pickle.load(f)
