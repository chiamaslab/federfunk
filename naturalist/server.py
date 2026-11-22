#!/usr/bin/env python3
"""A local BirdNET-Go companion: historical bird prints, stats, and controls."""
import argparse
import base64
from collections import OrderedDict
import hashlib
import hmac
import ipaddress
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import render
from meaningful_updates import NON_VISUAL_SETTINGS, publish_reason, retain_print
from schedule import schedule_state
from audit_library import audit_history
from artwork_packs import asset_path, public_asset
import bird_details
from history_store import HistoryStore, battery_csv, discharge_estimate, public_data, valid_date

LOG = logging.getLogger("federfunk-naturalist")
ROOT = Path(__file__).resolve().parent
DEVICE_STALE_SECONDS = 75 * 60  # Two normal 30-minute frame wakes, plus a margin.


class PreviewBusy(RuntimeError):
    """One draft is rendering; avoid queuing more request threads behind it."""


def battery_headers(headers):
    """Allowlist small optional readings; bad metadata never blocks a print."""
    readings = {}
    for field, header in (("battery_percent", "X-FederFunk-Battery-Percent"),
                          ("battery_voltage", "X-FederFunk-Battery-Voltage"),
                          ("charging", "X-FederFunk-Charging")):
        values = headers.get_all(header, []) if hasattr(headers, "get_all") else [headers.get(header)]
        if len(values) != 1 or not isinstance(values[0], str) or len(values[0]) > 32:
            continue
        value = values[0].strip()
        if field == "battery_percent" and re.fullmatch(r"(?:0|[1-9][0-9]?|100)", value):
            readings[field] = int(value)
        elif field == "battery_voltage" and re.fullmatch(r"[0-9](?:\.[0-9]{1,4})?", value):
            voltage = float(value)
            if 2.5 <= voltage <= 4.5:
                readings[field] = voltage
        elif field == "charging" and value in ("charging", "not_charging", "unknown"):
            readings[field] = value
    return readings


def refresh_control_supported(headers):
    name = "X-FederFunk-Refresh-Control"
    values = headers.get_all(name, []) if hasattr(headers, "get_all") else [headers.get(name)]
    return values == ["1"]


POWER_RESULTS = frozenset(("updated", "unchanged", "failed", "scheduled_hold", "low_battery"))
POWER_FIELDS = dict(power_control_supported=False, stop_voltage=None, last_displayed_etag=None,
                    last_displayed_reported_at=None, previous_result=None, previous_sleep_seconds=None)


def power_headers(headers):
    """Accept explicit device acknowledgements, never infer them from a GET."""
    def single(name):
        values = headers.get_all(name, []) if hasattr(headers, "get_all") else [headers.get(name)]
        return values[0] if len(values) == 1 and isinstance(values[0], str) else None

    result = dict(POWER_FIELDS)
    result["power_control_supported"] = single("X-FederFunk-Power-Control") == "1"
    if not result["power_control_supported"]:
        return result
    millivolts = single("X-FederFunk-Stop-Millivolts")
    if millivolts and re.fullmatch(r"[0-9]{4}", millivolts):
        value = int(millivolts)
        if 3200 <= value <= 3600 and value % 50 == 0:
            result["stop_voltage"] = value / 1000
    tag = single("X-FederFunk-Last-Displayed-ETag")
    if tag and re.fullmatch(r'"[0-9a-f]{64}"', tag):
        result["last_displayed_etag"] = tag
    previous_result = single("X-FederFunk-Previous-Result")
    if previous_result in POWER_RESULTS:
        result["previous_result"] = previous_result
    sleep = single("X-FederFunk-Previous-Sleep-Seconds")
    if sleep and re.fullmatch(r"(?:0|[1-9][0-9]{0,4})", sleep) and int(sleep) <= 86400:
        result["previous_sleep_seconds"] = int(sleep)
    return result


