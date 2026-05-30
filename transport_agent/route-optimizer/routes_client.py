"""Google Routes API client — builds a real driving distance/duration matrix.

Replaces straight-line haversine with actual road distances and travel times from
Google's Compute Route Matrix. This matters for the storm narrative: haversine can't
represent "this road is now impassable, reroute around it," but real driving routes can.

Falls back to haversine automatically when GOOGLE_MAPS_API_KEY is unset or the call
fails, so the optimizer (and the demo) never break. The agent's reasoning is unchanged —
this only improves the quality of the numbers OR-Tools optimizes over.

Setup (one-time, on your machine / Cloud Run):
  1. In Google Cloud Console, enable the "Routes API" on your project.
  2. Create an API key, restrict it to the Routes API.
  3. Set GOOGLE_MAPS_API_KEY in the optimizer's environment (.env or Cloud Run var).
  Cost: billed per element (origins x destinations). A 6-farm demo = ~49 elements per
  solve — negligible against the monthly free credit. Set a daily quota cap to be safe.
"""
from __future__ import annotations
import json
import math
import os
import urllib.request
from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
ENDPOINT = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"


def _haversine_m(a, b):
    R = 6371000.0
    dlat = math.radians(b["lat"] - a["lat"])
    dlng = math.radians(b["lng"] - a["lng"])
    x = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(a["lat"])) * math.cos(math.radians(b["lat"])) * math.sin(dlng / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(x))


def _haversine_matrix(locs):
    """Fallback: straight-line distance matrix in meters, durations estimated at 50 km/h."""
    n = len(locs)
    dist = [[int(_haversine_m(locs[i], locs[j])) for j in range(n)] for i in range(n)]
    dur = [[int(dist[i][j] / (50_000 / 3600)) for j in range(n)] for i in range(n)]  # ~50km/h
    return dist, dur, "haversine"


def _waypoint(loc):
    return {"waypoint": {"location": {"latLng": {"latitude": loc["lat"], "longitude": loc["lng"]}}}}


def distance_matrix(locs):
    """Return (dist_meters, duration_seconds, source) NxN matrices for the given locations.

    locs: list of {"lat":.., "lng":..}. Index 0 is typically the depot.
    Uses Google Routes API when GOOGLE_MAPS_API_KEY is set; otherwise haversine.
    """
    n = len(locs)
    if not API_KEY:
        return _haversine_matrix(locs)
    # Compute Route Matrix caps at 625 elements (25x25). Our problems are tiny, but guard anyway.
    if n * n > 625:
        return _haversine_matrix(locs)

    body = {
        "origins": [_waypoint(l) for l in locs],
        "destinations": [_waypoint(l) for l in locs],
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": "originIndex,destinationIndex,distanceMeters,duration,condition",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            elements = json.loads(resp.read().decode())
    except Exception as e:
        print(f"[routes] API call failed ({type(e).__name__}: {e}) — falling back to haversine")
        return _haversine_matrix(locs)

    # Initialize with haversine so any missing/failed element still has a sane value.
    dist, dur, _ = _haversine_matrix(locs)
    for el in elements:
        i, j = el.get("originIndex"), el.get("destinationIndex")
        if i is None or j is None:
            continue
        if el.get("condition") == "ROUTE_EXISTS":
            if "distanceMeters" in el:
                dist[i][j] = int(el["distanceMeters"])
            d = el.get("duration", "")
            if isinstance(d, str) and d.endswith("s"):
                dur[i][j] = int(float(d[:-1]))
        else:
            # No route between these points (e.g. impassable after storm): make it prohibitive.
            dist[i][j] = 10**9
            dur[i][j] = 10**9
    return dist, dur, "google_routes"
