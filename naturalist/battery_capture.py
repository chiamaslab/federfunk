#!/usr/bin/env python3
"""Observe an existing local dashboard during a battery test; never wake a frame."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import signal
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_LOG_BYTES = 128_000_000


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("A timezone-aware timestamp is required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("A timezone-aware timestamp is required")
    return result.astimezone(timezone.utc)


def validate_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.path != "/api/state" or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Use the local dashboard HTTP /api/state endpoint")
    parsed.port
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_state(url):
    request = Request(validate_url(url), headers={"User-Agent": "FederFunk-Battery-Observer/1.0"})
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("Dashboard response exceeds the read limit")
    return json.loads(data)


class Capture:
    def __init__(self, path, start, hours=96):
        if start.tzinfo is None or (hours is not None and not 0 < hours <= 96):
            raise ValueError("Test start must have a timezone and duration must be at most 96 hours")
        self.path, self.start = path, start.astimezone(timezone.utc)
        self.end = None if hours is None else self.start + timedelta(hours=hours)
        self.seen = set()
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_start = None
        existing_end = None
        if path.exists():
            if path.stat().st_size > MAX_LOG_BYTES:
                raise ValueError("Capture file exceeds its safety limit")
            # A power loss can leave only the last line incomplete. Preserve
            # every complete prior record and discard that unfinished tail.
            with path.open("rb+") as source:
                complete = 0
                for line in source:
                    if not line.endswith(b"\n"):
                        source.truncate(complete)
                        break
                    row = json.loads(line)
                    if row.get("event") == "test_started":
                        existing_start = timestamp(row["start_at"])
                        existing_end = row.get("end_at")
                    if row.get("event") == "test_window_updated":
                        existing_end = row.get("end_at")
                    if row.get("event") == "measurement":
                        self.seen.add(row["reported_at"])
                    complete += len(line)
        if existing_start is not None and existing_start != self.start:
            raise ValueError("This file belongs to a different battery test")
        end_at = self.end.isoformat() if self.end is not None else None
        if existing_start is None:
            self.append(dict(event="test_started", start_at=self.start.isoformat(), end_at=end_at,
                             note="Polling cached dashboard status only; gaps do not establish when a battery or device stopped."))
        elif existing_end != end_at:
            self.append(dict(event="test_window_updated", observed_at=datetime.now(timezone.utc).isoformat(),
                             previous_end_at=existing_end, end_at=end_at,
                             note="No fixed end time; continue until explicitly stopped." if end_at is None
                             else "Capture end time updated; original records retained."))
        os.chmod(path, 0o600)

    def append(self, row):
        if self.path.exists() and self.path.stat().st_size > MAX_LOG_BYTES:
            raise ValueError("Capture file exceeds its safety limit")
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a") as output:
            output.write(json.dumps(row, separators=(",", ":")) + "\n")
            output.flush()
            os.fsync(output.fileno())

    def observe(self, state, observed_at):
        poll = dict(event="poll", observed_at=observed_at.astimezone(timezone.utc).isoformat(), status="unavailable")
        device = state.get("device") if isinstance(state, dict) and not state.get("demo") else None
        if not isinstance(device, dict):
            self.append(poll)
            return False
        try:
            reported = timestamp(device.get("reported_at"))
            percent, voltage = device.get("battery_percent"), device.get("battery_voltage")
            charging = device.get("charging")
            if (type(percent) is not int or not 0 <= percent <= 100 or type(voltage) not in (float, int)
                    or not math.isfinite(voltage) or not 2.5 <= voltage <= 4.5
                    or charging not in ("charging", "not_charging", "unknown")
                    or reported > observed_at + timedelta(minutes=5)):
                raise ValueError("Invalid measurement")
        except (ValueError, TypeError):
            poll["status"] = "no_valid_measurement"
            self.append(poll)
            return False
        poll.update(reported_at=reported.isoformat(), age_seconds=max(0, int((observed_at-reported).total_seconds())))
        if reported < self.start:
            poll["status"] = "cached_before_test"
        elif self.end is not None and (reported > self.end or observed_at > self.end):
            poll["status"] = "outside_test_window"
        elif reported.isoformat() in self.seen:
            poll["status"] = "cached"
        else:
            record = dict(event="measurement", observed_at=poll["observed_at"], reported_at=reported.isoformat(),
                          at=reported.isoformat(), battery_percent=percent, battery_voltage=voltage, charging=charging)
            self.append(record)
            self.seen.add(record["reported_at"])
            poll["status"] = "new_measurement"
        self.append(poll)
        return poll["status"] == "new_measurement"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001/api/state")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", required=True, help="Battery disconnect/test start as an aware ISO timestamp")
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--hours", type=int, default=96)
    duration.add_argument("--until-stopped", action="store_true",
                          help="Keep recording without a fixed end; missing check-ins do not prove an empty battery")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        url = validate_url(args.url)
        if not 60 <= args.interval <= 3600:
            raise ValueError("Poll interval must be 60..3600 seconds")
        capture = Capture(args.output, timestamp(args.start), None if args.until_stopped else args.hours)
    except (ValueError, OSError):
        parser.error("Invalid capture settings or existing log; use a local /api/state URL, aware start time, and 1..96 hours or --until-stopped")
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopped:
        observed = datetime.now(timezone.utc)
        if capture.end is not None and observed >= capture.end:
            capture.append(dict(event="test_window_ended", observed_at=observed.isoformat()))
            return
        try:
            capture.observe(fetch_state(url), observed)
        except (OSError, ValueError, TypeError):
            capture.append(dict(event="poll", observed_at=observed.isoformat(), status="dashboard_unavailable"))
        if args.once:
            return
        deadline = time.monotonic() + args.interval
        while not stopped and time.monotonic() < deadline:
            time.sleep(max(0, min(1, deadline-time.monotonic())))
    capture.append(dict(event="collector_stopped", observed_at=datetime.now(timezone.utc).isoformat()))


if __name__ == "__main__":
    main()
