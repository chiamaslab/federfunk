"""Optional six-ink comparison profiles; never selected for the frame by default.

The reference ink values below are from Pimoroni Inky v2.5.0's EL133UF1
SATURATED_PALETTE. They are a reference model, not measurements of this frame.
https://github.com/pimoroni/inky/blob/v2.5.0/inky/inky_el133uf1.py
Palette source: Copyright (c) 2018 Pimoroni Ltd. (MIT).
The diffusion implementation here is independent and uses standard Floyd–
Steinberg and Stucki kernels. No upstream renderer is imported or executed.

Pimoroni's MIT permission notice, retained with the reference data:
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""
from PIL import Image


SOURCE_URL = "https://github.com/pimoroni/inky/blob/v2.5.0/inky/inky_el133uf1.py#L106"
IDEAL = ((0, 0, 0), (255, 255, 255), (255, 255, 0),
         (255, 0, 0), (0, 0, 255), (0, 255, 0))
REFERENCE = ((0, 0, 0), (161, 164, 165), (208, 190, 71),
             (156, 72, 75), (61, 59, 94), (58, 91, 70))
# A model partway between the two extremes is deliberately modest. This
# factor changes the matching palette; it is not an image-saturation boost.
COMPENSATION = 0.6
MATCHING = tuple(tuple(round(a * (1-COMPENSATION) + b * COMPENSATION)
                      for a, b in zip(ideal, reference))
                 for ideal, reference in zip(IDEAL, REFERENCE))
PROFILES = {
    "current": dict(id="current", label="Current colors", experimental=False,
                    description="Your approved colors, with a selective feather boost.",
                    palette_model="Ideal six inks", dither="Floyd–Steinberg", source_url=None),
    "compensated_floyd": dict(id="compensated_floyd", label="Compensated · fine grain", experimental=True,
                    description="A reference ink palette, with neutral paper and lettering protected. Preview only.",
                    palette_model="Pimoroni EL133UF1 reference, 60% blend; not calibrated to this frame",
                    dither="Floyd–Steinberg", source_url=SOURCE_URL),
    "compensated_stucki": dict(id="compensated_stucki", label="Compensated · soft grain", experimental=True,
                    description="The same reference palette with diffusion over a wider area. Preview only.",
                    palette_model="Pimoroni EL133UF1 reference, 60% blend; not calibrated to this frame",
                    dither="Stucki", source_url=SOURCE_URL),
}

_FLOYD = ((1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1))
_STUCKI = ((1, 0, 8), (2, 0, 4), (-2, 1, 2), (-1, 1, 4),
           (0, 1, 8), (1, 1, 4), (2, 1, 2), (-2, 2, 1),
           (-1, 2, 2), (0, 2, 4), (1, 2, 2), (2, 2, 1))


def compensated(image, profile):
    """Return ideal-indexed P pixels, using a compensated matching palette.

Both alternatives use the same input and matching palette: only the kernel
differs. Exact white/black are fixed endpoints and discard incoming error;
near-neutral source pixels (channel spread <=8) can use black/white only.
That protects type without washing actual colored feathers into gray.
"""
    if profile not in ("compensated_floyd", "compensated_stucki"):
        raise ValueError("Unknown compensated color profile")
    width, height = image.size
    pixels = image.convert("RGB").tobytes()
    output = bytearray(b"\x01") * (width * height)
    kernel, denominator = (_FLOYD, 16) if profile == "compensated_floyd" else (_STUCKI, 42)
    stride = (width + 4) * 3
    errors = [[0.0] * stride for _ in range(3)]
    palette = MATCHING
    for y in range(height):
        direction = 1 if y % 2 == 0 else -1
        positions = range(width) if direction == 1 else range(width-1, -1, -1)
        current = errors[0]
        row = y * width
        for x in positions:
            offset = (row + x) * 3
            r, g, b = pixels[offset:offset+3]
            if r == g == b == 255:
                continue
            if r == g == b == 0:
                output[row+x] = 0
                continue
            i = (x + 2) * 3
            # Bound the carried error at the source gamut. The two models
            # deliberately stay in encoded RGB, matching their reference.
            rr = max(0.0, min(255.0, r + current[i]))
            gg = max(0.0, min(255.0, g + current[i+1]))
            bb = max(0.0, min(255.0, b + current[i+2]))
            neutral = max(r, g, b) - min(r, g, b) <= 8
            choices = (0, 1) if neutral else range(6)
            best, distance = 1, float("inf")
            for index in choices:
                pr, pg, pb = palette[index]
                error = (rr-pr)**2 + (gg-pg)**2 + (bb-pb)**2
                if error < distance:
                    best, distance = index, error
            output[row+x] = best
            pr, pg, pb = palette[best]
            dr, dg, db = (rr-pr)/denominator, (gg-pg)/denominator, (bb-pb)/denominator
            for dx, dy, weight in kernel:
                j = (x + 2 + dx * direction) * 3
                target = errors[dy]
                target[j] += dr * weight
                target[j+1] += dg * weight
                target[j+2] += db * weight
        errors = [errors[1], errors[2], [0.0] * stride]
    result = Image.frombytes("P", (width, height), bytes(output))
    # Compare ink choices fairly: every preview has the same RGB swatches.
    result.putpalette([channel for color in IDEAL for channel in color] + [255] * 750)
    return result
