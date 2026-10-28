# The naturalist edition

A portrait print of the birds heard today: historical illustrations, generous
white space, and optional names, counts and a small dashboard QR code. The
companion runs beside BirdNET-Go. The existing ESP32 image URL and packed
six-color protocol continue to work.

## Try it

From this directory, with Python 3.10 or newer:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py --demo
```

Open `http://127.0.0.1:8001/`. Sample data is clearly labeled and is never
reused in live mode. For the actual station:

```sh
.venv/bin/python server.py --birdnet http://YOUR_BIRDNET_HOST:8080 --bind 0.0.0.0 --timezone America/Denver
```

Keep this service on a trusted LAN. Its controls intentionally have no login;
same-origin checks and a per-process CSRF token prevent cross-site settings
writes, but anyone with access to the dashboard can change the picture.
Do not expose this port directly to the Internet. An authenticated reverse
proxy can provide remote access separately.

For the existing Debian feed container, copy this directory without `.venv/`
or `state/` and run `sh install.sh` as root. It checks artwork hashes and runs
tests, installs into a versioned directory, preserves the existing BirdNET URL
and service settings, backs up the old service/frame, and rolls back if the
new endpoint fails readiness. It installs Debian's Pillow, qrcode, Astral and fonts.
The installer reports the backup path for manual rollback. It does not flash
the ESP32 or change BirdNET-Go.

## Picture controls

Choose a presentation in the dashboard:

- **Naturalist collage** (default): historical drawings arranged by silhouette,
  with frequency-based sizing and all illustrated species heard today.
- **Classic display**: the previous local feed's featured bird and daily summary,
  adapted to portrait or landscape. Photos are the default; Historical drawings
  uses the reviewed catalogue for the same featured species. Names and counts
  remain optional. The daily list includes all birds unless a limit is selected.
- **Field guide**: historical drawings in ordered rows, retaining frequency
  sizing and optional labels.

Select a presentation by its thumbnail under **Make it yours**. All three
thumbnails render the current birds with your draft name, count, title, QR,
orientation, sizing, and illustration settings. They update before you apply
changes; the saved print and physical display stay unchanged until you apply.
Photo and illustration attribution is available under **Preview image credits**.

Classic is based on the earlier local BirdNET adapter. Thomas's published
FederFunk repository supplies firmware, not the original server-side renderer;
this option does not claim to reproduce an unpublished renderer. Photos come
from BirdNET-Go's species image API, with its author and license attribution.
Missing drawings never cause another species to be substituted. A failed photo
request preserves the last successful print and settings.

The dashboard saves presentation, classic photo/drawing choice, names, scientific names, counts, title, QR URL, maximum
birds, orientation, sizing, the update interval, daylight schedule, and illustration choices. Settings,
stats and all three image representations are committed as one atomic bundle.
Changes appear on the web after saving; the frame receives them on its normal
next wake, every 30 minutes by default. A QR URL must be reachable by the
phone scanning it; localhost is usually not suitable. The QR is on by default in the bottom-right corner, with two-pixel modules
and the required four-module white border. Set `DASHBOARD_URL` or
`--dashboard-url` to the station’s LAN address; the generic development
default is localhost. Slightly softened dark modules keep the code discreet;
solid corner markers preserve scan reliability after dithering. Physical
scanning still needs a phone check when changing the QR size or contrast.

The three area scales are:

| Mode | Visible bird area |
| --- | --- |
| Frequency · logarithmic (default) | proportional to `log(1 + detections)` |
| Proportional | proportional to detections |
| Equal | equal for each illustration |

Scale is based on alpha-weighted visible bird ink, with a common resizing
factor for all birds. Reviewed, image-bound polygons exclude branches and
leaves from the sizing calculation; the original drawing stays intact.
See [the geometry notes](artwork/geometry/README.md) for review boundaries and
the explicit full-image fallback when a mask is unavailable or stale.
This is not a measure of a bird's physical size or the number of individual
animals. Integer pixel rounding introduces small differences.
No minimum individual size is imposed; very rare birds may be tiny in linear
mode. Composition packs expanded silhouette masks around asymmetrical anchors
in an oval, with protected spaces for optional labels. Drawings fit around one
another instead of occupying table cells. Stable placement and variant
selection avoid unnecessary refreshes within a day. The default daily drawing
choice is deterministic for each date and species. A fixed automatic choice
is also available, and a manual illustration choice always takes precedence.

## Update interval and power savings

Under Schedule, **Update every** offers 30, 60 or 120 minutes and Custom
(5–720 whole minutes). The project default is 30 minutes; the owner selected
60 minutes for this installation. This is
the display's sleep interval; it does not change the companion's BirdNET polling
interval or the independent battery recorder's polling interval.

