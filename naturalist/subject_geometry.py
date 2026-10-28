"""Reviewed bird-only geometry. Coordinates refer to the full prepared RGBA.

The masks affect measurement and hit testing only; they never alter artwork.
Unreviewed, stale or resized geometry falls back explicitly to the image alpha.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

GEOMETRY_FILE = Path(__file__).parent / "artwork" / "geometry" / "subjects.json"


@lru_cache(maxsize=1)
def _catalogue():
    try:
        return json.loads(GEOMETRY_FILE.read_text())["assets"]
    except (OSError, ValueError, KeyError):
        return {}


def _geometry(asset, image=None):
    item = asset.get("subject_geometry") or _catalogue().get(asset.get("id"))
    if not item or item.get("review", {}).get("status") != "reviewed":
        return None, "No reviewed bird geometry; full opaque area used."
    if item.get("sha256") != asset.get("sha256"):
        # External PNG bytes are encoded locally; the reviewed RGBA digest binds
        # their geometry independently of harmless PNG encoder differences.
        if not (item.get("pixel_sha256") and item.get("pixel_sha256") == asset.get("pixel_sha256")):
            return None, "Artwork hash changed; full opaque area used."
    if image is not None and list(image.size) != item.get("size"):
        return None, "Artwork dimensions changed; full opaque area used."
    try:
        for key in ("polygons", "exclude"):
            for polygon in item.get(key, []):
                if len(polygon) < 3 or any(len(point) != 2 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in point) for point in polygon):
                    raise ValueError("Invalid polygon")
    except (TypeError, ValueError):
        return None, "Invalid geometry coordinates; full opaque area used."
    if image is not None and not _apply(item, image).getbbox():
        return None, "Reviewed mask is empty; full opaque area used."
    return item, "Reviewed visible bird area; external perch excluded."


def geometry_status(asset, image=None):
    item, reason = _geometry(asset, image)
    return {"reviewed": item is not None, "method": "reviewed-mask" if item else "full-alpha-fallback", "reason": reason}


def _apply(item, image):
    alpha = image.convert("RGBA").getchannel("A")
    mask = Image.new("L", image.size, 0 if item.get("polygons") else 255)
    draw = ImageDraw.Draw(mask)
    w, h = image.size
    for key, value in (("polygons", 255), ("exclude", 0)):
        for polygon in item.get(key, []):
            draw.polygon([(round(x * (w - 1)), round(y * (h - 1))) for x, y in polygon], fill=value)
    return ImageChops.multiply(alpha, mask)


def subject_mask(asset, image):
    """Return an L mask: prepared alpha multiplied by the reviewed bird region."""
    item, _ = _geometry(asset, image)
    return _apply(item, image) if item is not None else image.convert("RGBA").getchannel("A")


def subject_area(asset, image):
    """Alpha-weighted pixel area, including visible feet/tail but excluding perch."""
    return sum(i * n for i, n in enumerate(subject_mask(asset, image).histogram())) / 255.0


def subject_bounds(asset, image):
    """Full-image pixel bounding box of the bird, or None for empty alpha."""
    return subject_mask(asset, image).getbbox()
