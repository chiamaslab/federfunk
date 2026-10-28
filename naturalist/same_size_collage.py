"""Local study: restore original bird dimensions within the rounded composition.

The compact layout supplies positions only. Every drawing is rendered again
from its source at the exact original width and height, never upscaled from a
preview PNG. The caller must explicitly supply any extra page area permitted.
"""
import math
from PIL import Image, ImageDraw, ImageFilter

import collage as baseline
import rounded_collage


def _factor(placements):
    lower, upper = 0.0, math.inf
    for p in placements:
        for dimension in ('w', 'h'):
            unit, pixels = p['item'][dimension], p[dimension]
            lower = max(lower, (pixels - .5) / unit if pixels > 1 else 0)
            upper = min(upper, (pixels + .5) / unit)
    if lower > upper + 1e-9:
        raise ValueError('Bird dimensions do not share one multiplier')
    return (lower + upper) / 2


def _shape(placement, label_lines, padding=0):
    """Full local contour, padded before dilation so no edge pixels are lost."""
    width, height = placement['w'], placement['h']
    label_left = math.floor((width-placement['label_width'])/2) if label_lines else 0
    label_right = math.ceil((width+placement['label_width'])/2) if label_lines else width-1
    left, right = min(0, label_left), max(width, label_right+1)
    bottom = height+placement['label_height']+1 if label_lines else height
    mask = Image.new('L', (right-left+2*padding, bottom+2*padding))
    picture = placement['item']['image'].resize((width, height), Image.Resampling.LANCZOS)
    mask.paste(picture.getchannel('A').point(lambda value: 255 if value else 0),
               (padding-left, padding))
    if label_lines:
        ImageDraw.Draw(mask).rectangle((label_left-left+padding, height+10+padding,
                                       label_right-left+padding, bottom-1+padding), fill=255)
    # Retain undilated ink bounds separately from the collision padding.
    bounds = mask.getbbox()
    bounds = tuple(value + (left-padding if index%2 == 0 else -padding)
                   for index, value in enumerate(bounds)) if bounds else (0, 0, 0, 0)
    if padding:
        mask = mask.filter(ImageFilter.MaxFilter(2*padding+1))
    width, height = mask.size
    stride = (width+7)//8
    encoded = mask.convert('1').tobytes()
    trailing_bits = stride*8-width
    rows = tuple(int.from_bytes(encoded[y*stride:(y+1)*stride], 'big') >> trailing_bits
                 for y in range(height))
    return dict(rows=rows, width=width, height=height, dx=left-padding, dy=-padding, bounds=bounds)


def _overlap(first, first_position, second, second_position):
    """Count shared contour pixels with no page-sized mask or clipping."""
    ax, ay = first_position[0]+first['dx'], first_position[1]+first['dy']
    bx, by = second_position[0]+second['dx'], second_position[1]+second['dy']
    top, bottom = max(ay, by), min(ay+first['height'], by+second['height'])
    if bottom <= top or ax+first['width'] <= bx or bx+second['width'] <= ax:
        return 0
    right = max(ax+first['width'], bx+second['width'])
    ashift, bshift = right-ax-first['width'], right-bx-second['width']
    return sum(((first['rows'][y-ay] << ashift) & (second['rows'][y-by] << bshift)).bit_count()
               for y in range(top, bottom))


def _inside(shape, position, region):
    left, top, width, height = region
    x, y = position
    x0, y0, x1, y1 = shape['bounds']
    return x+x0 >= left and y+y0 >= top and x+x1 <= left+width and y+y1 <= top+height


def _clear(placements, safe_region, label_lines):
    shapes = [_shape(p, label_lines) for p in placements]
    positions = [(round(p['x']), round(p['y'])) for p in placements]
    return (all(_inside(shape, position, safe_region) for shape, position in zip(shapes, positions))
            and not any(_overlap(shapes[i], positions[i], shapes[j], positions[j])
                        for i in range(len(shapes)) for j in range(i)))


