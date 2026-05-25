"""Local image picker for the vision step. Generates simple pixel-art-style PNGs on first run."""
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw
from . import config

LEVELS = ["healthy", "stressed", "wilting"]
ZONES = ["seedling", "growing", "harvest"]


def _generate_placeholder(path: Path, zone: str, level: str):
    """Tiny but Gemini-legible plant image: solid background + a stylised plant silhouette."""
    W, H = 128, 128
    sky = {
        "healthy": (135, 206, 235),
        "stressed": (235, 180, 110),
        "wilting": (210, 100, 60),
    }[level]
    leaf_top = {
        "healthy": (40, 160, 70),
        "stressed": (160, 150, 50),
        "wilting": (120, 90, 40),
    }[level]
    leaf_bottom = {
        "healthy": (20, 110, 40),
        "stressed": (120, 110, 30),
        "wilting": (90, 60, 30),
    }[level]
    soil = (90, 50, 30) if level != "wilting" else (110, 70, 40)

    plant_height = {"seedling": 40, "growing": 70, "harvest": 95}[zone]
    droop = {"healthy": 0, "stressed": 10, "wilting": 25}[level]

    img = Image.new("RGB", (W, H), sky)
    d = ImageDraw.Draw(img)
    # soil band
    d.rectangle((0, H - 24, W, H), fill=soil)
    # stem
    stem_x = W // 2
    base_y = H - 24
    top_y = base_y - plant_height
    stem_color = (50, 100, 40) if level == "healthy" else (120, 100, 40) if level == "stressed" else (110, 70, 30)
    d.rectangle((stem_x - 3, top_y, stem_x + 3, base_y), fill=stem_color)
    # leaves (3 pairs)
    for i, frac in enumerate([0.25, 0.55, 0.85]):
        y = int(base_y - plant_height * frac)
        size = 14 + i * 4
        ly = y + droop
        # left leaf
        d.ellipse((stem_x - 3 - size, ly - size // 2, stem_x - 3, ly + size // 2), fill=leaf_top)
        d.ellipse((stem_x - 3 - size, ly, stem_x - 3, ly + size // 2), fill=leaf_bottom)
        # right leaf
        d.ellipse((stem_x + 3, ly - size // 2, stem_x + 3 + size, ly + size // 2), fill=leaf_top)
        d.ellipse((stem_x + 3, ly, stem_x + 3 + size, ly + size // 2), fill=leaf_bottom)
    # fruit for harvest+healthy
    if zone == "harvest" and level != "wilting":
        fruit_color = (220, 40, 40) if level == "healthy" else (170, 80, 40)
        for fy in (top_y + 18, top_y + 40):
            d.ellipse((stem_x + 6, fy, stem_x + 18, fy + 12), fill=fruit_color)
    img.save(path, "PNG")


def ensure_assets():
    config.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    for zone in ZONES:
        for level in LEVELS:
            p = config.ASSETS_DIR / f"{zone}_{level}.png"
            if not p.exists():
                _generate_placeholder(p, zone, level)


def _level_for(zone_health: float) -> str:
    if zone_health >= 0.7:
        return "healthy"
    if zone_health >= 0.35:
        return "stressed"
    return "wilting"


def image_paths_for(zone_health: dict) -> list[Path]:
    """Return the chosen image path per zone, ordered seedling, growing, harvest."""
    ensure_assets()
    return [config.ASSETS_DIR / f"{z}_{_level_for(zone_health[z])}.png" for z in ZONES]
