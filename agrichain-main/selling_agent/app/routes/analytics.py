from fastapi import APIRouter, Body
from app.analytics.rl_model import QLearningRestockAgent
from app.analytics.lstm_model import LSTMTimeSeriesAnalyzer
from app.analytics.gnn_model import GNNNeighborhoodAnalyzer
import random

router = APIRouter()

# Module-level singletons (persist across requests within one server process)
_rl    = QLearningRestockAgent()
_lstm  = LSTMTimeSeriesAnalyzer()
_gnn   = GNNNeighborhoodAnalyzer()


@router.get("/restock")
async def get_restock_prediction():
    """Run RL inference and return tomorrow's recommended restock quantity."""
    state = {
        "inventory_change": random.randint(-40, 40),
        "sales_velocity":   random.randint(20, 90),
        "price_delta":      round(random.uniform(-0.5, 0.5), 3),
        "weather_score":    round(random.uniform(0.0, 1.0), 3),
    }
    prediction = _rl.predict(state)
    return {"state": state, "prediction": prediction}


@router.post("/model-update")
async def trigger_model_update(
    actual_sales: int = Body(default=85, embed=True)
):
    """
    Active-learning update: compare yesterday's prediction vs actual sales,
    compute reward, and perform one Q-table gradient step.
    """
    prev = _rl.last_prediction or 100
    state_key = "normal_inv_normal_sales_cloudy"   # simplified for mock
    result = _rl.update(actual_sales, prev, state_key)
    return result


@router.get("/items")
async def get_item_analysis():
    """LSTM anomaly detection + dynamic pricing for all tracked items."""
    items = _lstm.analyze()
    return {"items": items}


@router.get("/gnn")
async def get_gnn_recommendations():
    """GNN message-passing over local vendor graph → neighbourhood insights."""
    return _gnn.analyze()