def save_device(directory, device):
    directory.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="device-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "w") as output:
            json.dump(device, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, directory / "device.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_device(directory):
    empty = dict(battery_percent=None, battery_voltage=None, charging="unknown", last_seen=None, reported_at=None,
                 refresh_control_supported=False, refresh_interval_seconds=1800, **POWER_FIELDS)
    try:
        with (directory / "device.json").open() as source:
            encoded = source.read(4097)
        if len(encoded) > 4096:
            return empty
        saved = json.loads(encoded)
        if not isinstance(saved, dict):
            return empty
        result = dict(empty)
        result["refresh_control_supported"] = saved.get("refresh_control_supported") is True
        interval = saved.get("refresh_interval_seconds")
        if result["refresh_control_supported"] and type(interval) is int and 300 <= interval <= 43200 and interval % 60 == 0:
            result["refresh_interval_seconds"] = interval
        # Apply the same allowlist to persisted telemetry as to HTTP headers.
        restored_power = power_headers({
            "X-FederFunk-Power-Control": "1" if saved.get("power_control_supported") is True else "0",
            "X-FederFunk-Stop-Millivolts": str(round(saved["stop_voltage"] * 1000))
                if type(saved.get("stop_voltage")) in (int, float) and math.isfinite(saved["stop_voltage"])
                and abs(saved["stop_voltage"] * 1000 - round(saved["stop_voltage"] * 1000)) < 1e-7 else None,
            "X-FederFunk-Last-Displayed-ETag": saved.get("last_displayed_etag"),
            "X-FederFunk-Previous-Result": saved.get("previous_result"),
            "X-FederFunk-Previous-Sleep-Seconds": str(saved["previous_sleep_seconds"])
                if type(saved.get("previous_sleep_seconds")) is int else None})
        result.update(restored_power)
        for key in ("last_seen", "reported_at", "last_displayed_reported_at"):
            value = saved.get(key)
            if value is not None:
                stamp = datetime.fromisoformat(value)
                if stamp.tzinfo is None or stamp.timestamp() > time.time() + 300:
                    return empty
                result[key] = stamp.astimezone(timezone.utc).isoformat()
        if result["last_displayed_etag"] is None:
            result["last_displayed_reported_at"] = None
        elif result["last_displayed_reported_at"] is None:
            result["last_displayed_etag"] = None
        percent, voltage = saved.get("battery_percent"), saved.get("battery_voltage")
        if type(percent) is int and 0 <= percent <= 100:
            result["battery_percent"] = percent
        if type(voltage) in (int, float) and math.isfinite(voltage) and 2.5 <= voltage <= 4.5:
            result["battery_voltage"] = voltage
        if saved.get("charging") in ("charging", "not_charging", "unknown"):
            result["charging"] = saved["charging"]
        if result["reported_at"] is None:
            return {**result, "battery_percent": None, "battery_voltage": None, "charging": "unknown"}
        return result
    except (OSError, ValueError, TypeError, OverflowError):
        return empty


def clean(value, limit=180):
    return " ".join(str(value or "").split())[:limit]


def daily_birds(payload, threshold):
    if not isinstance(payload, list) or len(payload) > 2000:
        raise ValueError("Unexpected daily analytics response")
    birds = []
    seen = set()
    for row in payload:
        name = clean(row.get("scientific_name"))
        confidence = float(row.get("max_confidence", 0))
        count = int(row.get("count", 0))
        if not name or name in seen or not math.isfinite(confidence) or not 0 <= confidence <= 1 or count < 0:
            raise ValueError("Invalid species analytics")
        seen.add(name)
        if confidence < threshold or count == 0:
            continue
        hourly = row.get("hourly_counts", [])
        if len(hourly) != 24 or any(type(c) is not int or c < 0 for c in hourly):
            hourly = []
        # Deliberate allowlist: never persist recordings/source credentials.
        birds.append(dict(scientific_name=name, common_name=clean(row.get("common_name")) or name,
                          count=count, latest_heard=clean(row.get("latest_heard"), 8),
                          max_confidence=confidence, hourly_counts=hourly))
    return sorted(birds, key=lambda b: (-b["count"], b["scientific_name"]))


def get_json(base, path):
    request = Request(base.rstrip("/") + path, headers={"User-Agent": "FederFunk-Naturalist/1.0"})
    with urlopen(request, timeout=20) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError("Upstream response too large")
    return json.loads(body)


def validate_frame(frame):
    valid = {0, 1, 2, 3, 5, 6}
    if len(frame) != 960000 or any(b >> 4 not in valid or b & 15 not in valid for b in frame):
        raise ValueError("Invalid packed frame")


def save_bundle(directory, bundle):
    directory.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="current-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(handle, "wb") as output:
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
                for key, filename in (("frame", "current_large.bin"), ("preview", "preview.png"), ("art", "art.png")):
                    archive.writestr(filename, bundle[key])
                archive.writestr("state.json", json.dumps(bundle["state"]))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, directory / "current.zip")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_bundle(directory):
    try:
        with zipfile.ZipFile(directory / "current.zip") as archive:
            if any(info.file_size > 16_000_000 for info in archive.infolist()):
                return None
            bundle = {"frame": archive.read("current_large.bin"), "preview": archive.read("preview.png"),
                      "art": archive.read("art.png"), "state": json.loads(archive.read("state.json"))}
            validate_frame(bundle["frame"])
            if not isinstance(bundle["state"], dict) or not isinstance(bundle["state"].get("settings"), dict) or not isinstance(bundle["state"].get("stats"), dict):
                return None
            stats = bundle["state"]["stats"]
            if not isinstance(stats.get("species"), list) or not isinstance(stats.get("date"), str) or not isinstance(stats.get("observed_at"), str):
                return None
            return bundle
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None


class ArchivedPrintMissing(LookupError):
    pass


