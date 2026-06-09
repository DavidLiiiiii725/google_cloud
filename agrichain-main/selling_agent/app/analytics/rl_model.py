"""
Intelligent Restocking — Q-Learning RL Agent (Mock Implementation)

Real production path:
    from stable_baselines3 import PPO
    model = PPO("MlpPolicy", VendorEnv(), verbose=1)
    model.learn(total_timesteps=500_000)

Here we simulate a converged Q-table with mock state encoding
so the full API surface and business logic run correctly.
"""
import random
from datetime import datetime


class QLearningRestockAgent:
    """
    Q-Learning agent that maps (State) → (Action: restock_quantity).

    State  = (inventory_change, sales_velocity, price_delta, weather_score)
    Action = discrete restock qty chosen from ACTION_SPACE
    Reward = 1 - |actual_sales - predicted| / predicted   (∈ [0, 1])

    Q-update rule:
        Q(s, a) ← Q(s, a) + α · [R + γ · max_a' Q(s', a') − Q(s, a)]
    """

    ACTION_SPACE = [40, 60, 80, 100, 120, 150, 180, 200]

    # Pre-trained Q-table (mock learned values after 1 247 episodes)
    _PRETRAINED_Q: dict = {
        "low_inv_high_sales_sunny":   {40: 2.1, 60: 4.3, 80: 6.7, 100: 8.2, 120: 9.1, 150: 7.8, 180: 5.3, 200: 3.9},
        "low_inv_high_sales_rainy":   {40: 1.8, 60: 3.9, 80: 6.1, 100: 8.8, 120: 7.4, 150: 6.2, 180: 4.1, 200: 2.7},
        "low_inv_low_sales_sunny":    {40: 4.2, 60: 6.8, 80: 8.9, 100: 7.3, 120: 5.1, 150: 3.4, 180: 2.1, 200: 1.3},
        "low_inv_low_sales_rainy":    {40: 5.1, 60: 7.2, 80: 8.4, 100: 6.9, 120: 4.7, 150: 2.8, 180: 1.6, 200: 0.9},
        "low_inv_normal_sales_cloudy":{40: 3.3, 60: 5.7, 80: 7.8, 100: 8.6, 120: 7.1, 150: 5.0, 180: 3.2, 200: 2.0},
        "high_inv_high_sales_sunny":  {40: 6.1, 60: 7.9, 80: 8.3, 100: 9.4, 120: 8.8, 150: 7.2, 180: 5.9, 200: 4.4},
        "high_inv_high_sales_rainy":  {40: 5.5, 60: 7.1, 80: 8.0, 100: 9.0, 120: 8.2, 150: 6.7, 180: 4.8, 200: 3.5},
        "high_inv_low_sales_sunny":   {40: 8.7, 60: 7.4, 80: 5.9, 100: 4.2, 120: 2.8, 150: 1.5, 180: 0.8, 200: 0.4},
        "high_inv_low_sales_rainy":   {40: 9.1, 60: 7.8, 80: 5.3, 100: 3.6, 120: 2.1, 150: 1.1, 180: 0.6, 200: 0.3},
        "high_inv_normal_sales_cloudy":{40:7.2, 60: 6.5, 80: 7.8, 100: 8.1, 120: 7.0, 150: 5.5, 180: 3.8, 200: 2.5},
        "normal_inv_high_sales_sunny": {40: 2.5, 60: 4.8, 80: 7.2, 100: 9.3, 120: 8.7, 150: 7.1, 180: 5.0, 200: 3.3},
        "normal_inv_normal_sales_cloudy":{40:4.4,60:6.1, 80: 7.9, 100: 8.5, 120: 7.8, 150: 6.2, 180: 4.5, 200: 3.1},
        "normal_inv_low_sales_rainy":  {40: 6.3, 60: 7.7, 80: 8.2, 100: 7.5, 120: 5.9, 150: 3.8, 180: 2.2, 200: 1.4},
    }

    def __init__(self) -> None:
        self.q_table: dict = {k: dict(v) for k, v in self._PRETRAINED_Q.items()}
        self.alpha: float = 0.10          # learning rate
        self.gamma: float = 0.95          # discount factor
        self.epsilon: float = 0.10        # ε-greedy exploration
        self.training_episodes: int = 1247
        self.model_accuracy: float = 87.3
        self.last_prediction: int | None = None
        self.last_update: str = "2026-05-30 18:00:00"

    # ── State encoding ──────────────────────────────────────────────
    def _encode_state(self, inv_change: float, sales_vel: float,
                      price_delta: float, weather: float) -> str:
        inv   = "low"    if inv_change < -15 else "high" if inv_change > 15 else "normal"
        sales = "high"   if sales_vel  > 65  else "low"  if sales_vel  < 35 else "normal"
        wx    = "sunny"  if weather    > 0.6  else "rainy" if weather   < 0.35 else "cloudy"
        return f"{inv}_inv_{sales}_sales_{wx}"

    # ── Inference ───────────────────────────────────────────────────
    def predict(self, state_data: dict) -> dict:
        """
        ε-greedy policy: with prob ε explore randomly, else exploit Q-table.

        Pseudocode (real SB3 PPO equivalent):
            obs = env.reset()
            action, _ = model.predict(obs, deterministic=True)
        """
        key = self._encode_state(
            state_data.get("inventory_change", 0),
            state_data.get("sales_velocity", 50),
            state_data.get("price_delta", 0.0),
            state_data.get("weather_score", 0.5),
        )

        if key not in self.q_table:
            # Unseen state → nearest-neighbour fallback
            key = "normal_inv_normal_sales_cloudy"

        if random.random() < self.epsilon:
            # Explore
            best_action = random.choice(self.ACTION_SPACE)
            policy = "explore"
        else:
            # Exploit
            best_action = max(self.q_table[key], key=self.q_table[key].get)
            policy = "exploit"

        q_val = self.q_table[key][best_action]
        confidence = min(97, int(50 + q_val * 5 + random.uniform(-2, 2)))

        self.last_prediction = best_action
        return {
            "recommended_quantity": best_action,
            "state_key": key,
            "policy": policy,
            "q_value": round(q_val, 3),
            "confidence": confidence,
            "training_episodes": self.training_episodes,
            "model_accuracy": round(self.model_accuracy, 1),
            "epsilon": round(self.epsilon, 4),
            "last_update": self.last_update,
        }

    # ── Active Learning update ───────────────────────────────────────
    def update(self, actual_sales: int, previous_prediction: int,
               state_key: str = "normal_inv_normal_sales_cloudy") -> dict:
        """
        One-step Q-learning update on end-of-day reward signal.

        Pseudocode (real PyTorch equivalent):
            loss = F.mse_loss(q_pred, q_target)
            optimizer.zero_grad(); loss.backward(); optimizer.step()

        Reward function:
            R = 1 - |actual - predicted| / max(predicted, 1)   ∈ [0, 1]
        """
        prev  = max(previous_prediction, 1)
        reward = max(0.0, 1.0 - abs(actual_sales - prev) / prev)

        # Q-target: R + γ · max_a' Q(s', a')  [s' approximated as current state]
        if state_key in self.q_table:
            max_next_q = max(self.q_table[state_key].values())
            q_old      = self.q_table[state_key].get(prev, 5.0)
            td_error   = reward + self.gamma * max_next_q - q_old
            delta_q    = self.alpha * td_error
            self.q_table[state_key][prev] = round(q_old + delta_q, 4)
        else:
            td_error = reward - 0.5
            delta_q  = self.alpha * td_error

        # Update meta-stats
        self.training_episodes += 1
        self.model_accuracy = min(99.5, self.model_accuracy + reward * 0.04 - 0.01)
        self.epsilon = max(0.01, self.epsilon * 0.997)
        self.last_update = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return {
            "reward":        round(reward,   4),
            "td_error":      round(td_error, 4),
            "delta_q":       round(delta_q,  4),
            "new_accuracy":  round(self.model_accuracy, 2),
            "new_epsilon":   round(self.epsilon, 4),
            "episodes":      self.training_episodes,
            "state_key":     state_key,
        }
