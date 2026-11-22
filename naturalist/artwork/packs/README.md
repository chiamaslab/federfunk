# Optional reviewed artwork packs

`registry.json` is a small reviewed allowlist, not an automatic import of an
upstream collection. The first pack contains nine individual birds from
Fugleramme, pinned to commit `33b943b41222b980f7bde913f3453028ea401fe7`:
Common Nighthawk, Common Raven, Steller's Jay, Belted Kingfisher, House Sparrow,
Cooper's Hawk, Northern Flicker, House Finch and Western Tanager.

Each cutout was compared on white against its complete historical source plate
on 2026-10-14. Review records identify the figure, pose, retained perch, known
limitations, source-file SHA-256 and decoded RGBA SHA-256. The upstream images
contain existing digital restoration and pale paper halos. This implementation
does not paint, generate, erase or reconstruct anatomy. Paired Peregrine Falcon,
Mourning Dove and White-breasted Nuthatch images were reviewed and excluded from
this pack because one entry should represent one bird silhouette. The Northern
Flicker depicts the yellow-shafted form, without asserting local subspecies.

Upstream pack images are cached outside this repository. Install explicitly from the
`naturalist` directory:

```sh
.venv/bin/python artwork_packs.py fugleramme-classic-reviewed
```

The default cache is `~/.cache/federfunk/artwork-packs`; override with
`FEDERFUNK_ARTWORK_PACK_CACHE` or `--cache-root /tmp/federfunk-artwork-packs`.
The renderer and dashboard must use the same cache root. Installation opts in;
remove the pack's external cache directory to uninstall it. The dashboard does
not run downloads. A missing pack simply adds no variants; corrupt entries are
not selectable and the status reports a partial or missing installation.

An existing upstream checkout may be used with `--source-directory PATH`.
Only the nine registered bird image paths are read. No upstream program,
configuration or install script runs. The downloader accepts a fixed GitHub
host and exact commit, refuses redirects, limits each download to 8 MB and each
decoded image to 8 million pixels, and validates format, dimensions and hashes.
Verified WebP pixels are converted losslessly to PNG with credit metadata and
written atomically outside the repository. The source-file hash binds the
download; the RGBA hash binds the resulting PNG independent of encoder version.

## Attribution and reuse

The historical originals and the prepared cutouts have distinct rights.
Fugleramme offers these edited cutouts under **CC BY-SA 4.0**. Keeping their
files outside this repository does not remove attribution or share-alike
requirements. Per-image records retain the artist, digitizer/institution,
Fugleramme editor credit, source and license links, and change notices. The
Audubon courtesy credit and rawpixel credit are retained where applicable.
Credits belong with the dashboard and exported artwork metadata. Distributing
an adapted image must preserve the applicable CC BY-SA terms; this does not
change the license of unrelated application code.