class Feed:
    def __init__(self, base, directory, interval=300, threshold=.8,
                 timezone="America/Denver", demo=False, assets=None, artwork_dir=None,
                 dashboard_url=None, pack_directory=None, preview_only=False, battery_capture_file=None):
        self.base, self.directory, self.interval, self.threshold = base, directory, interval, threshold
        self.timezone, self.demo = ZoneInfo(timezone), demo
        self.assets = render.catalogue(artwork_dir, pack_directory=pack_directory) if assets is None else assets
        self.artwork_dir = artwork_dir or ROOT / "artwork"
        self.preview_only = preview_only
        self.pack_directory = pack_directory
        self.history = HistoryStore(directory)
        if battery_capture_file is not None:
            self.history.import_capture(battery_capture_file)
        self.archive_error = None
        self.preview_lock, self.details_lock = threading.Lock(), threading.Lock()
        self.preview_cache, self.details_cache = OrderedDict(), OrderedDict()
        self.presentation_lock = threading.Lock()
        self.presentation_cache_lock = threading.Lock()
        self.presentation_cache, self.presentation_photo_errors = OrderedDict(), OrderedDict()
        self.lock = threading.RLock()
        self.refresh_lock = threading.Lock()
        self.device = load_device(directory)
        initial = {"schedule": {"timezone": timezone}}
        if dashboard_url is not None:
            initial["dashboard_url"] = dashboard_url
        initial = render.validate_settings({}, initial, self.assets)
        self.bundle = load_bundle(directory)
        if self.bundle:
            self.bundle["state"].setdefault("display_settings", self.bundle["state"]["settings"])
            self.bundle["state"].setdefault("display_stats", self.bundle["state"]["stats"])
            self.bundle["state"].setdefault("print_generated_at", self.bundle["state"]["display_stats"]["observed_at"])
            self.bundle["state"].setdefault("artwork_deferred", False)
        if self.bundle and self.bundle["state"].get("demo") != demo:
            self.bundle = None  # Never serve sample observations as live detections.
        try:
            self.settings = render.validate_settings({}, self.bundle["state"]["settings"] if self.bundle else initial, self.assets)
            if self.bundle:
                # Adding defaults is a schema migration, not a queued edit.
                # Normalize displayed settings separately so overnight edits
                # remain pending until their first active-window render.
                self.bundle["state"]["settings"] = self.settings
                self.bundle["state"]["display_settings"] = render.validate_settings(
                    {}, self.bundle["state"]["display_settings"], self.assets)
        except (ValueError, TypeError, KeyError):
            self.bundle = None  # Recover from an incompatible/deleted artwork selection.
            self.settings = initial
        self.error = "Waiting for a fresh BirdNET update" if not demo else None
        self.last_success = None
        # A restored or overnight print needs fresh observations before its
        # first active frame response, even if the polling thread is asleep.
        self.resume_pending = bool(self.bundle)
        if self.bundle and not self.schedule()["active"]:
            self.error = None  # An intentional scheduled pause is not a fetch failure.
            try:
                self.last_success = datetime.fromisoformat(self.bundle["state"]["stats"]["observed_at"]).timestamp()
            except ValueError:
                pass
        self.csrf_token = secrets.token_urlsafe(32)
        self.library_lock = threading.Lock()
        self.library = None
        self.library_updated = 0

    def library_state(self):
        with self.library_lock:
            if self.library and time.time() - self.library_updated < 3600:
                return self.library
            payload = json.loads((ROOT / "demo.json").read_text()) if self.demo else get_json(self.base, "/api/v2/analytics/species/summary?limit=1000")
            report = audit_history(payload, {"assets": self.assets})
            reviewed = {a["id"] for a in self.assets if a.get("review", {}).get("status") == "reviewed"}
            for row in report["rows"]:
                row["artwork_reviewed"] = bool(row["artwork_ids"]) and all(key in reviewed for key in row["artwork_ids"])
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.directory / "library-audit.tmp"
            temporary.write_text(json.dumps(report, indent=2))
            temporary.replace(self.directory / "library-audit.json")
            self.library, self.library_updated = report, time.time()
            return report

    def state(self):
        with self.lock:
            current = dict(self.bundle["state"]) if self.bundle else {"settings": self.settings, "stats": None, "revision": None}
            schedule = self.schedule()
            reported = self.device["reported_at"]
            # Use the last device request's cadence, not an edit it has not
            # received yet. Legacy firmware continues to wake every 30 minutes.
            cadence = self.device.get("refresh_interval_seconds", 1800)
            stale_after = max(DEVICE_STALE_SECONDS, 2 * cadence + 15 * 60)
            stale = bool(reported and time.time() - datetime.fromisoformat(reported).timestamp() > stale_after)
            return {**public_data(current), "artworks": [public_asset(a) for a in self.assets], "error": self.error,
                    "last_success": self.last_success, "demo": self.demo,
                    "preview_only": self.preview_only, "archive_error": self.archive_error,
                    "schedule": schedule,
                    "device": {**self.device, "stale": stale, "resting": not schedule["active"],
                               "last_displayed_matches_current": bool(self.bundle and
                                   self.device.get("last_displayed_etag") == render.etag(self.bundle["frame"]))},
                    "pending_settings": bool(self.bundle and self.settings != self.bundle["state"].get("display_settings", self.settings))}

    def record_device(self, headers, refresh_seconds=1800, at=None):
        readings = battery_headers(headers)
        power = power_headers(headers)
        with self.lock:
            now = (at or datetime.now(timezone.utc)).isoformat()
            supported = refresh_control_supported(headers)
            # None means an overnight hold: retain the last daytime cadence.
            # Errors offer no new interval and use the firmware's 30m fallback.
            cadence = (self.device.get("refresh_interval_seconds", 1800)
                       if refresh_seconds is None else refresh_seconds) if supported else 1800
            device = {**self.device, "last_seen": now, "refresh_control_supported": supported,
                      "refresh_interval_seconds": cadence, **power}
            if power["last_displayed_etag"]:
                # This is acknowledgement reception time, not panel refresh time.
                device["last_displayed_reported_at"] = now
            if readings:
                # Never present an omitted measurement as part of a new report.
                device.update(battery_percent=None, battery_voltage=None, charging="unknown", reported_at=now)
                device.update(readings)
            # Retain readings across a request from older firmware or a device
            # that cannot measure its battery; reported_at remains unchanged.
            self.device = device
            try:
                save_device(self.directory, device)
            except OSError:
                LOG.warning("Could not persist frame status; continuing the image request")
            try:
                self.history.add_battery(readings, datetime.fromisoformat(now))
            except (OSError, sqlite3.Error):
                LOG.warning("Could not persist battery history; continuing the image request")

    def archive(self, bundle):
        try:
            self.history.archive(bundle)
            self.archive_error = None
        except (OSError, sqlite3.Error, ValueError):
            self.archive_error = "The latest print could not be added to the archive"
            LOG.warning("Could not archive the latest print; keeping the current image")

    def battery_state(self, days):
        rows = self.history.battery(days)
        device = self.state()["device"]
        return dict(days=days, readings=rows,
                    estimate=discharge_estimate(self.history.forecast_rows(rows),
                                                expected_interval_seconds=device.get("refresh_interval_seconds", 1800)),
                    device=device)

    def details(self, species, day, source="current"):
        species, day = bird_details.species_name(species), valid_date(day)
        if source not in ("current", "archive"):
            raise ValueError("Choose current or archived print details")
        with self.lock:
            bundle, dashboard_url = self.bundle, self.settings["dashboard_url"]
        snapshot_state = None
        if source == "current" and bundle and bundle["state"]["stats"]["date"] == day:
            snapshot_state = bundle["state"]
        else:
            encoded = self.history.archived(day, "state.json")
            if encoded:
                snapshot_state = json.loads(encoded)
            elif source == "archive":
                raise ArchivedPrintMissing("No print was archived for this date")
        snapshot = snapshot_state.get("display_stats", snapshot_state["stats"]) if snapshot_state else None
        key = (source, species, day, snapshot_state.get("revision") if snapshot_state else None,
               snapshot.get("observed_at") if snapshot else None)
        with self.details_lock:
            cached = self.details_cache.get(key)
            if cached and time.monotonic()-cached[0] < 60:
                return cached[1]
            if snapshot_state is not None:
                # Archive credits belong to that completed print. A later
                # catalogue revision or featured photo must not rewrite them.
                assets = [public_asset(a) for a in snapshot_state.get("artwork_credits",
                                                snapshot_state.get("photo_credits", []))]
            else:
                assets = [public_asset(a) for a in self.assets]
            result = bird_details.details(self.base, dashboard_url, species, day, snapshot, assets, self.demo)
            result["print_artwork_id"] = next((row.get("artwork_id") for row in (snapshot or {}).get("species", [])
                                               if row["scientific_name"] == species), None)
            self.details_cache[key] = (time.monotonic(), result)
            self.details_cache.move_to_end(key)
            while len(self.details_cache) > 32:
                self.details_cache.popitem(last=False)
            return result

    def color_previews(self):
        from color_profiles import PROFILES
        with self.lock:
            if not self.bundle:
                return None
            revision = self.bundle["state"]["revision"]
        return dict(revision=revision, profiles=[{**value, "url": f"/color-preview/{key}.png?revision={revision}"}
                                                 for key, value in PROFILES.items()])

    def color_preview(self, profile, revision=None):
        from color_profiles import PROFILES
        from PIL import Image
        if profile not in PROFILES:
            raise ValueError("Unknown color profile")
        with self.lock:
            bundle = self.bundle
        if not bundle:
            return None
        if revision and revision != bundle["state"]["revision"]:
            raise RuntimeError("The print changed; reload the color comparisons")
        if profile == "current":
            return bundle["preview"]
        key = (bundle["state"]["revision"], hashlib.sha256(bundle["art"]).hexdigest(), profile)
        with self.preview_lock:
            if key not in self.preview_cache:
                with Image.open(io.BytesIO(bundle["art"])) as original:
                    canvas = original.convert("RGB")
                render.add_qr(canvas, bundle["state"].get("display_settings", bundle["state"]["settings"]))
                _, preview = render.pack(canvas, profile=profile)
                self.preview_cache[key] = preview
                while len(self.preview_cache) > 8:
                    self.preview_cache.popitem(last=False)
            self.preview_cache.move_to_end(key)
            return self.preview_cache[key]

    def schedule(self):
        return schedule_state(self.settings["schedule"], datetime.now(self.timezone))

    def _compose(self, birds, date, settings, photo_fetch=None):
        """Share production layout with draft previews, without packing or saving."""
        photo_credits = []
        displayed = set()
        if settings.get("style") == "classic":
            import classic
            from PIL import Image
            eligible = [bird for bird in birds if bird["count"] > 0]
            featured = max(eligible, key=lambda bird: (bird.get("latest_heard", ""), bird["count"], bird["scientific_name"])) if eligible else None
            picture, credit, selected = None, {}, []
            if featured:
                if settings.get("classic_media", "photos") == "drawings":
                    selected = render.choose_birds([featured], settings, self.assets, date=date)
                    if selected:
                        selected = [(bird, render.artwork_for_display(asset, settings, date)) for bird, asset in selected]
                        credit = public_asset(selected[0][1])
                        with Image.open(asset_path(selected[0][1], self.artwork_dir)) as source:
                            picture = source.convert("RGBA")
                        if selected[0][1]["display_mirrored"]:
                            picture = picture.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                elif not self.demo:
                    picture, credit = (photo_fetch or classic.photo)(self.base, featured["scientific_name"])
                    credit = {**credit, "common_name": featured["common_name"],
                              "scientific_name": featured["scientific_name"]}
                # Demo mode never substitutes a drawing for a requested photograph.
                if picture is not None:
                    displayed.add(featured["scientific_name"])
                    photo_credits = [credit]
            canvas = classic.compose(birds, settings, picture, credit, date, featured)
            render.add_qr(canvas, settings)
        else:
            canvas, selected = render.compose(birds, settings, self.assets, self.artwork_dir, date=date)
            displayed = {bird["scientific_name"] for bird, _ in selected}
        render.add_print_rights(canvas, [*[asset for _, asset in selected], *photo_credits])
        return canvas, selected, displayed, photo_credits

    def presentation_previews(self, patch):
        """Render drafts from one cached observation snapshot; never publish them."""
        from PIL import Image
        import classic
        with self.lock:
            settings = render.validate_settings(patch, self.settings, self.assets)
            if not self.bundle:
                return None
            # Copy mutable state before releasing the feed lock. The render can
            # be slow, but a normal device GET must not wait for these cards.
            stats = json.loads(json.dumps(self.bundle["state"]["stats"]))
            revision = self.bundle["state"]["revision"]
            settings = json.loads(json.dumps(settings))
        # Selection changes only which card is highlighted, not its pixels.
        settings["style"] = "collage"
        # Timing has no visual effect and should not invalidate three thumbnails.
        for key in NON_VISUAL_SETTINGS:
            settings.pop(key, None)
        key = hashlib.sha256(json.dumps({"settings": settings, "birds": stats["species"],
                                        "date": stats["date"], "revision": revision}, sort_keys=True).encode()).hexdigest()
        with self.presentation_cache_lock:
            cached = self.presentation_cache.get(key)
            if cached and (cached[0] is None or time.monotonic() < cached[0]):
                self.presentation_cache.move_to_end(key)
                return dict(date=stats["date"], observed_at=stats["observed_at"], revision=revision,
                            draft_id=key, previews=cached[1])
            if not self.presentation_lock.acquire(blocking=False):
                raise PreviewBusy("Preview renderer is busy; try again shortly.")
        try:
            previews = []
            for style, label in (("collage", "Collage"), ("classic", "Classic"),
                                 ("field_guide", "Field guide")):
                draft = {**settings, "style": style}
                error, credits = None, []

                def preview_photo(base, species):
                    nonlocal error
                    failure_key = (base, species, revision)
                    deadline = self.presentation_photo_errors.get(failure_key, 0)
                    if time.monotonic() >= deadline:
                        try:
                            # The normal eight-species photo LRU also
                            # serves production renders; toggles reuse it.
                            picture, credit = classic.photo(base, species)
                            self.presentation_photo_errors.pop(failure_key, None)
                            return picture, credit
                        except (OSError, ValueError, RuntimeError, Image.DecompressionBombError):
                            self.presentation_photo_errors[failure_key] = time.monotonic() + 60
                            self.presentation_photo_errors.move_to_end(failure_key)
                            while len(self.presentation_photo_errors) > 8:
                                self.presentation_photo_errors.popitem(last=False)
                    error = "Photograph unavailable; try again shortly."
                    return None, {}

                try:
                    canvas, selected, _, photo_credits = self._compose(
                        stats["species"], stats["date"], draft, photo_fetch=preview_photo)
                    credits = list({(a.get("id"), a.get("source_url"), a.get("title")): public_asset(a)
                                    for a in [*[asset for _, asset in selected], *photo_credits]}.values())
                    width = 240
                    height = round(canvas.height * width / canvas.width)
                    thumbnail = canvas.resize((width, height), Image.Resampling.LANCZOS)
                    data = base64.b64encode(render.png(thumbnail)).decode("ascii")
                    image_url = "data:image/png;base64," + data
                except (OSError, ValueError, RuntimeError, Image.DecompressionBombError):
                    width, height = (240, 320) if draft["orientation"] == "portrait" else (240, 180)
                    image_url, error = None, "This presentation preview is temporarily unavailable."
                previews.append(dict(style=style, label=label, image_url=image_url,
                                     width=width, height=height, error=error, credits=credits))
            # Failed upstream pictures/layouts can recover without a new
            # observation. Successful drafts remain reusable until evicted.
            deadline = time.monotonic() + 60 if any(p["error"] for p in previews) else None
            with self.presentation_cache_lock:
                self.presentation_cache[key] = (deadline, previews)
                self.presentation_cache.move_to_end(key)
                while len(self.presentation_cache) > 8:
                    self.presentation_cache.popitem(last=False)
        finally:
            self.presentation_lock.release()
        return dict(date=stats["date"], observed_at=stats["observed_at"], revision=revision,
                    draft_id=key, previews=previews)

    def build(self, birds, date, settings, observed_at, observation_timezone=None):
        canvas, selected, displayed, photo_credits = self._compose(birds, date, settings)
        frame, preview = render.pack(canvas)
        selected_ids = {b["scientific_name"]: a["id"] for b, a in selected}
        mirrored = {b["scientific_name"]: bool(a.get("display_mirrored")) for b, a in selected}
        available = {a["scientific_name"] for a in self.assets}
        stats = dict(date=date, species=[{**b, "artwork_id": selected_ids.get(b["scientific_name"]),
                                         "artwork_mirrored": mirrored.get(b["scientific_name"], False),
                                         "illustrated": b["scientific_name"] in displayed,
                                         "displayed": b["scientific_name"] in displayed,
                                         "artwork_available": b["scientific_name"] in available} for b in birds],
                     total_detections=sum(b["count"] for b in birds), species_count=len(birds),
                     illustrated_count=len(displayed), confidence_threshold=self.threshold,
                     observed_at=observed_at,
                     timezone=observation_timezone or settings["schedule"]["timezone"])
        artwork_credits = list({(a.get("id"), a.get("source_url"), a.get("title")): public_asset(a)
                                for a in [*[asset for _, asset in selected], *photo_credits]}.values())
        state = dict(settings=settings, display_settings=settings, stats=stats, display_stats=stats,
                     print_generated_at=observed_at, artwork_deferred=False, photo_credits=photo_credits,
                     artwork_credits=artwork_credits, canvas_size={"width": canvas.width, "height": canvas.height},
                     hotspots=canvas.info.get("Bird hotspots", []),
                     print_rights=canvas.info.get("Print rights"),
                     revision=hashlib.sha256(frame).hexdigest(), demo=self.demo)
        return dict(frame=frame, preview=preview, art=render.png(canvas), state=state)

    def refresh(self, resume_only=False):
        # A sunrise request may coincide with the regular poll. Share the work
        # and only return the newly published bundle to a waiting display.
        with self.refresh_lock:
            if resume_only:
                with self.lock:
                    if not self.resume_pending:
                        return
            self._refresh()

    def _refresh(self):
        with self.lock:
            if not self.schedule()["active"] and self.bundle:
                self.resume_pending = True
                return  # Preserve both print and its observations across midnight.
            zone = ZoneInfo(self.settings["schedule"]["timezone"])
        now = datetime.now(zone)
        if self.demo:
            payload = json.loads((ROOT / "demo.json").read_text())
        else:
            # Query the station's configured local day, never the health API's
            # UTC calendar day (which can be tomorrow in the evening).
            # BirdNET's query is a percentage (80), while per-row confidence
            # is a fraction (.8). Filtering only max_confidence would include
            # every low-confidence call for a species with one confident hit.
            payload = get_json(self.base, "/api/v2/analytics/species/daily?" + urlencode({
                "date": now.date().isoformat(), "min_confidence": self.threshold * 100}))
        birds = daily_birds(payload, self.threshold)
        with self.lock:
            if not self.schedule()["active"] and self.bundle:
                self.resume_pending = True
                return  # The cutoff may have passed while the request was in flight.
            candidate = self.build(birds, now.date().isoformat(), self.settings, now.isoformat(), str(zone))
            reason = publish_reason(self.bundle["state"] if self.bundle else None, candidate["state"], self.settings)
            bundle = candidate if reason else retain_print(self.bundle, candidate)
            if not self.schedule()["active"] and self.bundle:
                self.resume_pending = True
                return
            save_bundle(self.directory, bundle)
            self.bundle = bundle
            if self.schedule()["active"]:
                # Daily history always has matching fresh art and observation
                # totals, even when the main/device print is intentionally held.
                self.archive(candidate)
            self.last_success = time.time()
            self.error = None
            self.resume_pending = False

    def frame_for_display(self):
        with self.lock:
            status = self.schedule()
            if not status["active"]:
                self.resume_pending = bool(self.bundle)
                return self.bundle, status["sleep_seconds"]
            refresh_needed = self.resume_pending and bool(self.bundle)
        if refresh_needed:
            # Do not serve the old ETag at sunrise: the device would otherwise
            # skip it and sleep for another full refresh interval.
            self.refresh(resume_only=True)
        with self.lock:
            status = self.schedule()
            if not status["active"]:
                self.resume_pending = bool(self.bundle)
            return self.bundle, status["sleep_seconds"]

    def update_settings(self, patch):
        with self.lock:
            settings = render.validate_settings(patch, self.settings, self.assets)
            if not self.bundle:
                raise ValueError("Wait for the first BirdNET update before saving")
            stats = self.bundle["state"]["stats"]
            birds = stats["species"]
            active = schedule_state(settings["schedule"], datetime.now(self.timezone))["active"]
            timing_only = all(value == self.settings[key] for key, value in settings.items()
                              if key not in NON_VISUAL_SETTINGS)
            if timing_only:
                # Keep exact image bytes, colors, revision and the daily archive.
                displayed = {**self.bundle["state"].get("display_settings", self.settings),
                             **{key: settings[key] for key in NON_VISUAL_SETTINGS}}
                bundle = {**self.bundle, "state": {**self.bundle["state"], "settings": settings,
                                                   "display_settings": displayed}}
            elif active:
                bundle = self.build(birds, stats["date"], settings, stats["observed_at"], stats.get("timezone"))
                active = schedule_state(settings["schedule"], datetime.now(self.timezone))["active"]
                if not active:
                    bundle = {**self.bundle, "state": {**self.bundle["state"], "settings": settings}}
            else:
                bundle = {**self.bundle, "state": {**self.bundle["state"], "settings": settings}}
            # Persist settings and all images together before publishing to
            # concurrent request handlers. Failure leaves the old state intact.
            save_bundle(self.directory, bundle)
            self.settings, self.bundle = settings, bundle
            if active and not timing_only:
                self.archive(bundle)
            if not active:
                self.resume_pending = True
            return self.state()

    def run(self):
        while True:
            try:
                self.refresh()
            except Exception as exc:
                with self.lock:
                    self.error = type(exc).__name__
                LOG.warning("BirdNET update failed (%s); preserving last print", type(exc).__name__)
            time.sleep(self.interval)


