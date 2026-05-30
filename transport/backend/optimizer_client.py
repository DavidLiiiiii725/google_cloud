"""Thin async client for the OR-Tools route-optimizer service on Cloud Run.

If OPTIMIZER_URL is unset, the pipeline catches the raised error and uses
agent.greedy_fallback — so local dev works with no Cloud Run deploy.
"""
from __future__ import annotations
import json
import urllib.request
from . import config


async def solve(problem: dict) -> dict:
    if not config.OPTIMIZER_URL:
        raise RuntimeError("OPTIMIZER_URL unset")
    req = urllib.request.Request(
        config.OPTIMIZER_URL.rstrip("/") + "/solve",
        data=json.dumps(problem).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # urllib is blocking; for the demo this is fine. Swap to httpx/aiohttp if needed.
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())