Sources: [pinned upstream attribution](https://github.com/arnegiacomo/fugleramme/blob/33b943b41222b980f7bde913f3453028ea401fe7/assets/artwork/classic/ATTRIBUTION.md),
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

## Audubon source-plate cutouts

`audubon-public-domain-reviewed.json` is a separate optional collection made
directly from two public-domain University of Pittsburgh scans on Wikimedia
Commons. Plate 180 supplies two Pine Siskins (historically "Pine Finch");
plate 233 supplies three Soras. These are our own traced excerpts from the
original scans, with source identification and individual visual reviews.
They do not import Fugleramme's edited artwork or inherit its editing credits.

```sh
.venv/bin/python artwork_packs.py audubon-public-domain-reviewed
```

Only provenance and vector contours are stored in Git. The installer downloads
the two hash-pinned original JPEG plates into the external cache, then applies
`audubon-contours.json` using `contour_artwork.py`. It changes alpha, bounding
crop and final image size only. The original pigments, poses and visible
anatomy remain; hidden feathers or toes are not reconstructed. The Pine
Siskins retain the cones held by their toes. Bird-only sizing excludes those
cones and any retained ground or vegetation.

For an existing download, `--source-directory PATH` reads `8756366.jpg` and
`8775325.jpg`. The originals total about 117 MB; allow at least 1 GB of available
memory while preparing them. The installer bounds downloads to 80 MB per source
and 200 million pixels, verifies source hashes and dimensions, and checks each
result against its reviewed RGBA hash before publishing. Normal dashboard
operation reads only the small prepared PNGs and never downloads the plates.

Figure labels follow the original captions: Pine Siskin upper female #2 and
lower male #1; Sora left female #2, centre male #1 and right young #3. Choosing
one of these variants makes no claim about the sex or age of a recorded bird.
Daily selection remains stable for a date; an explicit dashboard choice wins.
Known source occlusions are recorded with each variant, including the female
Sora's rear outline behind the male's tail.

Source and rights records: [Pine Finch, plate 180](https://commons.wikimedia.org/wiki/File:180_Pine_Finch.jpg),
[Sora or Rail, plate 233](https://commons.wikimedia.org/wiki/File:233_Sora_or_Rail.jpg).
Credits remain in the dashboard and PNG metadata, without a footer on the print.

## Reviewed North American expansion

`fugleramme-north-america-reviewed.json` adds 30 exact-name species selected
from the station's all-history BirdNET-Go summary. It is pinned to Fugleramme
commit `4c41f772a0574e4348f6ff722b1acf97d536fc8b`. Each decoded image is bound by
both its source-file and RGBA pixel hashes and retains its historical source
link, artist group, attribution, rights and dated review.

The admitted choices were inspected individually at full size and together on
paper. Alternate files containing multiple birds were rejected; the pack uses
single-bird alternatives for Barn Swallow, Common Loon, Pine Grosbeak, Bushtit,
Greater White-fronted Goose and Peregrine Falcon. Mourning Dove, Say's Phoebe
and White-throated Sparrow remain missing because their reviewed upstream
choices still contain multiple birds. American Barn Owl, Green-winged Teal and
Mangrove Yellow Warbler remain taxonomy holds rather than artwork substitutions.

Install explicitly from the `naturalist` directory:

```sh
.venv/bin/python artwork_packs.py fugleramme-north-america-reviewed
```

The prepared cutouts are Fugleramme edits licensed CC BY-SA 4.0. Historical
source works and institutions are recorded per image; the pack-level source and
attribution links are pinned to the same upstream commit.

## Bird-only adaptations

The owner requested removal of the supports/scenery from Brown Creeper,
Greater White-fronted Goose and Ring-necked Pheasant on 2026-10-27.
`support-removals-reviewed.json` records those three PNG adaptations in
`edits/`, including the exact edit prompts, original source/pixel hashes,
output file/pixel hashes, dated visual reviews and CC BY-SA change notices.
The built-in image editor removed the creeper's trunk, the goose's rock/ground,
and the pheasant's stump, surrounding plants and background female. These are
AI-assisted edits; exact source pigments and recreated edge details are not
represented as untouched historical artwork.

Installing the original source pack still opts in. The read path verifies each
unchanged source image and then uses its reviewed adaptation under the same
stable asset ID, preserving saved artwork selections and catalogue counts.
Unrelated images remain unchanged. The original cache is retained, and a
missing, corrupt or mismatched adaptation is excluded from selection. The
dashboard and exported artwork credits disclose the editing method and retain
the original artist, source and share-alike terms.

The earlier pheasant source review was incorrect: it is left-facing and its
source contains a background female. The source record now states that, and
the displayed adaptation contains the foreground male alone.

## Flying-bird illustrations

`habird-flight-reviewed.json` adds eleven native transparent flight poses from
the AvianVisitors / HABirdDashboard illustration family matched in the owner's
reference photo: Black-capped Chickadee, Red-breasted Nuthatch, House Finch,
Song Sparrow, Canyon Wren, Common Raven, American Crow, Great Horned Owl,
Hermit Thrush, Mourning Dove and Cooper's Hawk. These are **upstream
AI-generated Japanese-print-style illustrations**, not historical plates.
The existing drawings and the owner's selection mode remain available.

The selected source is HABirdDashboard commit
`3f3e2e78aa6537a58a51df05ac4b34dcdb118733`. Its README explicitly licenses both
code and artwork under MIT with the agreement of Teddy Warner and contributors.
The separate AvianVisitors repository still states CC BY-NC-SA; this pack uses
the explicitly MIT-licensed HABirdDashboard distribution. Each entry includes
the pinned source, attribution, full MIT copyright/permission notice, file
and RGBA hashes, and a dated visual review. Native color and alpha pixels are
unchanged; the installer adds attribution/licence metadata only. No upstream
code, species-label list, taxonomy table or generation pipeline is imported.

```sh
.venv/bin/python artwork_packs.py habird-flight-reviewed
```

Scientific names match the station's reported labels. Flight and sex variants
are artistic choices, never conclusions drawn from detection confidence.
The reviewed Mountain Chickadee candidate was rejected because it lacked the
diagnostic white eyebrow. This is an eleven-figure review, not approval of the
whole upstream collection. Only reviewed native PNGs with transparent alpha
are admitted; source images stay in the external pack cache.

Source and terms: [pinned artwork](https://github.com/adamoberley/HABirdDashboard/tree/3f3e2e78aa6537a58a51df05ac4b34dcdb118733/avian/assets/illustrations),
[credits and artwork licence](https://github.com/adamoberley/HABirdDashboard/blob/3f3e2e78aa6537a58a51df05ac4b34dcdb118733/README.md#credits--license),
[MIT notice](https://github.com/adamoberley/HABirdDashboard/blob/3f3e2e78aa6537a58a51df05ac4b34dcdb118733/LICENSE).

## Salt Lake area bats

`salt-lake-bats-reviewed.json` adds Little Brown Bat (*Myotis lucifugus*),
Hoary Bat (*Lasiurus cinereus*), Big Brown Bat (*Eptesicus fuscus*), and
Mexican/Brazilian Free-tailed Bat (*Tadarida brasiliensis*). These species are
documented in the [Antelope Island mammal list](https://stateparks.utah.gov/parks/antelope-island/park-mammals/).
Artwork availability does not add a detection or change the station's labels.
Only an exact scientific-name match selects an image.

```sh
.venv/bin/python artwork_packs.py salt-lake-bats-reviewed
```

The four prepared PNGs in `edits/bats/` are **AI-assisted extractions from
public-domain historical illustrations**, made with the built-in image editor
on 2026-11-08. They are not untouched source-pixel crops. Each record stores
the complete edit prompt, source URL and SHA-256, prepared file and RGBA
hashes, taxonomy evidence, limitations, and a dated individual visual review.
The installer verifies all four local files before publishing any, embeds
their credits in the external cache, and performs no network download.
The content revision is SHA-256 of the ordered prepared-file hashes.

Seton's 1898 *Four-footed Americans and their kin* supplies the Little Brown
Bat. De Kay's 1842 *Zoology of New York*, plate 2, supplies the Hoary Bat
(lower figure 2, historically *Vespertilio pruinosus*) and Big Brown Bat
(upper figure 1, historically the Carolina Bat, *V. carolinensis*). The
historical synonym mappings have separate museum/species-account evidence.
Both De Kay figures retain the source's foreshortened, fading right wing;
missing portions were not extended. The complete *Nyctinomus brasiliensis*
figure in University of Amsterdam's Iconographia Zoologica scan
`UBA01 IZ20800059` supplies the free-tailed bat, with both wing tips and its
projecting tail. Separate head/skull diagrams are excluded.

A Fuertes candidate captioned "Hoary Bat" was rejected: the 1918 book's
erratum explicitly identifies it as Little Brown Bat. A clipped 1847
free-tailed figure and an overlapping Fuertes group were also rejected.
The original downloaded scans are retained in the ignored preparation
archive; their institutional URLs and exact hashes remain in the registry.
The daily direction option and Original/Mirrored overrides also apply to
these illustrations. No bat recording or historical print is synthesized.

## API and checks

`load_pack_assets(cache_root)` returns verified installed assets only;
`asset_path(asset, bundled_dir)` resolves either kind of image;
`public_asset(asset)` removes private filesystem paths before serialization;
`packs_status(cache_root)` returns dashboard-safe installation information.
None of these read APIs downloads files.

```sh
.venv/bin/python -m unittest discover -s artwork/packs -p 'test_*.py'
```