def handler_for(feed):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def send(self, code, body, kind="application/json", tag=None, head=False, hold_seconds=0, download=None, retry_after=0,
                 refresh_seconds=0, stop_millivolts=0):
            if tag and any(t.strip().removeprefix("W/") in (tag, "*") for t in self.headers.get("If-None-Match", "").split(",")):
                code = 304
            self.send_response(code)
            self.send_header("Content-Type", kind)
            if code != 304:
                self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache" if tag else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if tag:
                self.send_header("ETag", tag)
            if hold_seconds:
                self.send_header("X-FederFunk-Hold-Seconds", str(hold_seconds))
            elif refresh_seconds and code in (200, 304):
                self.send_header("X-FederFunk-Refresh-Seconds", str(refresh_seconds))
            if stop_millivolts and code in (200, 304):
                self.send_header("X-FederFunk-Stop-Millivolts", str(stop_millivolts))
            if retry_after:
                self.send_header("Retry-After", str(retry_after))
            self.end_headers()
            if code != 304 and not head:
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def json(self, code, payload, head=False):
            self.send(code, json.dumps(payload).encode(), head=head)

        def valid_host(self):
            try:
                hostname = urlsplit("http://" + self.headers.get("Host", "")).hostname
                if hostname in ("localhost", "127.0.0.1", "::1", urlsplit(feed.settings["dashboard_url"]).hostname):
                    return bool(hostname)
                ipaddress.ip_address(hostname)
                return True
            except ValueError:
                return False

        def do_HEAD(self):
            self.do_GET(head=True)

        def do_GET(self, head=False):
            if not self.valid_host():
                self.json(403, {"error": "Use the server's IP address or configured dashboard URL"}, head)
                return
            path = urlsplit(self.path).path
            try:
                if len(self.path) > 2048:
                    raise ValueError("Request is too long")
                query = parse_qs(urlsplit(self.path).query, max_num_fields=8)
                if any(len(values) != 1 or len(values[0]) > 200 for values in query.values()):
                    raise ValueError("Invalid request parameters")
            except ValueError:
                self.json(400, {"error": "Invalid request parameters"}, head)
                return
            bundle = feed.bundle
            if path == "/api/state":
                self.json(200, feed.state(), head)
            elif path == "/api/archive":
                try:
                    self.json(200, {"days": feed.history.days()}, head)
                except (OSError, sqlite3.Error, ValueError):
                    self.json(503, {"error": "The print archive is temporarily unavailable"}, head)
            elif path == "/api/artwork-packs":
                from artwork_packs import packs_status
                self.json(200, {"packs": packs_status(feed.pack_directory)}, head)
            elif re.fullmatch(r"/archive/\d{4}-\d{2}-\d{2}/(?:art\.png|preview\.png|state\.json|download\.zip)", path):
                _, _, day, filename = path.split("/")
                try:
                    data = feed.history.archived(day, filename)
                except ValueError:
                    self.json(400, {"error": "Invalid archive date"}, head)
                    return
                except (OSError, sqlite3.Error):
                    self.json(503, {"error": "The print archive is temporarily unavailable"}, head)
                    return
                if data is None:
                    self.json(404, {"error": "No print was archived for this date"}, head)
                else:
                    kind = "application/zip" if filename.endswith(".zip") else "application/json" if filename.endswith(".json") else "image/png"
                    self.send(200, data, kind, render.etag(data), head,
                              download=f"federfunk-{day}.zip" if filename.endswith(".zip") else None)
            elif path in ("/api/battery", "/api/battery.csv"):
                try:
                    days = int(query.get("days", ["30"])[0])
                    if set(query)-{"days"} or days not in (7, 30):
                        raise ValueError("Invalid history interval")
                    data = feed.battery_state(days)
                    if path.endswith(".csv"):
                        self.send(200, battery_csv(data["readings"]), "text/csv; charset=utf-8", head=head,
                                  download=f"federfunk-battery-{days}days.csv")
                    else:
                        self.json(200, data, head)
                except ValueError:
                    self.json(400, {"error": "Choose a 7-day or 30-day battery history"}, head)
                except (OSError, sqlite3.Error):
                    self.json(503, {"error": "Battery history is temporarily unavailable"}, head)
            elif path == "/api/birds":
                try:
                    if set(query)-{"species", "date", "source"} or "species" not in query:
                        raise ValueError("Choose a species")
                    day = query.get("date", [bundle["state"]["stats"]["date"] if bundle else datetime.now(feed.timezone).date().isoformat()])[0]
                    species = bird_details.species_name(query["species"][0])
                    source = query.get("source", ["current"])[0]
                    if source not in ("current", "archive"):
                        raise ValueError("Choose current or archived print details")
                    valid_date(day)
                except ValueError:
                    self.json(400, {"error": "Choose a species and a valid date"}, head)
                    return
                try:
                    self.json(200, feed.details(species, day, source), head)
                except ArchivedPrintMissing:
                    self.json(404, {"error": "No print was archived for this date"}, head)
                except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
                    self.json(503, {"error": "BirdNET details are temporarily unavailable"}, head)
            elif path == "/api/color-previews":
                result = feed.color_previews()
                self.json(200 if result else 503, result or {"error": "The first print is not ready"}, head)
            elif re.fullmatch(r"/color-preview/[a-z_]+\.png", path):
                try:
                    if set(query)-{"revision"}:
                        raise ValueError("Invalid preview request")
                    result = feed.color_preview(path.rsplit("/", 1)[1][:-4], query.get("revision", [None])[0])
                    if result is None:
                        self.json(503, {"error": "The first print is not ready"}, head)
                    else:
                        self.send(200, result, "image/png", render.etag(result), head)
                except ValueError:
                    self.json(400, {"error": "Unknown color preview"}, head)
                except RuntimeError:
                    self.json(409, {"error": "The print changed; reload the comparisons"}, head)
            elif path == "/api/library":
                try:
                    self.json(200, feed.library_state(), head)
                except (OSError, ValueError, KeyError, TypeError):
                    self.json(503, {"error": "The species history is temporarily unavailable"}, head)
            elif path == "/api/settings":
                self.json(200, {"settings": feed.settings, "csrf_token": feed.csrf_token}, head)
            elif path == "/health":
                state = feed.state()
                healthy = bool(bundle) and (not state["schedule"]["active"] or (state["last_success"] and not state["error"]))
                self.json(200 if healthy else 503,
                          {**state, "ready": bool(bundle), "renderVersion": 3,
                           "frameBytes": len(bundle["frame"]) if bundle else 0,
                           "etag": render.etag(bundle["frame"]) if bundle else None}, head)
            elif path == "/credits":
                self.json(200, {"assets": [public_asset(a) for a in feed.assets],
                                "photo_credits": [public_asset(a) for a in bundle["state"].get("photo_credits", [])] if bundle else []}, head)
            elif path in ("/api/v1/display/current_large.bin", "/preview.png", "/art.png"):
                hold = 0
                refresh_seconds = 0
                stop_millivolts = 0
                device_request = False
                if path == "/api/v1/display/current_large.bin":
                    # Both deployed Arduino HTTPClient versions send this
                    # signature, including firmware without battery headers.
                    # Downloads and readiness probes are not frame check-ins.
                    device_request = not head and self.headers.get_all("User-Agent", []) == ["ESP32HTTPClient"]
                    received_at = datetime.now(timezone.utc)
                    try:
                        bundle, hold = feed.frame_for_display()
                    except Exception as exc:
                        with feed.lock:
                            feed.error = type(exc).__name__
                            if device_request:
                                feed.record_device(self.headers, at=received_at)
                        self.json(503, {"error": "The new print is not ready; the previous image is preserved"}, head)
                        return
                    with feed.lock:
                        refresh_seconds = feed.settings["refresh_interval_minutes"] * 60 if bundle and not hold else 0
                        stop_millivolts = round(feed.settings["stop_voltage"] * 1000) if bundle else 0
                        if device_request:
                            feed.record_device(self.headers, refresh_seconds=(None if hold and bundle else refresh_seconds or 1800),
                                               at=received_at)
                if not bundle:
                    self.json(503, {"error": "The first print is not ready"}, head)
                    return
                key = {"/preview.png": "preview", "/art.png": "art"}.get(path, "frame")
                data = bundle[key]
                self.send(200, data, "application/octet-stream" if key == "frame" else "image/png", render.etag(data), head, hold,
                          refresh_seconds=refresh_seconds, stop_millivolts=stop_millivolts)
            elif path.startswith("/artwork/") and path.endswith(".png"):
                asset = next((a for a in feed.assets if path == "/artwork/" + a["id"] + ".png"), None)
                if not asset:
                    self.json(404, {"error": "Unknown artwork"}, head)
                else:
                    data = asset_path(asset, feed.artwork_dir).read_bytes()
                    self.send(200, data, "image/png", render.etag(data), head)
            elif path in ("/", "/app.js", "/style.css", "/enhancements.js", "/enhancements.css"):
                filename = "index.html" if path == "/" else path[1:]
                kind = "text/html; charset=utf-8" if filename.endswith(".html") else "text/javascript; charset=utf-8" if filename.endswith(".js") else "text/css; charset=utf-8"
                self.send(200, (ROOT / "web" / filename).read_bytes(), kind, head=head)
            else:
                self.json(404, {"error": "Not found"}, head)

        def do_POST(self):
            self.close_connection = True
            path = urlsplit(self.path).path
            preview = path == "/api/presentation-previews"
            if not self.valid_host() or path not in ("/api/settings", "/api/presentation-previews"):
                self.json(403, {"error": "Request not allowed"})
                return
            origin = self.headers.get("Origin")
            if (origin and urlsplit(origin).netloc != self.headers.get("Host")) or self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.json(403, {"error": "Use controls on the dashboard itself"})
                return
            if not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), feed.csrf_token):
                self.json(403, {"error": "Reload the page before saving settings"})
                return
            try:
                size = int(self.headers.get("Content-Length", 0))
                if self.headers.get("Transfer-Encoding") or not 0 < size <= 16384 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("Send a JSON settings object (maximum 16 KB)")
                self.connection.settimeout(10)
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ValueError("Incomplete settings request")
                result = feed.presentation_previews(json.loads(data)) if preview else feed.update_settings(json.loads(data))
                if result is None:
                    self.json(503, {"error": "Waiting for the first BirdNET update before previewing"})
                else:
                    self.json(200, result)
            except (ValueError, TypeError, KeyError):
                self.json(400, {"error": "Invalid settings. Check the values, artwork choices, and QR URL."})
            except PreviewBusy:
                self.send(503, b'{"error":"Preview renderer is busy; try again shortly."}', retry_after=1)
            except (OSError, RuntimeError):
                message = ("Could not render presentation previews. The current print is unchanged." if preview else
                           "Could not save the new print. The previous settings remain active.")
                self.json(500, {"error": message})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--birdnet", default=os.getenv("BIRDNET_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--bind", default=os.getenv("FEED_BIND", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("FEED_PORT", "8001")))
    parser.add_argument("--state", type=Path, default=Path(os.getenv("STATE_DIRECTORY", str(ROOT / "state"))))
    parser.add_argument("--interval", type=int, default=int(os.getenv("FEED_INTERVAL", "300")))
    parser.add_argument("--confidence", type=float, default=float(os.getenv("MIN_CONFIDENCE", ".8")))
    parser.add_argument("--timezone", default=os.getenv("FEED_TIMEZONE", "America/Denver"))
    parser.add_argument("--dashboard-url", default=os.getenv("DASHBOARD_URL"),
                        help="Reachable dashboard URL for the QR code on a new installation; saved settings take precedence")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--preview-only", action="store_true", help="Mark this isolated dashboard as a local preview")
    parser.add_argument("--artwork-packs", type=Path, help="Directory containing explicitly installed optional artwork packs")
    parser.add_argument("--battery-capture", type=Path, help="Explicitly import a passive battery-test JSONL log into local history")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval < 30 or not 0 <= args.confidence <= 1:
        parser.error("Interval must be at least 30 seconds; confidence must be 0..1")
    if args.dashboard_url is not None:
        try:
            render.validate_settings({"show_qr": True, "dashboard_url": args.dashboard_url}, {}, [])
        except (ValueError, TypeError):
            # URL parsing errors can echo credentials or malformed port text.
            parser.error("Dashboard URL must be a valid HTTP(S) address without credentials")
    logging.basicConfig(level=logging.INFO)
    service = Feed(args.birdnet, args.state, args.interval, args.confidence, args.timezone, args.demo,
                   dashboard_url=args.dashboard_url, pack_directory=args.artwork_packs, preview_only=args.preview_only,
                   battery_capture_file=args.battery_capture)
    if args.once:
        service.refresh()
        return
    server = ThreadingHTTPServer((args.bind, args.port), handler_for(service))
    threading.Thread(target=service.run, daemon=True).start()
    LOG.info("Naturalist dashboard: http://%s:%s/", args.bind, args.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
