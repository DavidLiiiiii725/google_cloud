"""The reasoning layer of the Merchant Agent.

THIS FILE is the agent's brain. The supply that actually arrives is decided upstream by
the Transport Agent (which farms it could route vs. which the storm cut off); the Merchant
reads that outcome and makes the *market* decisions a human trader would:

  • which arriving crops exist, and in what quantity (join transport_plans × farms)
  • who gets served first when there isn't enough (priority fair-share)
  • what the shortfall does to price (scarcity pricing)

The math is deliberately simple and explainable — the value is the policy, not the
arithmetic. gemini_client.py adds a natural-language rationale on top; everything here is
deterministic so the demo is reproducible and never blocks on the network.
"""
from __future__ import annotations
import re

# Base wholesale price, integer cents per kg. Tuned so essentials are cheap and
# export/luxury crops are dear; scarcity multiplies these up.
PRICE_BOOK = {
    "tomato": 180, "avocado": 320, "maize": 90, "strawberry": 600,
    "potato": 110, "mango": 260, "milk": 150, "kale": 140,
}
DEFAULT_PRICE = 200

# How hard scarcity pushes price. At a total shortfall (nothing arrives) price reaches
# base * (1 + MAX_SCARCITY_PREMIUM). At no shortfall, multiplier is 1.0.
MAX_SCARCITY_PREMIUM = 0.8

_SUFFIX_RE = re.compile(r"-(cold|ambient|load)$")


def base_farm_id(fid: str) -> str:
    """Strip the compatibility-class suffixes the Transport Agent appends to nodes
    (`-cold` / `-ambient`) and the optimizer's `-load`, recovering the real farm _id."""
    prev = None
    out = fid or ""
    while out != prev:
        prev = out
        out = _SUFFIX_RE.sub("", out)
    return out


def served_farm_ids(plan: dict | None) -> set[str]:
    """Base farm ids that appear as a routed stop in the committed plan."""
    out: set[str] = set()
    if not plan:
        return out
    for v in plan.get("vehicles", []):
        for stop in v.get("stops", []):
            out.add(base_farm_id(stop.get("farm_id", "")))
    return out


def arriving_supply(plan: dict | None, farms: list[dict]) -> dict:
    """Crop → kg that will actually reach market, plus which farms contributed and which
    were cut off. A farm contributes its stock only if the Transport Agent routed it; the
    storm's blocked/destroyed farms simply never appear in the plan, so their crops vanish
    from supply. This is the cascade made concrete: fewer routed farms → less supply here.
    """
    by_id = {f["_id"]: f for f in farms}
    served = served_farm_ids(plan)
    supply: dict[str, float] = {}
    contributors: dict[str, list[str]] = {}
    for fid in served:
        farm = by_id.get(fid)
        if not farm:
            continue
        for s in farm.get("stock", []):
            crop = s.get("crop", s.get("sku", "unknown"))
            kg = float(s.get("quantity_kg", 0) or 0)
            if kg <= 0:
                continue
            supply[crop] = supply.get(crop, 0.0) + kg
            contributors.setdefault(crop, []).append(fid)

    # Farms present on the blackboard but not served — the lost supply the storm caused.
    cut_off = []
    for f in farms:
        if f["_id"] not in served:
            crops = sorted({s.get("crop") for s in f.get("stock", []) if s.get("crop")})
            cut_off.append({
                "farm_id": f["_id"], "name": f.get("name", f["_id"]),
                "access": f.get("access", "open"),
                "yield_status": f.get("yield_status", "normal"),
                "crops": crops,
            })
    return {
        "supply": {k: round(v, 1) for k, v in supply.items()},
        "contributors": contributors,
        "served_farms": sorted(served),
        "cut_off_farms": cut_off,
    }


def _scarcity(arriving_kg: float, requested_kg: float) -> float:
    """Scarcity multiplier on price. 1.0 when supply meets demand; rises toward
    1 + MAX_SCARCITY_PREMIUM as the shortfall approaches 100%."""
    if requested_kg <= 0:
        return 1.0
    shortfall_ratio = max(0.0, (requested_kg - arriving_kg) / requested_kg)
    return round(1.0 + shortfall_ratio * MAX_SCARCITY_PREMIUM, 3)


