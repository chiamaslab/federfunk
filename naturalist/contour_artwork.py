"""Reproduce reviewed vector cutouts from unaltered historical source scans.

Only alpha, a bounding crop and a final size reduction change. Source pigment
and naturally occluded anatomy are never repainted or synthesized. Recipes use
coordinates on the entire source at their stated reference width.
"""
from __future__ import annotations

import math
import warnings

from PIL import Image, ImageDraw, ImageFilter

MAX_SOURCE_PIXELS = 200_000_000


def build_cutout_with_geometry(source_path, recipe):
    reference = float(recipe["reference_width"])
    polygons = recipe["polygons"]
    if not math.isfinite(reference) or reference <= 0 or not polygons:
        raise ValueError("Invalid contour reference")
    if recipe.get("rotation_degrees", 0):
        raise ValueError("These reviewed contours preserve the source orientation")
    maximum = int(recipe.get("max_size", 1400))
    blur = float(recipe.get("alpha_edge_blur", 0.25))
    if not 1 <= maximum <= 1400 or not math.isfinite(blur) or not 0 <= blur <= 1:
        raise ValueError("Invalid contour output limits")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        with Image.open(source_path) as source:
            if source.width * source.height > MAX_SOURCE_PIXELS:
                raise ValueError("Source exceeds reviewed scan pixel limit")
            scale = source.width / reference
            points = []
            for key in ("polygons", "exclude", "subject_polygons", "subject_exclude"):
                for polygon in recipe.get(key, []):
                    if len(polygon) < 3:
                        raise ValueError("A contour needs at least three points")
                    for point in polygon:
                        if len(point) != 2 or any(not math.isfinite(v) for v in point):
                            raise ValueError("Invalid contour coordinate")
                        if not (0 <= point[0] <= reference and 0 <= point[1] <= source.height / scale):
                            raise ValueError("Contour outside source")
                    if key == "polygons":
                        points.extend(polygon)
            pad = math.ceil(blur * scale * 4) + 2
            crop = (max(0, math.floor(min(p[0] for p in points) * scale) - pad),
                    max(0, math.floor(min(p[1] for p in points) * scale) - pad),
                    min(source.width, math.ceil(max(p[0] for p in points) * scale) + pad + 1),
                    min(source.height, math.ceil(max(p[1] for p in points) * scale) + pad + 1))
            picture = source.crop(crop).convert("RGBA")
    mask = Image.new("L", picture.size)
    draw = ImageDraw.Draw(mask)
    for key, fill in (("polygons", 255), ("exclude", 0)):
        for polygon in recipe.get(key, []):
            draw.polygon([(round(x * scale) - crop[0], round(y * scale) - crop[1]) for x, y in polygon], fill=fill)
    if blur:
        mask = mask.filter(ImageFilter.GaussianBlur(blur * scale))
    picture.putalpha(mask)
    bounds = mask.getbbox()
    if not bounds:
        raise ValueError("Empty artwork contour")
    picture = picture.crop(bounds)
    x0, y0 = crop[0] + bounds[0], crop[1] + bounds[1]
    original_size = picture.size
    if min(original_size) < 2:
        raise ValueError("Contour has no two-dimensional artwork")
    picture.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
    geometry = {"size": list(picture.size)}
    for source_key, key in (("subject_polygons", "polygons"), ("subject_exclude", "exclude")):
        geometry[key] = [[[max(0, min(1, (x * scale - x0) / (original_size[0] - 1))),
                           max(0, min(1, (y * scale - y0) / (original_size[1] - 1)))]
                          for x, y in polygon] for polygon in recipe.get(source_key, [])]
    return picture, geometry


def build_cutout(source_path, recipe):
    return build_cutout_with_geometry(source_path, recipe)[0]