def _space_contours(placements, safe_region, label_lines, gap=24):
    """Repair close neighbors with small translations, never resizing artwork.

    Both 1200x1600 orientations use a 24px minimum contour gap. Dilation
    includes every antialiased pixel, twig and label. Movement is bounded to
    three gaps per bird; crowded prints keep the best safe local improvement
    rather than shrinking all birds or replacing the rounded arrangement.
    """
    if len(placements) < 2:
        return placements
    padding = math.ceil(gap/2)
    shapes = [_shape(p, label_lines, padding) for p in placements]
    ink = [_shape(p, label_lines) for p in placements]
    original = [(round(p['x']), round(p['y'])) for p in placements]
    positions = list(original)
    pairs = [(i, j) for i in range(len(shapes)) for j in range(i)]
    directions = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
    limit_squared = (gap*3)**2

    def scores(points):
        return {(i, j): _overlap(shapes[i], points[i], shapes[j], points[j]) for i, j in pairs}

    def displacement(points):
        return sum((x-ox)**2+(y-oy)**2 for (x, y), (ox, oy) in zip(points, original))

    overlaps = scores(positions)
    for _ in range(max(24, len(placements)*12)):
        conflicts = [pair for pair, area in overlaps.items() if area]
        if not conflicts:
            break
        current = (len(conflicts), sum(overlaps.values()), displacement(positions))
        best, best_positions, best_overlaps = current, None, None
        involved = sorted({index for pair in conflicts for index in pair})
        # Prefer a short local step; larger escape steps are only considered
        # if no short step can improve the crowded neighborhood.
        for step in (2, 4, 8, 16):
            moves = [((index, dx*step, dy*step),) for index in involved for dx, dy in directions]
            moves.extend(((i, dx*step, dy*step), (j, -dx*step, -dy*step))
                         for i, j in conflicts for dx, dy in directions)
            for move in moves:
                trial = list(positions)
                changed = {index for index, _, _ in move}
                for index, dx, dy in move:
                    trial[index] = (positions[index][0]+dx, positions[index][1]+dy)
                if any((trial[i][0]-original[i][0])**2+(trial[i][1]-original[i][1])**2 > limit_squared
                       or not _inside(ink[i], trial[i], safe_region) for i in changed):
                    continue
                affected = [pair for pair in pairs if changed.intersection(pair)]
                if any(_overlap(ink[i], trial[i], ink[j], trial[j]) for i, j in affected):
                    continue
                trial_overlaps = dict(overlaps)
                for i, j in affected:
                    trial_overlaps[i, j] = _overlap(shapes[i], trial[i], shapes[j], trial[j])
                score = (sum(bool(area) for area in trial_overlaps.values()),
                         sum(trial_overlaps.values()), displacement(trial))
                if score < best:
                    best, best_positions, best_overlaps = score, trial, trial_overlaps
            if best_positions is not None:
                break
        if best_positions is None:
            break
        positions, overlaps = best_positions, best_overlaps

    if not any(overlaps.values()):
        # Once clear, move each bird back toward its original center wherever
        # possible. This removes unnecessary motion from the coarse repair.
        for _ in range(gap*3):
            moved = False
            for i, (x, y) in enumerate(positions):
                ox, oy = original[i]
                candidates = [(x+dx, y+dy) for dx, dy in directions
                              if (x+dx-ox)**2+(y+dy-oy)**2 < (x-ox)**2+(y-oy)**2]
                candidates.sort(key=lambda p: ((p[0]-ox)**2+(p[1]-oy)**2, p))
                for point in candidates:
                    if not _inside(ink[i], point, safe_region):
                        continue
                    if any(_overlap(shapes[i], point, shapes[j], positions[j]) for j in range(len(shapes)) if i != j):
                        continue
                    positions[i] = point
                    moved = True
                    break
            if not moved:
                break
    result = []
    for p, (x, y) in zip(placements, positions):
        result.append({**p, 'x': x, 'y': y, 'cx': p['cx'] + x-round(p['x'])})
    return result


def arrange(items, region, label_lines, *, safe_region=None):
    if not items:
        return []
    originals = baseline.arrange(items, region, label_lines)
    compact = rounded_collage.arrange(items, region, label_lines)
    original_by_item = {id(p['item']): p for p in originals}
    ratio = _factor(originals) / _factor(compact)
    result = []
    for p in compact:
        original = original_by_item[id(p['item'])]
        # Preserve the rounded drawing centers and expand their spacing along
        # with the art. Copy the original exact raster and label dimensions.
        width, height = original['w'], original['h']
        x = (p['x'] + p['w']/2) * ratio - width/2
        y = (p['y'] + p['h']/2) * ratio - height/2
        restored = {key: value for key, value in original.items() if key != 'collision'}
        restored.update(x=x, y=y, cx=x+width/2)
        result.append(restored)

    min_x = min(p['cx']-p['label_width']/2 for p in result)
    min_y = min(p['y'] for p in result)
    max_x = max(p['cx']+p['label_width']/2 for p in result)
    max_y = max(p['y']+p['h']+p['label_height'] for p in result)
    left, top, width, height = safe_region or region
    if max_x-min_x > width-2 or max_y-min_y > height-2:
        # Keep the established layout if this collection cannot be rounded
        # safely at its existing sizes. Never drop or shrink a bird to fit.
        return originals
    dx = left+width/2-(min_x+max_x)/2
    dy = top+height/2-(min_y+max_y)/2
    for p in result:
        p['x'] = round(p['x']+dx)
        p['y'] = round(p['y']+dy)
        p['cx'] = p['x']+p['w']/2
    if not _clear(result, (left, top, width, height), label_lines):
        return originals
    return _space_contours(result, (left, top, width, height), label_lines)