The dashboard distinguishes a saved preference from support confirmed by the
device's capability header. Older firmware retains its compiled cadence.
On supported firmware, a saved interval takes effect after the next successful
device update; an already sleeping device cannot receive the change sooner.
Intervals run from the end of a wake cycle, not at fixed wall-clock times.
Longer intervals provide fewer genuine battery measurements. Freshness checks
and forecast gap tolerances follow the last interval offered to a supporting
device, rather than a dashboard edit it has not received yet.

The image response supplies `X-FederFunk-Refresh-Seconds` on both 200 and 304.
The firmware accepts it only after validating and handling that response.
Missing/invalid values on successful updates use the compiled 30-minute default.
Consecutive failed wake cycles retry after 30, 60, then 120 minutes; success or
a valid schedule hold resets this backoff.
An overnight `X-FederFunk-Hold-Seconds` takes precedence. Timing-only edits
preserve the exact artwork, color rendering, image ETag and daily archive.
See the [interval protocol](../docs/refresh-interval.md) and
[power policy](../docs/power-policy.md) for verification details.

**Meaningful changes** can hold the published image between substantial changes
while the dashboard continues receiving fresh counts. New species, changed
artwork/settings or visible labels publish a new print. The default size trigger
is a 10% relative change in a bird's share of total bird-only area; small changes
accumulate against the last published print. A three-hour maximum hold provides
a further update even without a size trigger. Both limits are adjustable.
The regular check-in interval and overnight schedule still apply, so publication
does not wake a sleeping frame. The default mode remains **Every check**.

**Pause below (V)** defaults to 3.30 V and accepts 3.20–3.60 V in 0.05 V steps.
Supporting integrated-board firmware persists the cutoff and requires two
valid readings at least 0.15 V above it to resume. Low or invalid battery
readings pause image and badge updates, with battery-only checks every 30
minutes and no Wi-Fi connection. Charging does not bypass the voltage guard.
The dashboard shows the cutoff last reported by the device separately from a
newly saved preference. It cannot confirm a new pause while the radio is off.
This reduces load; it does not physically disconnect or protect the cell from
continued standby draw.

## Daylight schedule

The default location is ZIP 84105's approximate center (40.7372, -111.8581),
America/Denver. Updates run from sunrise through one hour after sunset.
The dashboard can disable the pause, choose fixed start/end times, adjust
solar offsets by up to three hours either way, or change coordinates/timezone.
Astral calculates sunrise and sunset offline; no external geocoding call is
needed at runtime. Times account for daylight saving, but not mountain shadows.

During the pause, the service preserves the last image and its observations,
including across midnight and restart. Settings saved overnight are queued
for the next active window. Normal polling resumes in the morning. The pause
does not delete or reclassify BirdNET detections; the next active daily summary
still includes that day's qualifying detections from before sunrise.

Supported firmware honors `X-FederFunk-Hold-Seconds` on the image response,
leaves even the local battery badge unchanged, and sleeps until the next
window. Older firmware keeps fetching the same frozen image every 30 minutes
and may still redraw its local battery badge. A firmware update is needed for
the full overnight sleep behavior. A sleeping frame cannot receive a changed
schedule until it next wakes or is manually reset. Network failure before
receiving a hold preserves the existing retry and battery-warning behavior.

BirdNET's daily analytics are requested for the station timezone with
`min_confidence=80` by default (80%). This filters individual detections, not
just each species' highest confidence. Species without art remain visible in
the statistics; another species is never substituted. All species with available art appear by default. The optional bird limit
selects the 1–12 most-heard species only when “All birds heard today” is off. An empty day shows
“Listening for visitors”. Fetch failures retain the last successful print and
show a stale-data notice in the dashboard.

## Artwork collection

See [artwork/README.md](artwork/README.md) and `artwork/manifest.json` for
species, variants, original scans, creators, historical dates, rights evidence,
processing steps and checksums. These are historical illustrations, not
generated birds or photos. Adding a second asset with the same scientific
name makes it available in the dashboard's illustration selector. Keep exact
species identification, provenance and rights information when extending the
collection. The manifest is checked on service startup; restart after adding
assets. Unknown or mismatched variant IDs cannot be saved.

Source records, artists, dates and rights stay on the dashboard with clickable
archive, original-scan and license links. They are also embedded in exported
PNG metadata. No credits or references are printed on the e-paper artwork;
its QR code leads to the dashboard's full attribution.

The dashboard's image library compares the entire BirdNET species history
with exact scientific-name matches, shows every reviewed drawing, and lists
missing illustrations in detection-count order. Coverage counts all reported
labels, including low-confidence records; a BirdNET label is not a verified
sighting. Suspect records are flagged for review, not silently substituted
with a similar species. Historical totals remain on the station, outside Git.
The library audit refreshes at most once an hour.

