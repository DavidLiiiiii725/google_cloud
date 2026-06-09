"""Gemini wrapper for the Merchant Agent (google-genai, Vertex AI + ADC).

Adds a natural-language trading rationale on top of the deterministic allocation in
agent.py. Always degrades gracefully: if Vertex is unavailable it returns a templated
rationale built from the same numbers, so the demo never blocks on the network.
"""
from __future__ import annotations
import json
from . import config, events

_client = None
_init_failed = False


def _get_client():
    global _client, _init_failed
    if _client is not None:
        return _client
    if _init_failed:
        return None
    try:
        from google import genai
        _client = genai.Client(vertexai=True, project=config.GCP_PROJECT, location=config.GCP_REGION)
        return _client
    except Exception as e:
        _init_failed = True
        print(f"[gemini] merchant init failed: {e} — using templated rationale")
        return None


def _fallback_rationale(result: dict, supply_info: dict, trigger: str) -> str:
    f = result["fulfillment"]
    scarce = result.get("scarce_crops", [])
    cut = [c["name"] for c in supply_info.get("cut_off_farms", [])
           if c.get("access") == "blocked" or c.get("yield_status") == "destroyed"]
    parts = [
        f"Served {round(f['fill_rate']*100)}% of standing demand "
        f"({round(f['allocated_kg'])}kg of {round(f['requested_kg'])}kg requested)."
    ]
    if scarce:
        parts.append(f"Applied scarcity pricing on {', '.join(scarce)} where arrivals fell short of orders.")
    else:
        parts.append("Supply met demand, so prices held at baseline.")
    if cut:
        parts.append(f"Lost supply from {', '.join(cut)} after the storm; "
                     f"prioritised the hospital and institutional buyers over export orders.")
    else:
        parts.append("Allocated essential buyers first, then institutional, retail and export in turn.")
    return " ".join(parts)


def _prompt(result: dict, supply_info: dict, trigger: str) -> str:
    payload = {
        "trigger": trigger,
        "arriving_supply_kg": supply_info.get("supply", {}),
        "cut_off_farms": [c["name"] for c in supply_info.get("cut_off_farms", [])],
        "fulfillment": result["fulfillment"],
        "crop_summary": result["crop_summary"],
        "scarce_crops": result.get("scarce_crops", []),
        "revenue_cents": result["revenue_cents"],
    }
    return (
        "You are the Merchant Agent in an agricultural supply chain. The Transport Agent has "
        "just delivered the supply summarized below; you have already allocated it across "
        "competing buyers (priority 1 = essential hospital, 4 = discretionary export) and set "
        "scarcity prices. In 2-3 sentences, explain the trade-offs you made: who you protected "
        "under shortage, which crops you re-priced and why, and the revenue impact. Be concrete "
        "and use the numbers. Plain prose, no preamble.\n\n"
        f"Data: {json.dumps(payload, default=str)}"
    )


def allocation_rationale(result: dict, supply_info: dict, trigger: str = "initial") -> dict:
    """Return {text, model, source}. source ∈ {gemini, fallback}."""
    client = _get_client()
    if client is None:
        return {"text": _fallback_rationale(result, supply_info, trigger),
                "model": config.GEMINI_MODEL, "source": "fallback"}
    try:
        from google.genai import types as gtypes
        resp = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=_prompt(result, supply_info, trigger),
            config=gtypes.GenerateContentConfig(temperature=0.4),
        )
        text = (resp.text or "").strip()
        if not text:
            raise ValueError("empty response")
        return {"text": text, "model": config.GEMINI_MODEL, "source": "gemini"}
    except Exception as e:
        events.emit("REASON", f"Gemini rationale failed ({type(e).__name__}) — using templated rationale")
        return {"text": _fallback_rationale(result, supply_info, trigger),
                "model": config.GEMINI_MODEL, "source": "fallback"}
