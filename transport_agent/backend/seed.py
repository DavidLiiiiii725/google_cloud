"""Seed historical_climate with 12 monthly baseline docs. Idempotent — re-runs replace existing baseline."""
from __future__ import annotations
from . import db, config

# (mean, std, warn_low, warn_high, crit_low, crit_high) per sensor by month
# Single climate profile assumed (controlled greenhouse) — variation is minor seasonal drift.
BASELINE_TEMPLATE = {
    "temperature": (24.0, 2.0, 20.0, 30.0, 16.0, 36.0),
    "humidity":    (65.0, 8.0, 45.0, 80.0, 30.0, 90.0),
    "co2":         (800.0, 150.0, 500.0, 1400.0, 350.0, 1800.0),
    "vpd":         (1.0, 0.3, 0.4, 1.6, 0.2, 2.2),
    "par":         (450.0, 80.0, 200.0, 900.0, 80.0, 1200.0),
    "moisture":    (55.0, 10.0, 35.0, 75.0, 20.0, 88.0),
    "tank_level":  (75.0, 15.0, 25.0, 100.0, 15.0, 100.0),
    "ph":          (6.2, 0.3, 5.6, 6.8, 5.2, 7.2),
    "solution_ec": (1.8, 0.3, 1.2, 2.4, 0.8, 3.0),
}

# Monthly multipliers nudge means slightly (1=Jan ... 12=Dec)
MONTHLY_TEMP_OFFSET = [-1, -1, 0, 1, 2, 3, 3, 3, 2, 1, 0, -1]
MONTHLY_HUMID_OFFSET = [-5, -5, 0, 5, 5, 0, -5, -5, 0, 0, -5, -5]


def seed():
    col = db.historical_climate()
    col.delete_many({})
    docs = []
    for m in range(1, 13):
        doc = {"month": m, "sensors": {}}
        for sensor, (mean, std, wl, wh, cl, ch) in BASELINE_TEMPLATE.items():
            adj_mean = mean
            if sensor == "temperature":
                adj_mean = mean + MONTHLY_TEMP_OFFSET[m - 1]
            elif sensor == "humidity":
                adj_mean = mean + MONTHLY_HUMID_OFFSET[m - 1]
            doc["sensors"][sensor] = {
                "mean": adj_mean, "std": std,
                "warn_low": wl, "warn_high": wh,
                "crit_low": cl, "crit_high": ch,
            }
        docs.append(doc)
    col.insert_many(docs)
    print(f"[seed] inserted {len(docs)} monthly baseline docs into historical_climate ({db.db_mode()})")


def get_baseline(month: int) -> dict:
    """Return baseline dict for a month; seeds first if empty."""
    col = db.historical_climate()
    doc = col.find_one({"month": month})
    if not doc:
        seed()
        doc = col.find_one({"month": month}) or {"sensors": {}}
    return doc.get("sensors", {})


if __name__ == "__main__":
    db.get_db()
    seed()