Every displayed asset must have a recorded visual review of species,
orientation and cropping. Original scans, contour recipes and checksums make
the preparation reproducible. Missing artwork never becomes a placeholder
photo in the print. Additional exact-species variants can be added to the
same catalogue.

### Optional collections

The dashboard links to optional, reviewed Fugleramme packs. The original pack
contains nine drawings, including six species absent from the bundled library.
The North American expansion adds 30 single-bird drawings selected against the
station's refreshed all-history BirdNET-Go list. Registries contain provenance,
review notes and hashes only. No upstream image files are bundled or added to
this repository. Users explicitly download the allowlisted images to an
external cache, or import them from their own upstream checkout:

```sh
.venv/bin/python artwork_packs.py fugleramme-classic-reviewed
.venv/bin/python artwork_packs.py fugleramme-north-america-reviewed
```

The default cache is `~/.cache/federfunk/artwork-packs`. Use
`FEDERFUNK_ARTWORK_PACK_CACHE` or the server's `--artwork-packs` to select a
shared persistent cache when the service runs as a different user. Restart
the companion after installing. Only installed, verified and reviewed assets
participate in daily selection. The dashboard never executes downloads.
The Debian installer defaults the service to `/var/lib/federfunk-artwork-packs`;
use that location with the download command's `--cache-root` option. The
existing service environment file can override `FEDERFUNK_ARTWORK_PACK_CACHE`.

Prepared Fugleramme images retain **CC BY-SA 4.0** terms, even though they are
downloaded separately. Attribution, source links and modification notices stay
with each image. Composed prints using that artwork carry compatible print
rights in their dashboard credits and exported PNG metadata; no visible
copyright footer is added to the e-paper. See [pack instructions and terms](artwork/packs/README.md).

A second optional pack, `audubon-public-domain-reviewed`, adds two Pine Siskin
and three Sora variants from public-domain original plates. It stores only
reviewed contour recipes and source records in Git; scans and prepared PNGs
remain in the external cache. Install it with
`.venv/bin/python artwork_packs.py audubon-public-domain-reviewed`.
The original poses and pigment are preserved, with no reconstructed anatomy.
The cones held by the Pine Siskins are excluded from bird-area sizing.

## Field notes and daily archive

Select a bird in the picture, or use its accessible button or species-list
link, to see counts, first/last detection dates, the illustration's sources,
and links to individual BirdNET recordings and spectrograms. Automated
identifications are not confirmed sightings. Recording links open the local
BirdNET instance; audio is not copied into FederFunk.

The archive automatically saves the latest successful active-window print for each station
date, with its original observations, selected credits, full-color artwork,
six-color preview and packed frame. Today is replaced atomically as it grows;
previous days are retained. Overnight holds do not create new prints. The
archive begins when this version runs; it does not invent earlier history.
Meaningful-change mode still archives the fresh daily composition and its
matching observations on successful active-window polls, even while the main
display image is held. Held-image details use that image's original observations
and credits; live dashboard totals continue advancing.
A saved composition is not evidence that the physical screen refreshed.
Back up the companion's state directory to preserve both prints and battery
history. Prints are retained without automatic deletion.

**Download** downloads the displayed image to your computer; it does not
create an archive entry. Daily archiving needs no button press. Each date has
one evolving print, not a separate permanent snapshot of every refresh or
settings change. A separate localhost preview needs a consistent snapshot of
the archive database as well as `current.zip` to show the station's existing
daily history.

## Battery journal and bench recording

The battery journal records valid reports received during normal frame wakes,
with percentage, voltage and charging state. Repeated cached readings do not
become new measurements. History persists in the state directory and can be
downloaded as CSV. A voltage-based remaining-time range appears only after a
sufficient, fresh, consistently discharging run (at least eight samples over
six hours with a meaningful decline). Charging, gaps and uncertain reports
withhold or restart the estimate. It cannot predict an exact shutdown time.
The database retains up to 100,000 readings.

For a bench test without replacing an existing feed, `battery_capture.py` is a
standalone, standard-library recorder. Run it as a supervised service on the
home server, using the existing dashboard's cached state endpoint:

```sh
python3 battery_capture.py --output /var/lib/federfunk-battery-test/capture.jsonl \
  --start 2026-10-14T21:00:00Z --hours 96 --interval 60
```

