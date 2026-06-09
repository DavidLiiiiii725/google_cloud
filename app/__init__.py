"""Unified Agent Greenhouse + Transport application.

Two FastAPI services that share one MongoDB blackboard:
  - app.greenhouse  (port 8000) — pixel-art greenhouse, writes farms doc
  - app.transport   (port 8001) — reads farms, calls optimizer, writes transport_plans
The optimizer runs as a separate Cloud-Run-style microservice (optimizer/main.py).
"""
