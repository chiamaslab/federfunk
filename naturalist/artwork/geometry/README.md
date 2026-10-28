# Bird-only sizing geometry

`subjects.json` records visual review of all 25 bundled variants, bound to each
prepared PNG's SHA-256 and full dimensions. Optional packs carry equivalent
geometry and a decoded RGBA hash in their registry entries.

Coordinates are normalized to the complete prepared image, before bounding-box
cropping or resizing. Inclusion polygons form the visible bird region;
exclusion polygons remove botanical ink. Empty polygon lists deliberately mean
the full prepared alpha, used for reviewed cutouts that have no substantial
external perch. The masks count visible bills, wings, tails and feet, but not
branches and leaves. A perch that crosses the bird is removed only where its
visible outline can be distinguished. Hidden feathers are never reconstructed.
Tiny toe/branch boundaries and anti-aliased edges are approximate; this is a
visible-ink estimate, not a biological body-area measurement.

Every mask was inspected over its actual full image. Five difficult perched
birds received a second independent review with enlarged foot/perch crops:
Black-capped Chickadee, American Robin, Eurasian Collared-Dove, Mountain
Chickadee and Great Horned Owl. Review notes record retained ambiguous areas.

`subject_area(asset, image)` returns alpha-weighted bird pixels. The renderer
uses that area as the denominator when scaling by detection frequency. It must
call this on the **full prepared RGBA**, before cropping or resizing.
`subject_bounds` and `subject_mask` support placement and bird-only hit testing.
The original artwork pixels are never changed by these helpers.

Missing, stale, resized, invalid or empty geometry explicitly falls back to
the full image alpha; `geometry_status` explains the reason. This avoids stale
coordinates silently clipping a newly replaced drawing.

```sh
.venv/bin/python -m unittest discover -s artwork/geometry -p 'test_*.py'
```