def allocate(supply: dict, buyers: list[dict]) -> dict:
    """Priority fair-share allocation with scarcity pricing.

    For each crop: serve buyers in priority order (essentials first), each taking up to
    what they requested until the arriving supply runs out. Price every kg of that crop at
    the same scarcity-adjusted unit price (shortfall lifts price for everyone equally —
    rationing happens by *who gets served*, not by charging the hospital more).
    Returns allocation lines, per-crop summary, and overall fulfillment totals.
    """
    # Index demand by crop with buyer order = (priority asc, requested desc).
    crops = sorted(set(supply) | {d["crop"] for b in buyers for d in b.get("demand", [])})
    lines: list[dict] = []
    crop_summary: list[dict] = []

    for crop in crops:
        wanters = []
        for b in buyers:
            for d in b.get("demand", []):
                if d["crop"] == crop and d.get("requested_kg", 0) > 0:
                    wanters.append((b, float(d["requested_kg"])))
        wanters.sort(key=lambda t: (t[0].get("priority", 99), -t[1]))

        arriving = float(supply.get(crop, 0.0))
        total_req = sum(req for _, req in wanters)
        base = PRICE_BOOK.get(crop, DEFAULT_PRICE)
        mult = _scarcity(arriving, total_req)
        unit_price = int(round(base * mult))

        remaining = arriving
        allocated_total = 0.0
        for b, req in wanters:
            give = round(min(remaining, req), 1)
            remaining = round(remaining - give, 1)
            allocated_total += give
            fill = round(give / req, 3) if req else 0.0
            lines.append({
                "buyer_id": b["_id"], "buyer_name": b.get("name", b["_id"]),
                "channel": b.get("channel", ""), "priority": b.get("priority", 99),
                "crop": crop, "requested_kg": round(req, 1), "allocated_kg": give,
                "fill_rate": fill, "unit_price_cents": unit_price,
                "line_total_cents": int(round(give * unit_price)),
                "status": "filled" if fill >= 0.999 else "partial" if fill > 0 else "unfilled",
            })

        if total_req > 0 or arriving > 0:
            crop_summary.append({
                "crop": crop, "arriving_kg": round(arriving, 1),
                "requested_kg": round(total_req, 1), "allocated_kg": round(allocated_total, 1),
                "fill_rate": round(allocated_total / total_req, 3) if total_req else 1.0,
                "scarcity_multiplier": mult, "base_price_cents": base,
                "unit_price_cents": unit_price,
                "unmet_kg": round(max(0.0, total_req - allocated_total), 1),
                "surplus_kg": round(max(0.0, arriving - allocated_total), 1),
            })

    total_requested = sum(l["requested_kg"] for l in lines)
    total_allocated = sum(l["allocated_kg"] for l in lines)
    revenue = sum(l["line_total_cents"] for l in lines)
    scarce_crops = [c["crop"] for c in crop_summary if c["scarcity_multiplier"] > 1.0]

    return {
        "lines": lines,
        "crop_summary": crop_summary,
        "fulfillment": {
            "requested_kg": round(total_requested, 1),
            "allocated_kg": round(total_allocated, 1),
            "fill_rate": round(total_allocated / total_requested, 3) if total_requested else 1.0,
            "unmet_kg": round(max(0.0, total_requested - total_allocated), 1),
        },
        "revenue_cents": int(revenue),
        "scarce_crops": scarce_crops,
    }


def reallocation_diff(prior: dict | None, result: dict, supply_info: dict) -> dict | None:
    """Before/after for the headline storm reallocation — the Merchant's analogue of the
    Transport Agent's replan_diff. Populated only when a prior order exists."""
    if not prior:
        return None
    pf = prior.get("fulfillment", {})
    cf = result["fulfillment"]
    prior_rev = prior.get("revenue_cents", 0)
    cur_rev = result["revenue_cents"]
    cut = [f["name"] for f in supply_info.get("cut_off_farms", [])
           if f.get("access") == "blocked" or f.get("yield_status") == "destroyed"]
    bits = []
    if pf.get("fill_rate") is not None:
        bits.append(f"fill {round(pf.get('fill_rate',0)*100)}% → {round(cf['fill_rate']*100)}%")
    if result["scarce_crops"]:
        bits.append(f"scarcity pricing on {', '.join(result['scarce_crops'])}")
    if cut:
        bits.append(f"{', '.join(cut)} supply lost")
    return {
        "fill_before": round(pf.get("fill_rate", 0) * 100),
        "fill_after": round(cf["fill_rate"] * 100),
        "allocated_before_kg": pf.get("allocated_kg", 0),
        "allocated_after_kg": cf["allocated_kg"],
        "revenue_before_cents": prior_rev,
        "revenue_after_cents": cur_rev,
        "summary": "; ".join(bits) or "allocation adjusted",
    }
