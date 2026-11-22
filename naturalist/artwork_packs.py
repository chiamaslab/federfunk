"""Optional, pinned reviewed artwork; downloads are explicit and outside Git.

Only small reviewed allowlists in artwork/packs are accepted. Upstream code
is never imported or executed. Verified WebP pixels or local contour recipes
produce PNGs in the external cache, keeping original attribution.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import urllib.request
from urllib.parse import urlsplit

from PIL import Image

REGISTRY = Path(__file__).parent / "artwork" / "packs" / "registry.json"
AUDUBON_REGISTRY = REGISTRY.with_name("audubon-public-domain-reviewed.json")
NORTH_AMERICA_REGISTRY = REGISTRY.with_name("fugleramme-north-america-reviewed.json")
HABIRD_REGISTRY = REGISTRY.with_name("habird-flight-reviewed.json")
BATS_REGISTRY = REGISTRY.with_name("salt-lake-bats-reviewed.json")
SUPPORT_REMOVALS = REGISTRY.with_name("support-removals-reviewed.json")
MAX_BYTES = 8_000_000
MAX_PIXELS = 8_000_000
MAX_SOURCE_BYTES = 80_000_000
MAX_SOURCE_PIXELS = 200_000_000
MAX_RECIPE_BYTES = 2_000_000
AUDUBON_KIND = "audubon-contour-v1"
HABIRD_KIND = "habird-flight-png-v1"
BATS_KIND = "reviewed-local-png-v1"


def _habird_pack():
    """Expand a small native-PNG allowlist from the photo-matched collection."""
    if not HABIRD_REGISTRY.is_file():
        return None
    if HABIRD_REGISTRY.stat().st_size > MAX_RECIPE_BYTES:
        raise ValueError("Artwork registry exceeds its size limit")
    data = json.loads(HABIRD_REGISTRY.read_text())
    assets, defaults = data.pop("assets", None), data.pop("review_defaults", None)
    if (data.pop("schema_version", None) != 1 or data.get("id") != "habird-flight-reviewed"
            or data.get("kind") != HABIRD_KIND or not isinstance(defaults, dict)
            or not isinstance(assets, list) or not 1 <= len(assets) <= 32):
        raise ValueError("Unexpected flight artwork registry")
    revision = data.get("revision", "")
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Flight artwork is not pinned to a commit")
    expanded = []
    for compact in assets:
        slug = compact.get("slug", "")
        if (not slug.endswith("-2")
                or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in slug)):
            raise ValueError("Invalid flight artwork slug")
        relative = f"avian/assets/illustrations/{slug}.png"
        source = f"https://github.com/adamoberley/HABirdDashboard/blob/{revision}/{relative}"
        expanded.append({
            "id": f"habird-flight-{slug}",
            "scientific_name": compact["scientific_name"], "common_name": compact["common_name"],
            "title": "AvianVisitors / HABirdDashboard · flight illustration",
            "variant": "In flight · Japanese print style (AI-generated)",
            "creator": data["creator"], "date": "2026", "artwork_kind": "ai-generated", "pose": "flight",
            "source_url": source, "upstream_url": source, "upstream_file": relative,
            "source_sha256": compact["source_sha256"], "pixel_sha256": compact["pixel_sha256"],
            "width": compact["width"], "height": compact["height"],
            "rights": data["rights"], "rights_url": data["rights_url"],
            "license_notice": data["license_notice"], "attribution": data["attribution"],
            "attribution_url": data["attribution_url"], "changes": data["changes"],
            "modifications": data["changes"],
            "subject_geometry": {
                "pixel_sha256": compact["pixel_sha256"], "size": [compact["width"], compact["height"]],
                "polygons": [], "exclude": [],
                "review": {"status": "reviewed", "date": defaults["date"],
                           "scope": "Reviewed bird-only native alpha; no perch, ground or extra bird."},
            },
            "review": {
                "status": "reviewed", "date": defaults["date"],
                "species_check": compact["species_review"], "orientation": compact["figure_review"],
                "crop": "Native PNG alpha composited on paper and inspected at full size: one complete flying bird, no support or adjacent bird. Source colors, orientation and alpha are unchanged.",
                "scope": defaults["scope"],
                "reviewed_source_sha256": compact["source_sha256"],
                "reviewed_pixel_sha256": compact["pixel_sha256"],
            },
        })
    data["assets"] = expanded
    return data


def cache_directory(cache_root=None):
    return Path(cache_root or os.environ.get("FEDERFUNK_ARTWORK_PACK_CACHE") or Path.home() / ".cache/federfunk/artwork-packs").expanduser().resolve()


def _north_america_pack():
    """Expand the compact, reviewed North American registry into pack entries.

    Repeated rights, attribution and review declarations live at pack/source
    level in the JSON.  Runtime entries remain identical to the original
    Fugleramme allowlist and pass through the same hash and pixel checks.
    """
    if not NORTH_AMERICA_REGISTRY.is_file():
        return None
    if NORTH_AMERICA_REGISTRY.stat().st_size > MAX_RECIPE_BYTES:
        raise ValueError("Artwork registry exceeds its size limit")
    data = json.loads(NORTH_AMERICA_REGISTRY.read_text())
    assets, sources = data.pop("assets", None), data.pop("sources", None)
    review_defaults = data.pop("review_defaults", None)
    if (data.pop("schema_version", None) != 1 or not isinstance(assets, list)
            or not 1 <= len(assets) <= 32 or not isinstance(sources, dict)
            or not isinstance(review_defaults, dict)):
        raise ValueError("Unexpected North American artwork registry")
    revision = data.get("revision", "")
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("North American artwork is not pinned to a commit")
    expanded = []
    for compact in assets:
        source = sources.get(compact.get("source_key"))
        slug = compact.get("slug", "")
        if (not isinstance(source, dict) or not slug
                or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in slug)):
            raise ValueError("Invalid North American artwork entry")
        upstream_file = f"assets/artwork/classic/birds/{slug}.webp"
        changes = data["changes"]
        attribution = (f"{source['attribution']}; historical plate cut and digital restoration by "
                       "Fugleramme / Arne Giacomo Munthe-Kaas. Prepared artwork: CC BY-SA 4.0.")
        figure = compact["figure_review"]
        entry = {
            "id": f"fugleramme-na-{slug}",
            "scientific_name": compact["scientific_name"],
            "common_name": compact["common_name"],
            "title": f"{source['title']} · Fugleramme",
            "variant": f"{source['title']} · single bird",
            "creator": source["creator"],
            "date": source["date"],
            "source_url": compact["source_url"],
            "upstream_url": f"https://github.com/arnegiacomo/fugleramme/blob/{revision}/{upstream_file}",
            "upstream_file": upstream_file,
            "source_sha256": compact["source_sha256"],
            "pixel_sha256": compact["pixel_sha256"],
            "width": compact["width"],
            "height": compact["height"],
            "rights": data["rights"],
            "rights_url": data["rights_url"],
            "attribution": attribution,
            "attribution_url": data["attribution_url"],
            "changes": changes,
            "modifications": changes,
            "review": {
                "status": "reviewed",
                "date": review_defaults["date"],
                "species_check": (f"Pinned upstream manifest at {revision} maps {upstream_file} to "
                                  f"{compact['scientific_name']}; the linked historical source and "
                                  "prepared figure were checked for the reported species."),
                "orientation": figure,
                "crop": compact.get("crop_review", (
                    f"{figure} Paper-composited transparent pixels were reviewed at full size; "
                    "exactly one bird is visible and no adjacent bird is retained.")),
                "scope": review_defaults["scope"],
                "reviewed_source_sha256": compact["source_sha256"],
                "reviewed_pixel_sha256": compact["pixel_sha256"],
            },
        }
        expanded.append(entry)
    data["assets"] = expanded
    return data


def registry():
    packs = json.loads(REGISTRY.read_text())["packs"]
    north_america = _north_america_pack()
    return [*packs, north_america] if north_america else packs


def registered_packs():
    """Keep the original upstream registry separate from our optional recipes."""
    packs = registry()
    if AUDUBON_REGISTRY.is_file():
        if AUDUBON_REGISTRY.stat().st_size > MAX_RECIPE_BYTES:
            raise ValueError("Artwork registry exceeds its size limit")
        pack = json.loads(AUDUBON_REGISTRY.read_text())
        if pack.get("id") != "audubon-public-domain-reviewed" or pack.get("kind") != AUDUBON_KIND:
            raise ValueError("Unexpected Audubon artwork registry")
        packs = [*packs, pack]
    flight = _habird_pack()
    if flight is not None:
        packs = [*packs, flight]
    if BATS_REGISTRY.is_file():
        if BATS_REGISTRY.stat().st_size > MAX_RECIPE_BYTES:
            raise ValueError("Bat artwork registry exceeds its size limit")
        pack = json.loads(BATS_REGISTRY.read_text())
        if (pack.get("schema_version") != 1 or pack.get("id") != "salt-lake-bats-reviewed"
                or pack.get("kind") != BATS_KIND):
            raise ValueError("Unexpected bat artwork registry")
        packs = [*packs, pack]
    return packs


def packs_status(cache_root=None):
    """Public registry/install status, with no local cache paths."""
    installed = load_pack_assets(cache_root)
    result = []
    for pack in registered_packs():
        count = sum(a["pack_id"] == pack["id"] for a in installed)
        total = len(pack["assets"])
        result.append({
            "id": pack["id"], "title": pack["title"], "revision": pack["revision"],
            "description": pack.get("description", "Individually reviewed historical cutouts; downloaded outside the bundled repository."),
            "source_url": pack["source_url"], "license": pack["rights"], "license_url": pack["rights_url"],
            "rights": pack["rights"], "rights_url": pack["rights_url"],
            "attribution": pack.get("attribution", "Historical artists and source institutions credited per image; prepared cutouts by Fugleramme / Arne Giacomo Munthe-Kaas."),
            "attribution_url": pack["attribution_url"], "installed_count": count, "reviewed_count": total,
            "status": "installed" if count == total else "partial" if count else "not-installed",
            "install_command": f"python artwork_packs.py {pack['id']}",
        })
    return result


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _safe(root, relative):
    rel = PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise ValueError("Unsafe artwork path")
    path = (root / str(rel)).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Artwork path escapes its directory")
    return path


def _cache_path(root, pack, entry):
    return _safe(root, f"{pack['id']}/{pack['revision']}/{entry['id']}.png")


def _cache_parent(root, output):
    ancestors = [root]
    current = root
    for part in output.parent.relative_to(root).parts:
        current = current / part
        ancestors.append(current)
    missing = [directory for directory in ancestors if not directory.exists()]
    output.parent.mkdir(parents=True, exist_ok=True)
    for directory in missing:
        directory.chmod(0o755)


def _image(data, entry, expected_format):
    if len(data) > MAX_BYTES:
        raise ValueError("Artwork exceeds download limit")
    with Image.open(io.BytesIO(data)) as source:
        if source.format != expected_format or source.width * source.height > MAX_PIXELS:
            raise ValueError("Unexpected artwork format or dimensions")
        if list(source.size) != [entry["width"], entry["height"]]:
            raise ValueError("Artwork dimensions do not match reviewed image")
        image = source.convert("RGBA")
    if _digest(image.tobytes()) != entry["pixel_sha256"]:
        raise ValueError("Artwork pixels do not match reviewed image")
    return image


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Artwork download redirects are not allowed")


def _audubon_recipes(pack):
    """Read only committed JSON data, pinned to its full SHA-256 digest."""
    digest = pack.get("recipe_sha256", "")
    if (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
            or pack.get("revision") != digest):
        raise ValueError("Audubon recipe is not pinned to an exact revision")
    relative = pack.get("recipe_file", "")
    if not isinstance(relative, str) or not relative.endswith(".json"):
        raise ValueError("Expected a local JSON contour recipe")
    path = _safe(AUDUBON_REGISTRY.parent, relative)
    with path.open("rb") as source:
        data = source.read(MAX_RECIPE_BYTES + 1)
    if len(data) > MAX_RECIPE_BYTES or _digest(data) != digest:
        raise ValueError("Contour recipe hash mismatch or size limit exceeded")
    decoded = json.loads(data)
    entries = decoded.get("assets")
    if decoded.get("schema_version") != 1 or not isinstance(entries, list) or not 1 <= len(entries) <= 32:
        raise ValueError("Unexpected contour recipe schema")
    recipes = {entry["id"]: entry for entry in entries}
    if len(recipes) != len(entries):
        raise ValueError("Duplicate contour recipe id")
    return recipes


def _jpeg_dimensions(path):
    """Read bounded JPEG headers without disabling Pillow's global bomb guard.

    The two trusted scans exceed Pillow's default source-size guard. Inspect
    their SOF dimensions before the contour builder opens/crops those scans.
    """
    with path.open("rb") as source:
        if source.read(2) != b"\xff\xd8":
            raise ValueError("Expected a JPEG source plate")
        while source.tell() < 1_000_000:
            if source.read(1) != b"\xff":
                raise ValueError("Invalid JPEG header")
            marker = source.read(1)
            while marker == b"\xff":
                marker = source.read(1)
            if not marker or marker in (b"\xda", b"\xd9", b"\x00"):
                break
            value = marker[0]
            if value == 0x01 or 0xd0 <= value <= 0xd7:
                continue
            encoded = source.read(2)
            if len(encoded) != 2:
                break
            length = int.from_bytes(encoded, "big")
            if length < 2:
                break
            if value in (0xc0, 0xc1, 0xc2):  # Huffman baseline/extended/progressive.
                header = source.read(5)
                if len(header) != 5 or length < 8 or header[0] != 8:
                    break
                height, width = int.from_bytes(header[1:3], "big"), int.from_bytes(header[3:5], "big")
                if width <= 0 or height <= 0 or width * height > MAX_SOURCE_PIXELS:
                    raise ValueError("Source plate exceeds pixel limit")
                return width, height
            source.seek(length - 2, 1)
    raise ValueError("Unsupported or invalid JPEG dimensions")


def _audubon_source(root, pack, spec, opener, source_directory):
    filename = spec.get("filename", "")
    if (not isinstance(filename, str) or PurePosixPath(filename).name != filename
            or any(c in filename for c in "\\?#") or not filename.lower().endswith((".jpg", ".jpeg"))):
        raise ValueError("Unsafe source plate filename")
    url = spec.get("url", "")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "upload.wikimedia.org"
            or not parsed.path.startswith("/wikipedia/commons/") or "/thumb/" in parsed.path
            or not parsed.path.lower().endswith((".jpg", ".jpeg")) or parsed.query or parsed.fragment):
        raise ValueError("Only original Commons JPEG plates may be downloaded")
    digest = spec.get("sha256", "")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Source plate is not hash-pinned")
    width, height = spec.get("width"), spec.get("height")
    if type(width) is not int or type(height) is not int or min(width, height) <= 0 or width * height > MAX_SOURCE_PIXELS:
        raise ValueError("Source plate exceeds pixel limit")
    output = _safe(root, f"{pack['id']}/{pack['revision']}/sources/{filename}")
    _cache_parent(root, output)
    temporary_path = None
    try:
        # Sources are public, cached externally, and streamed to bound memory.
        # Recheck a cached original before reuse; a corrupt cache is not trusted.
        if source_directory is not None:
            stream = _safe(Path(source_directory).resolve(), filename).open("rb")
        elif output.is_file():
            stream = output.open("rb")
        else:
            stream = opener.open(urllib.request.Request(url, headers={"User-Agent": "Federfunk-reviewed-artwork/1"}), timeout=30)
        with stream, tempfile.NamedTemporaryFile(dir=output.parent, suffix=".jpg", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            hasher, total = hashlib.sha256(), 0
            while block := stream.read(256 * 1024):
                total += len(block)
                if total > MAX_SOURCE_BYTES:
                    raise ValueError("Source plate exceeds download limit")
                hasher.update(block)
                temporary.write(block)
        if hasher.hexdigest() != digest:
            raise ValueError("Source artwork hash mismatch")
        if _jpeg_dimensions(temporary_path) != (width, height):
            raise ValueError("Source plate dimensions do not match reviewed image")
        temporary_path.chmod(0o644)
        temporary_path.replace(output)
        return output
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _install_audubon(pack, root, opener, source_directory):
    from contour_artwork import build_cutout
    recipes, sources = _audubon_recipes(pack), {}
    assets = pack.get("assets", [])
    if not isinstance(assets, list) or not 1 <= len(assets) <= 32:
        raise ValueError("Artwork pack exceeds the bounded allowlist limit")
    for entry in assets:
        recipe = recipes[entry.get("recipe_id", entry["id"])]
        source_id = entry["source_id"]
        spec = pack["sources"][source_id]
        review = entry.get("review", {})
        if (review.get("status") != "reviewed" or review.get("reviewed_source_sha256") != spec["sha256"]
                or review.get("reviewed_pixel_sha256") != entry["pixel_sha256"]
                or entry.get("source_sha256") != spec["sha256"] or recipe.get("source_id") != source_id):
            raise ValueError("Cutout does not match its recorded source and pixel review")
        if source_id not in sources:
            sources[source_id] = _audubon_source(root, pack, spec, opener, source_directory)
        image = build_cutout(sources[source_id], recipe)
        if (image.mode != "RGBA" or image.width * image.height > MAX_PIXELS
                or list(image.size) != [entry["width"], entry["height"]]
                or _digest(image.tobytes()) != entry["pixel_sha256"]):
            raise ValueError("Prepared cutout does not match reviewed pixels")
        _publish_png(root, pack, entry, image)
    return len(assets)


def _publish_png(root, pack, entry, image):
    output = _cache_path(root, pack, entry)
    # Public artwork must be readable by a sandboxed service user. Limit
    # permission changes to the pack's own cache directories and files.
    _cache_parent(root, output)
    from PIL.PngImagePlugin import PngInfo
    meta = PngInfo()
    for key in ("attribution", "rights", "rights_url", "source_url", "changes",
                "license_notice", "artwork_kind", "pose"):
        if key in entry:
            meta.add_text(key, str(entry[key]))
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".png", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        image.save(temporary_path, format="PNG", pnginfo=meta)
        if temporary_path.stat().st_size > MAX_BYTES:
            raise ValueError("Prepared artwork exceeds size limit")
        temporary_path.chmod(0o644)
        temporary_path.replace(output)
    finally:
        temporary_path.unlink(missing_ok=True)


def _install_bats(pack, root):
    """Install committed, individually reviewed derivatives without a download.

    Historical source hashes and edit prompts document the originals. The
    stored PNG and decoded pixels bind the reviewed AI-assisted extraction.
    """
    entries = pack.get("assets")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
        raise ValueError("Artwork pack exceeds the bounded allowlist limit")
    revision = _digest("\n".join(entry["prepared_sha256"] for entry in entries).encode())
    if pack.get("revision") != revision:
        raise ValueError("Bat artwork revision does not match reviewed PNGs")
    verified = []
    for entry in entries:
        relative = entry.get("prepared_file", "")
        if not relative.startswith("edits/bats/") or not relative.endswith(".png"):
            raise ValueError("Only reviewed local bat PNGs may be imported")
        path = _safe(BATS_REGISTRY.parent, relative)
        with path.open("rb") as source:
            data = source.read(MAX_BYTES + 1)
        review = entry.get("review", {})
        if (review.get("status") != "reviewed"
                or review.get("reviewed_source_sha256") != entry.get("source_sha256")
                or review.get("reviewed_pixel_sha256") != entry.get("pixel_sha256")
                or review.get("reviewed_sha256") != entry.get("prepared_sha256")
                or _digest(data) != entry["prepared_sha256"]):
            raise ValueError("Bat artwork does not match its source and pixel review")
        image = _image(data, entry, "PNG")
        if image.getchannel("A").getextrema() != (0, 255):
            raise ValueError("Bat artwork needs reviewed transparent alpha")
        verified.append((entry, image))
    for entry, image in verified:
        _publish_png(root, pack, entry, image)
    return len(verified)


def install_pack(pack_id, cache_root=None, source_directory=None):
    """Install one registered pack; optionally import a read-only upstream clone.

    Return installed count. Failed downloads never replace an existing file.
    The cache cannot be anywhere inside this repository. No archive extraction,
    arbitrary URLs, executable files or upstream scripts are supported.
    """
    pack = next((p for p in registered_packs() if p["id"] == pack_id), None)
    if pack is None:
        raise ValueError("Unknown artwork pack")
    root = cache_directory(cache_root)
    repository = Path(__file__).resolve().parent.parent
    if root.is_relative_to(repository):
        raise ValueError("Artwork packs must be cached outside the repository")
    opener = urllib.request.build_opener(_NoRedirect())
    if pack.get("kind") == AUDUBON_KIND:
        return _install_audubon(pack, root, opener, source_directory)
    if pack.get("kind") == BATS_KIND:
        return _install_bats(pack, root)
    if len(pack["revision"]) != 40 or any(c not in "0123456789abcdef" for c in pack["revision"]):
        raise ValueError("Pack is not pinned to an exact revision")
    if len(pack["assets"]) > 32:
        raise ValueError("Artwork pack exceeds the bounded allowlist limit")
    flight = pack.get("kind") == HABIRD_KIND
    prefix, suffix, image_format, repository = (
        ("avian/assets/illustrations/", ".png", "PNG", "adamoberley/HABirdDashboard") if flight else
        ("assets/artwork/classic/birds/", ".webp", "WEBP", "arnegiacomo/fugleramme"))
    installed = 0
    for entry in pack["assets"]:
        if entry.get("review", {}).get("status") != "reviewed":
            raise ValueError("Pack contains unreviewed artwork")
        relative = entry["upstream_file"]
        if not relative.startswith(prefix) or not relative.endswith(suffix):
            raise ValueError("Only allowlisted bird images may be imported")
        if ".." in PurePosixPath(relative).parts or any(c in relative for c in "?\\#"):
            raise ValueError("Unsafe upstream artwork path")
        if source_directory is not None:
            path = _safe(Path(source_directory).resolve(), relative)
            with path.open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
        else:
            url = f"https://raw.githubusercontent.com/{repository}/{pack['revision']}/{relative}"
            with opener.open(urllib.request.Request(url, headers={"User-Agent": "Federfunk-reviewed-artwork/1"}), timeout=30) as response:
                data = response.read(MAX_BYTES + 1)
        if _digest(data) != entry["source_sha256"]:
            raise ValueError("Source artwork hash mismatch")
        image = _image(data, entry, image_format)
        if flight and image.getchannel("A").getextrema() != (0, 255):
            raise ValueError("Flight artwork needs reviewed transparent alpha")
        _publish_png(root, pack, entry, image)
        installed += 1
    return installed


def _apply_reviewed_support_removals(assets):
    """Replace opted-in pack figures with reviewed, hash-bound local edits.

    The immutable upstream cache remains the provenance source. Stable asset
    IDs preserve saved selections; only matching source pixels can receive an
    edit. These three PNGs are explicit AI-assisted adaptations, not original
    historical pigment crops. No downloads occur in this read path.
    """
    if not SUPPORT_REMOVALS.is_file():
        return assets
    if SUPPORT_REMOVALS.stat().st_size > MAX_RECIPE_BYTES:
        raise ValueError("Support-removal registry exceeds its size limit")
    record = json.loads(SUPPORT_REMOVALS.read_text())
    entries = record.get("assets")
    if (record.get("schema_version") != 1 or not isinstance(entries, list)
            or not 1 <= len(entries) <= 32):
        raise ValueError("Unexpected support-removal registry")
    edits = {entry["id"]: entry for entry in entries}
    if len(edits) != len(entries):
        raise ValueError("Duplicate support-removal asset id")
    result = []
    for source in assets:
        edit = edits.get(source["id"])
        if edit is None:
            result.append(source)
            continue
        try:
            review = edit["review"]
            if (edit["scientific_name"] != source["scientific_name"]
                    or edit["source_sha256"] != source["source_sha256"]
                    or edit["source_pixel_sha256"] != source["pixel_sha256"]
                    or review["status"] != "reviewed"
                    or review["reviewed_source_sha256"] != source["source_sha256"]
                    or review["reviewed_pixel_sha256"] != edit["pixel_sha256"]
                    or review["reviewed_sha256"] != edit["sha256"]):
                continue
            path = _safe(SUPPORT_REMOVALS.parent, edit["file"])
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                continue
            data = path.read_bytes()
            if _digest(data) != edit["sha256"]:
                continue
            image = _image(data, edit, "PNG")
            if image.getchannel("A").getextrema() != (0, 255):
                continue
            changes = edit["changes"]
            asset = {
                **source,
                "width": edit["width"], "height": edit["height"],
                "sha256": edit["sha256"], "pixel_sha256": edit["pixel_sha256"],
                "variant": edit["variant"], "changes": changes, "modifications": changes,
                "attribution": source["attribution"] + " " + edit["editor_credit"],
                "review": review,
                "source_pixel_sha256": source["pixel_sha256"],
                "source_prepared_sha256": source["sha256"],
                "edit_method": "AI-assisted support removal",
                "edit_prompt": edit["edit_prompt"],
                "subject_geometry": {
                    "sha256": edit["sha256"], "pixel_sha256": edit["pixel_sha256"],
                    "size": [edit["width"], edit["height"]], "polygons": [], "exclude": [],
                    "review": {"status": "reviewed", "date": review["date"],
                               "scope": "Bird-only alpha after reviewed support removal."},
                },
                "file": path.name, "_resolved_path": str(path),
                "_cache_root": str(SUPPORT_REMOVALS.parent.resolve()),
            }
            result.append(asset)
        except (OSError, ValueError, KeyError, Image.DecompressionBombError):
            # A damaged/unreviewed replacement must never bypass verification.
            continue
    return result


def load_pack_assets(cache_root=None):
    """Read verified installed PNGs only; missing/corrupt entries are excluded.

    Installing the pack opts in. Uninstall by removing its external directory.
    This function performs no network operations and never creates directories.
    """
    root = cache_directory(cache_root)
    assets = []
    for pack in registered_packs():
        for entry in pack["assets"]:
            try:
                path = _cache_path(root, pack, entry)
                if not path.is_file() or path.stat().st_size > MAX_BYTES:
                    continue
                data = path.read_bytes()
                _image(data, entry, "PNG")
                if entry.get("review", {}).get("status") != "reviewed":
                    continue
                asset = dict(entry)
                asset.update(pack_id=pack["id"], pack_revision=pack["revision"], sha256=_digest(data),
                             file=path.name, _resolved_path=str(path), _cache_root=str(root))
                asset["review"] = {**entry["review"], "reviewed_sha256": asset["sha256"]}
                assets.append(asset)
            except (OSError, ValueError, KeyError, Image.DecompressionBombError):
                continue
    return _apply_reviewed_support_removals(assets)


def asset_path(asset, bundled_dir):
    """Resolve a bundled or verified external asset without allowing traversal."""
    if asset.get("pack_id"):
        root = Path(asset["_cache_root"]).resolve()
        path = Path(asset["_resolved_path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("External artwork escapes its cache")
    else:
        path = _safe(Path(bundled_dir).resolve(), asset["file"])
    if _digest(path.read_bytes()) != asset["sha256"]:
        raise ValueError("Artwork hash mismatch")
    return path


def public_asset(asset):
    """Strip local filesystem details while retaining source/license/review data."""
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if not k.startswith("_") and k not in {"file", "source_file", "resolved_path", "cache_root"}}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        if isinstance(value, Path):
            return None
        if isinstance(value, str) and (value.startswith("/") or value.startswith("~")):
            return None
        return value
    return clean(asset)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", choices=[p["id"] for p in registered_packs()])
    parser.add_argument("--cache-root")
    parser.add_argument("--source-directory", help="Read registered source images from a local directory, without running upstream code")
    args = parser.parse_args()
    print(f"Installed {install_pack(args.pack, args.cache_root, args.source_directory)} reviewed images in {cache_directory(args.cache_root)}")