Replace the example start time with the actual test start, including its UTC
offset, and keep that same value when restarting. It reads localhost port 8001
by default. It never contacts or wakes the frame.
Its durable JSONL log distinguishes fresh measurements, stale cached readings,
service errors and polling gaps. On restart it resumes the same test without
duplicating readings. The default window ends after 96 hours. For a discharge
test that should keep logging until explicitly stopped, replace `--hours 96`
with `--until-stopped`. Keep the original `--start` and output file: the
recorder appends a window-change event and preserves all previous records.
It does not stop automatically on silence, because expected overnight sleep
and network failures cannot establish when a battery becomes empty. The
32 MB log limit still applies. A service with automatic
restart can continue independently of a laptop, browser or chat. The home
server and local Wi-Fi must remain powered; Internet access is not required.
Long overnight gaps are expected when solar sleep is enabled.

Pass `--battery-capture /path/to/capture.jsonl` to the companion to import a
recorder log idempotently. It uses actual report timestamps, not polling times.

## Color studies and isolated previews

**Current colors remain the default.** The comparison dialog offers two
experimental palette-compensated previews using fine Floyd–Steinberg or wider
Stucki diffusion. They use a reference pigment palette, retain neutral ink and
QR structure, and do not stack another saturation boost on the current preset.
See [the color implementation and reference](color_profiles.py). The comparison
endpoints cannot apply a profile or change the live frame. A physical-panel
comparison is still required: screen RGB swatches cannot simulate its pigments.

To review this release independently, use a new state directory and port:

```sh
.venv/bin/python server.py --birdnet http://YOUR_BIRDNET_HOST:8080 \
  --bind 127.0.0.1 --port 8877 --state state/local-preview --preview-only
```

This labels the dashboard as a local preview. Keep the frame pointed at its
existing feed and do not share the production state directory. Local settings
and previews are independent of the deployed service.

## Display and endpoints

| Endpoint | Purpose |
| --- | --- |
| `/` | Dashboard and persistent print controls |
| `/art.png` | Full-color artwork, portrait by default |
| `/preview.png` | Same composition quantized to the six display colors |
| `/api/v1/display/current_large.bin` | Existing 960,000-byte firmware endpoint |
| `/api/state` | Settings, observations, availability, frame battery, last poll and errors |
| `/api/settings` | Settings and CSRF token; POST applies validated changes |
| `/api/library` | All-history coverage, missing species and review records |
| `/api/birds?species=SCIENTIFIC_NAME&date=YYYY-MM-DD` | Bird details and recording links |
| `/api/archive` | Saved daily prints and download links |
| `/archive/YYYY-MM-DD/download.zip` | A saved print with its field notes and credits |
| `/api/battery?days=7` | Persistent reports and a cautious remaining-time range |
| `/api/battery.csv?days=30` | Battery history download |
| `/api/color-previews` | Read-only, revision-bound color comparisons |
| `/api/presentation-previews` | POST draft settings for read-only layout thumbnails; same-origin and CSRF checks apply |
| `/api/artwork-packs` | Optional collection links and installation status |
| `/credits` | Artwork sources and rights metadata |
| `/health` | Readiness, frame size, error status and content ETag |

Portrait art is 1200×1600. The wire image remains 1600×1200, high nibble first,
using only codes 0,1,2,3,5,6. The server rotates portrait art counterclockwise
once to cancel the driver's existing clockwise address rotation. Tests check
native corners and controller seam pixels. Do not change the driver rotation.
The installed panel's upright portrait orientation was confirmed by its owner.

The firmware adds a small red battery warning only below 15%; that badge is
not in the web preview. The existing landscape badge placement maps to the
portrait upper-left edge, which the print reserves. Charging never appears
on the artwork. Updated firmware reports battery percentage, voltage and
charging state with its existing image request; the dashboard shows when
those readings were last received, with no additional frame wakeups. Older
firmware supplies no battery readings. Percentages are voltage estimates.

E-paper output lifts brightness and saturation in colored feathers before
six-color dithering, reducing black in colored areas. Near-neutral details,
white paper and the QR code retain their original tones.
Source scans and full-color web art stay unchanged; the six-color preview
shows the boosted output. Actual e-paper pigments still differ from screen
colors, so final color judgment needs the physical panel.

The 7.3-inch firmware remains supported by the parent project, but this
companion's output currently targets only the 13.3-inch display.

## Verification

```sh
.venv/bin/python -m unittest discover -v -p 'test_*.py'
.venv/bin/python -m unittest discover -s artwork/packs -p 'test_*.py'
.venv/bin/python -m unittest discover -s artwork/geometry -p 'test_*.py'
.venv/bin/python artwork/verify.py
```

Checks cover palette/nibble packing, portrait rotation, sizing mathematics,
validated variant binding, confidence queries, missing artwork, empty days,
failure retention, atomic settings recovery, ETags, CSRF and file access.

For the separate firmware sleep-current improvement, see
[sleep-power.md](../docs/sleep-power.md). Build success does not establish
electrical power savings; a battery-only current measurement is required.
