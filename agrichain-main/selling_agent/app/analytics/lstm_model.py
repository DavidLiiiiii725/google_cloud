"""
Item Anomaly Detection & Dynamic Pricing — Mock LSTM (2-layer, hidden=64)

Real production path:
    import torch, torch.nn as nn
    class LSTMModel(nn.Module):
        def __init__(self):
            super().__init__()
            self.lstm = nn.LSTM(input_size=1, hidden_size=64,
                                num_layers=2, batch_first=True, dropout=0.2)
            self.fc   = nn.Linear(64, 1)
        def forward(self, x):               # x: (batch, seq_len, 1)
            out, _ = self.lstm(x)
            return self.fc(out[:, -1, :])   # last hidden state → forecast

Here we simulate the LSTM forward pass with a gated weighted moving average
that honours forget/input/output/cell gate semantics.
"""
import random
import math


class LSTMTimeSeriesAnalyzer:
    """
    Monitors per-item 14-day sales time series.
    Detects anomalies and triggers Dynamic Pricing or Halt-Sales.
    """

    SEQ_LEN    = 14   # days of look-back
    HIDDEN_DIM = 64
    NUM_LAYERS = 2

    ITEMS = [
        {"id": "strawberries", "name": "Strawberries", "emoji": "🍓", "base_price": 4.99, "unit": "pint"},
        {"id": "tomatoes",     "name": "Tomatoes",     "emoji": "🍅", "base_price": 2.49, "unit": "lb"},
        {"id": "spinach",      "name": "Spinach",      "emoji": "🥬", "base_price": 3.29, "unit": "bag"},
        {"id": "blueberries",  "name": "Blueberries",  "emoji": "🫐", "base_price": 5.99, "unit": "pint"},
        {"id": "carrots",      "name": "Carrots",      "emoji": "🥕", "base_price": 1.99, "unit": "lb"},
        {"id": "zucchini",     "name": "Zucchini",     "emoji": "🥒", "base_price": 1.49, "unit": "each"},
    ]

    ALTERNATIVES = {
        "Strawberries": "Blueberries",
        "Tomatoes":     "Bell Peppers",
        "Spinach":      "Kale",
        "Blueberries":  "Raspberries",
        "Carrots":      "Sweet Potatoes",
        "Zucchini":     "Yellow Squash",
    }

    # Mock trained gate weights (sigmoid outputs, ∈ (0,1))
    GATES = {"forget": 0.73, "input": 0.81, "output": 0.69, "cell": 0.77}

    def _generate_history(self, anomaly_type: str) -> list[int]:
        """Produce a 14-day mock sales series matching the anomaly type."""
        base = random.randint(38, 72)
        trend = [max(0, base + int(random.gauss(0, 6))) for _ in range(12)]

        if anomaly_type == "severe":
            tail = [max(0, base - random.randint(35, 55)),
                    max(0, base - random.randint(50, 70))]
        elif anomaly_type == "flash":
            tail = [max(0, base - random.randint(18, 30)),
                    max(0, base - random.randint(22, 38))]
        else:
            tail = [max(0, base + int(random.gauss(2, 5))) for _ in range(2)]

        return trend + tail

    def _lstm_forward(self, seq: list[int]) -> float:
        """
        Mock LSTM cell computation.

        Real cell update:
            f_t = σ(W_f · [h_{t-1}, x_t] + b_f)   # forget gate
            i_t = σ(W_i · [h_{t-1}, x_t] + b_i)   # input gate
            g_t = tanh(W_g · [h_{t-1}, x_t] + b_g) # candidate cell
            o_t = σ(W_o · [h_{t-1}, x_t] + b_o)   # output gate
            c_t = f_t ⊙ c_{t-1} + i_t ⊙ g_t
            h_t = o_t ⊙ tanh(c_t)

        Here: exponentially-weighted avg (≈ learned forget gate decay).
        """
        alpha = self.GATES["forget"]           # decay ~ forget gate
        h = float(seq[0])
        for x in seq[1:]:
            g = math.tanh(x / max(max(seq), 1))  # mock candidate cell
            h = alpha * h + (1 - alpha) * self.GATES["input"] * x * g
        return round(self.GATES["output"] * math.tanh(h), 1)

    def analyze(self) -> list[dict]:
        results = []
        for item in self.ITEMS:
            # Assign anomaly class (weighted random for demo variety)
            roll = random.random()
            if roll > 0.82:
                atype = "severe"
            elif roll > 0.55:
                atype = "flash"
            else:
                atype = "normal"

            history  = self._generate_history(atype)
            forecast = self._lstm_forward(history)
            decay    = round(random.uniform(0.08, 0.22), 3) if atype != "normal" \
                       else round(random.uniform(0.01, 0.05), 3)
            anomaly_score = round(
                0.88 + random.uniform(0, 0.10) if atype == "severe" else
                0.65 + random.uniform(0, 0.18) if atype == "flash"  else
                random.uniform(0.05, 0.40), 3
            )

            # ── Dynamic Pricing logic ──────────────────────────────
            if atype == "severe":
                status      = "HALT_SALES"
                current_price = None
                badge       = "🚫 Halt Sales"
                price_change = None
                alt         = self.ALTERNATIVES.get(item["name"], "seasonal alternatives")
                tip         = f"Suggest replacing with {alt}"

            elif atype == "flash":
                discount    = random.choice([0.15, 0.20, 0.25])
                current_price = round(item["base_price"] * (1 - discount), 2)
                status      = "FLASH_SALE"
                badge       = f"⚡ Flash Sale −{int(discount*100)}%"
                tip         = "Move inventory fast — demand dropping"
                price_change = -discount

            else:
                current_price = item["base_price"]
                status      = "NORMAL"
                badge       = "✅ Steady"
                tip         = "Maintain pricing — demand stable"
                price_change = 0.0

            results.append({
                **item,
                "current_price": current_price,
                "price_change":  price_change,
                "status":        status,
                "badge":         badge,
                "tip":           tip,
                "decay_rate":    decay,
                "anomaly_score": anomaly_score,
                "sales_history": history,
                "forecast_tomorrow": forecast,
                "lstm_gates":    self.GATES,
            })
        return results
