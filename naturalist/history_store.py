"""Private durable daily prints and bounded battery history; no network work."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import csv
import io
import json
import math
import os
import sqlite3
import statistics
import zipfile

MAX_BATTERY_ROWS = 100_000
CAVEAT = ("Rough trend only: percentage is estimated from voltage, which is nonlinear and changes with load "
          "and temperature. The 0% projection is not a prediction of when the frame will stop working.")


def valid_date(value):
    if not isinstance(value, str) or len(value) != 10 or date.fromisoformat(value).isoformat() != value:
        raise ValueError("Use a date in YYYY-MM-DD format")
    return value


def public_data(value):
    if isinstance(value, dict):
        return {k: public_data(v) for k, v in value.items()
                if not k.startswith("_") and k not in {"file", "source_file", "resolved_path", "cache_root"}}
    if isinstance(value, list):
        return [public_data(v) for v in value]
    return value


class HistoryStore:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "history.sqlite3"
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS daily_prints (
                    date TEXT PRIMARY KEY, observed REAL NOT NULL, state TEXT NOT NULL,
                    art BLOB NOT NULL, preview BLOB NOT NULL, frame BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS battery (
                    id INTEGER PRIMARY KEY, stamp REAL NOT NULL UNIQUE,
                    percent INTEGER NOT NULL, voltage REAL NOT NULL, charging TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS battery_stamp ON battery(stamp);
                CREATE TABLE IF NOT EXISTS battery_breaks (stamp REAL PRIMARY KEY);
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def archive(self, bundle):
        state = public_data(bundle["state"])
        day = valid_date(state["stats"]["date"])
        stamp = datetime.fromisoformat(state["stats"]["observed_at"])
        if stamp.tzinfo is None:
            raise ValueError("Archive observations need a timezone")
        with self.connection() as db:
            db.execute("""INSERT INTO daily_prints VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET observed=excluded.observed, state=excluded.state,
                art=excluded.art, preview=excluded.preview, frame=excluded.frame
                WHERE excluded.observed >= daily_prints.observed""",
                       (day, stamp.timestamp(), json.dumps(state), bundle["art"], bundle["preview"], bundle["frame"]))

    def days(self):
        with self.connection() as db:
            records = db.execute("SELECT date,state FROM daily_prints ORDER BY date DESC").fetchall()
        result = []
        for day, encoded in records:
            state = json.loads(encoded)
            stats = state["stats"]
            base = "/archive/" + day + "/"
            result.append(dict(date=day, observed_at=stats["observed_at"], species_count=stats["species_count"],
                               total_detections=stats["total_detections"],
                               style=state.get("display_settings", state["settings"]).get("style", "collage"),
                               revision=state["revision"], art_url=base+"art.png", preview_url=base+"preview.png",
                               state_url=base+"state.json", download_url=base+"download.zip"))
        return result

    def archived(self, day, filename):
        valid_date(day)
        if filename not in ("art.png", "preview.png", "state.json", "download.zip"):
            raise ValueError("Unknown archive file")
        with self.connection() as db:
            row = db.execute("SELECT state,art,preview,frame FROM daily_prints WHERE date=?", (day,)).fetchone()
        if row is None:
            return None
        state, art, preview, frame = row
        if filename == "download.zip":
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
                for key, value in (("state.json", state), ("art.png", art), ("preview.png", preview),
                                   ("current_large.bin", frame)):
                    archive.writestr(key, value)
            return output.getvalue()
        return {"state.json": state.encode(), "art.png": art, "preview.png": preview}[filename]

    def add_battery(self, readings, at):
        percent, voltage = readings.get("battery_percent"), readings.get("battery_voltage")
        charging = readings.get("charging", "unknown")
        if (at.tzinfo is None or charging not in ("charging", "not_charging", "unknown")
                or (percent is not None and (type(percent) is not int or not 0 <= percent <= 100))
                or (voltage is not None and (type(voltage) not in (float, int) or not math.isfinite(voltage)
                                             or not 2.5 <= voltage <= 4.5))):
            return False
        if percent is None or voltage is None:
            # A genuine partial report makes the intervening battery state
            # uncertain. Preserve that break without inventing a chart reading.
            if readings:
                with self.connection() as db:
                    db.execute("INSERT OR IGNORE INTO battery_breaks VALUES (?)", (at.timestamp(),))
                    db.execute("DELETE FROM battery_breaks WHERE stamp < ?", (at.timestamp()-30*86400,))
                    db.execute("DELETE FROM battery_breaks WHERE stamp NOT IN (SELECT stamp FROM battery_breaks ORDER BY stamp DESC LIMIT ?)", (MAX_BATTERY_ROWS,))
            return False
        with self.connection() as db:
            # Bound repeated/manual wake traffic without losing charge-state
            # transitions, recharges, or normal 30-minute wake measurements.
            last = db.execute("SELECT stamp,percent,voltage,charging FROM battery ORDER BY stamp DESC LIMIT 1").fetchone()
            if last:
                elapsed = at.timestamp()-last[0]
                transition = charging != last[3] or percent > last[1]+2 or voltage > last[2]+.08
                if elapsed <= 0 or (elapsed < 60 and not transition):
                    return False
            db.execute("INSERT INTO battery(stamp,percent,voltage,charging) VALUES (?,?,?,?)",
                       (at.timestamp(), percent, voltage, charging))
            db.execute("DELETE FROM battery WHERE id NOT IN (SELECT id FROM battery ORDER BY stamp DESC LIMIT ?)", (MAX_BATTERY_ROWS,))
        return True

    def forecast_rows(self, rows):
        with self.connection() as db:
            last = db.execute("SELECT MAX(stamp) FROM battery_breaks").fetchone()[0]
        return rows if last is None else [r for r in rows if datetime.fromisoformat(r["at"]).timestamp() > last]

    def battery(self, days=30, now=None):
        if type(days) is not int or days not in (7, 30):
            raise ValueError("Choose 7 or 30 days")
        now = now or datetime.now(timezone.utc)
        with self.connection() as db:
            rows = db.execute("SELECT stamp,percent,voltage,charging FROM battery WHERE stamp BETWEEN ? AND ? ORDER BY stamp",
                              ((now-timedelta(days=days)).timestamp(), now.timestamp())).fetchall()
        return [dict(at=datetime.fromtimestamp(stamp, timezone.utc).isoformat(), battery_percent=percent,
                     battery_voltage=voltage, charging=charging) for stamp, percent, voltage, charging in rows]

    def import_capture(self, path):
        """Idempotent import of explicitly selected passive battery-test logs."""
        if path.stat().st_size > 32_000_000:
            raise ValueError("Battery capture exceeds its size limit")
        values, start, end = [], None, None
        with path.open("rb") as source:
            while line := source.readline(8193):
                if len(line) > 8192:
                    raise ValueError("Battery capture line exceeds its size limit")
                if not line.endswith(b"\n"):
                    break  # A live copy may end in an incomplete final write.
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("Invalid battery capture record")
                if row.get("event") == "test_started":
                    start, end = datetime.fromisoformat(row["start_at"]), datetime.fromisoformat(row["end_at"])
                    if start.tzinfo is None or end.tzinfo is None or not timedelta(0) < end-start <= timedelta(hours=96):
                        raise ValueError("Invalid battery capture window")
                if row.get("event") != "measurement":
                    continue
                at = datetime.fromisoformat(row["reported_at"])
                observed = datetime.fromisoformat(row["observed_at"])
                percent, voltage, charging = row.get("battery_percent"), row.get("battery_voltage"), row.get("charging")
                if (at.tzinfo is None or observed.tzinfo is None or start is None or not start <= at <= end
                        or at > observed+timedelta(minutes=5) or type(percent) is not int or not 0 <= percent <= 100
                        or type(voltage) not in (int, float) or not math.isfinite(voltage) or not 2.5 <= voltage <= 4.5
                        or charging not in ("charging", "not_charging", "unknown")):
                    raise ValueError("Invalid battery capture measurement")
                values.append((at.timestamp(), percent, voltage, charging))
        with self.connection() as db:
            before = db.total_changes
            db.executemany("INSERT OR IGNORE INTO battery(stamp,percent,voltage,charging) VALUES (?,?,?,?)", values)
            inserted = db.total_changes-before
            # Keep the newest readings by time, including when backfilling a log.
            db.execute("DELETE FROM battery WHERE id NOT IN (SELECT id FROM battery ORDER BY stamp DESC LIMIT ?)", (MAX_BATTERY_ROWS,))
        return inserted


def battery_csv(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=("at", "battery_percent", "battery_voltage", "charging"))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def discharge_estimate(rows, now=None, *, expected_interval_seconds=1800):
    """Conservative trend from a fresh uninterrupted uncharged segment only."""
    now = now or datetime.now(timezone.utc)
    # A slower device cadence must not look like an unexplained reporting gap.
    # Keep the original tolerances for the default 30-minute interval, and
    # allow processing time around longer scheduled wakes. Invalid settings
    # fall back to the default instead of indefinitely trusting stale data.
    if type(expected_interval_seconds) is not int or not 300 <= expected_interval_seconds <= 43200:
        expected_interval_seconds = 1800
    freshness_seconds = max(75*60, 2*expected_interval_seconds + 15*60)
    max_gap_seconds = max(3*3600, 3*expected_interval_seconds + 15*60)
    result = dict(available=False, reason="More consistent readings are needed", hours_to_15=None,
                  hours_to_zero=None, sample_count=0, span_hours=0, caveat=CAVEAT)
    segment = []
    for row in rows:
        stamp = datetime.fromisoformat(row["at"])
        if row["charging"] != "not_charging":
            segment = []
            continue
        if segment:
            previous = segment[-1]
            gap = (stamp - datetime.fromisoformat(previous["at"])).total_seconds()
            if (not 0 < gap <= max_gap_seconds or row["battery_percent"] > previous["battery_percent"] + 2
                    or row["battery_voltage"] > previous["battery_voltage"] + .08):
                segment = []
        segment.append(row)
    # Use at most the last 48 hours, and a bounded sample set for pairwise slopes.
    segment = [r for r in segment if now-datetime.fromisoformat(r["at"]) <= timedelta(hours=48)]
    segment = segment[-200:]
    result["sample_count"] = len(segment)
    if not segment:
        result["reason"] = "No recent uninterrupted not-charging readings"
        return result
    if now-datetime.fromisoformat(segment[-1]["at"]) > timedelta(seconds=freshness_seconds):
        result["reason"] = "Latest reading is too old for a forecast"
        return result
    hours = [(datetime.fromisoformat(r["at"])-datetime.fromisoformat(segment[0]["at"])).total_seconds()/3600 for r in segment]
    result["span_hours"] = round(hours[-1], 1)
    if len(segment) < 8 or hours[-1] < 6:
        return result
    if (segment[0]["battery_percent"]-segment[-1]["battery_percent"] < 3 or
            segment[0]["battery_voltage"]-segment[-1]["battery_voltage"] < .015):
        result["reason"] = "Not enough measured decline to estimate remaining time"
        return result
    slopes = sorted((segment[i]["battery_percent"]-segment[j]["battery_percent"])/(hours[j]-hours[i])
                    for i in range(len(segment)) for j in range(i+1, len(segment)) if hours[j]-hours[i] >= 2)
    if not slopes or sum(s > 0 for s in slopes)/len(slopes) < .8:
        result["reason"] = "The recent readings do not show a consistent discharge trend"
        return result
    rate = statistics.median(slopes)
    low_rate, high_rate = slopes[len(slopes)//4], slopes[3*len(slopes)//4]
    if low_rate <= 0 or high_rate/low_rate > 2.5:
        result["reason"] = "The recent discharge rate is too variable"
        return result
    low_rate, high_rate = min(low_rate, rate*.7), max(high_rate, rate*1.3)
    def hours_to(target):
        remaining = max(0, segment[-1]["battery_percent"]-target)
        if not remaining:
            return dict(low=0, high=0)
        return dict(low=max(0, int(remaining/high_rate//6)*6),
                    high=int(math.ceil(remaining/low_rate/6))*6)
    if hours_to(0)["high"] > 720:
        result["reason"] = "The trend is too small for a useful forecast"
        return result
    result.update(available=True, reason="Rough range from the recent not-charging trend",
                  hours_to_15=hours_to(15), hours_to_zero=hours_to(0))
    return result
