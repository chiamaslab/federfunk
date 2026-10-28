"""Preview-only, body-centered packing for a filled natural-history oval.

The source drawings and count-based scale are unchanged.  Bird bodies guide
composition, while the complete drawing (including perches) and label blocks
remain protected by the existing conservative collision masks.
"""
import hashlib
import math

from PIL import Image, ImageChops, ImageDraw

import collage as baseline


UNIT = baseline.UNIT


def _identity(item):
    return str(item.get("bird", {}).get("scientific_name") or
               item.get("identity") or item.get("asset", {}).get("id", ""))


def _subject(item, size):
    """Bird-body center within the complete placement box, in grid units."""
    w, h, box_w, _ = size
    image = item["image"]
    bounds = item.get("body_bounds") or (0, 0, image.width, image.height)
    l, t, r, b = bounds
    return ((box_w - w) / 2 + (l + r) * .5 * w / image.width) / UNIT, \
           ((t + b) * .5 * h / image.height) / UNIT


def _prepare(items, region, labels, factor):
    _, _, width, height = region
    gw, gh = int(width // UNIT), int(height // UNIT)
    prepared = []
    for item in items:
        size = baseline._dimensions(item, factor, labels)
        footprint = baseline._footprint(item, size, labels)
        if footprint.width >= gw - 2 or footprint.height >= gh - 2:
            return None
        prepared.append((item, size, footprint, _subject(item, size)))
    # Largest visible birds establish the group; long perches must not decide
    # the visual hierarchy. Stable identities break ties without count-rank
    # dependent anchor changes when a thirteenth bird arrives.
    return sorted(prepared, key=lambda entry: (-entry[0]["weight"], _identity(entry[0])))


def _pack(prepared, region, labels, phase):
    left, top, width, height = region
    gw, gh = int(width // UNIT), int(height // UNIT)
    cx, cy = gw / 2, gh / 2
    rx, ry = gw * .47, gh * .47
    occupied = Image.new("L", (gw, gh))
    # Full silhouettes stay inside the oval. The generous mask includes thin
    # tails/feet and label space, so no part is clipped to obtain roundness.
    outside = Image.new("L", (gw, gh), 255)
    ImageDraw.Draw(outside).ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=0)
    placed = []
    total_weight = weighted_x = weighted_y = 0
    for index, (item, size, footprint, subject) in enumerate(prepared):
        mw, mh = footprint.size
        ox, oy = subject
        weight = item["weight"]
        angle = int.from_bytes(hashlib.sha256(_identity(item).encode()).digest()[:2], "big") / 65536 * math.tau + phase
        # A small stable directional preference prevents perfectly mechanical
        # concentric rows, but central grouping remains the dominant objective.
        tx, ty = .10 * math.cos(angle), .10 * math.sin(angle)
        candidates = []
        for y in range(1, gh - mh, 2):
            for x in range(1, gw - mw, 2):
                bx, by = (x + ox - cx) / rx, (y + oy - cy) / ry
                centroid_x = (weighted_x + weight * bx) / (total_weight + weight)
                centroid_y = (weighted_y + weight * by) / (total_weight + weight)
                cost = (bx - tx) ** 2 + (by - ty) ** 2
                cost += .8 * (centroid_x ** 2 + centroid_y ** 2)
                candidates.append((cost, x, y, bx, by))
        candidates.sort()
        for _, x, y, bx, by in candidates:
            if ImageChops.multiply(outside.crop((x, y, x + mw, y + mh)), footprint).getbbox():
                continue
            if ImageChops.multiply(occupied.crop((x, y, x + mw, y + mh)), footprint).getbbox():
                continue
            occupied.paste(255, (x, y), footprint)
            placement = baseline._placement(item, size, left + x * UNIT, top + y * UNIT, labels)
            placement["collision"] = (x, y, footprint)
            placed.append(placement)
            weighted_x += weight * bx
            weighted_y += weight * by
            total_weight += weight
            break
        else:
            return None
    return placed


def _score(placements, region):
    """Balance the visible birds, not transparent boxes or long perches."""
    left, top, width, height = region
    points = []
    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    for p in placements:
        item = p["item"]
        ox, oy = _subject(item, (p["w"], p["h"], p["label_width"], p["h"] + p["label_height"]))
        bx = p["cx"] - p["label_width"] / 2 + ox * UNIT
        by = p["y"] + oy * UNIT
        x, y = (bx-left-width/2)/width, (by-top-height/2)/height
        points.append((x, y, item["weight"]))
        min_x, min_y = min(min_x, p["x"]), min(min_y, p["y"])
        max_x, max_y = max(max_x, p["x"]+p["w"]), max(max_y, p["y"]+p["h"]+p["label_height"])
    weight = sum(p[2] for p in points)
    mean_x, mean_y = sum(x*w for x,y,w in points)/weight, sum(y*w for x,y,w in points)/weight
    vx = sum(w*(x-mean_x)**2 for x,y,w in points)/weight
    vy = sum(w*(y-mean_y)**2 for x,y,w in points)/weight
    covariance = sum(w*(x-mean_x)*(y-mean_y) for x,y,w in points)/weight
    spread = vx + vy
    anisotropy = ((vx-vy)**2 + 4*covariance**2)/(spread**2 + 1e-9)
    box_center = ((min_x+max_x)/2-left-width/2)**2/width**2 + ((min_y+max_y)/2-top-height/2)**2/height**2
    return 8*(mean_x**2+mean_y**2) + .1*anisotropy + .6*spread + 2*box_center


def arrange(items, region, label_lines):
    if not items:
        return []
    _, _, width, height = region
    if width <= 2 * UNIT or height <= 2 * UNIT or label_lines not in range(4):
        raise ValueError("Invalid collage region or label count")
    if any(item["w"] <= 0 or item["h"] <= 0 or item["weight"] <= 0 for item in items):
        raise ValueError("Illustration sizes and weights must be positive")
    factor = math.sqrt(width * height * (.28 if label_lines else .38) / sum(item["weight"] for item in items))
    profiles = [baseline._labels(label_lines, size) for size in (24, 22, 20, 18, 16, 14, 12)]
    profile_index = next((i for i,p in enumerate(profiles)
                          if len(items)*p["min_width"]*p["label_height"] <= width*height*.4), len(profiles)-1)
    # Try a bounded set of compositions at a common scale. Never shrink or
    # omit a low-count species independently to make the oval look tidier.
    for labels in profiles[profile_index:profile_index+2]:
        for attempt in range(13):
            prepared = _prepare(items, region, labels, factor * .90 ** attempt)
            if prepared is None:
                continue
            candidates = [p for phase in (0, math.pi/2, math.pi, 3*math.pi/2)
                          if (p := _pack(prepared, region, labels, phase)) is not None]
            if candidates:
                return min(candidates, key=lambda p: _score(p, region))
    # Dense labels or extreme silhouettes still have the established
    # all-species safety fallback. No change to colors, QR or source pixels.
    return baseline.arrange(items, region, label_lines)
