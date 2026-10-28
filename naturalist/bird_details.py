"""Read-only, allowlisted details from the installed BirdNET-Go API contract."""
import json
import math
import re
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from history_store import valid_date


def species_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 180 or any(ord(c) < 32 for c in value):
        raise ValueError("Choose a species")
    return value.strip()


def text(value, limit=180):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def confidence(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1 else None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(base, path):
    parsed = urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid BirdNET base URL")
    request = Request(base.rstrip("/") + path, headers={"User-Agent": "FederFunk-Bird-Details/1.0"})
    with build_opener(NoRedirect()).open(request, timeout=8) as response:
        encoded = response.read(2_000_001)
    if len(encoded) > 2_000_000:
        raise ValueError("BirdNET response is too large")
    return json.loads(encoded)


def browser_base(base, dashboard_url):
    """A configured loopback upstream is reached through the dashboard host."""
    parsed = urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        return None
    host = parsed.hostname
    if host in ("127.0.0.1", "localhost", "::1"):
        host = urlsplit(dashboard_url).hostname
    if not host:
        return None
    authority = f"[{host}]" if ":" in host else host
    if parsed.port is not None:
        authority += ":" + str(parsed.port)
    return urlunsplit((parsed.scheme, authority, parsed.path.rstrip("/"), "", ""))


def details(base, dashboard_url, species, day, snapshot=None, artworks=(), demo=False):
    species, day = species_name(species), valid_date(day)
    printed = next((row for row in (snapshot or {}).get("species", []) if row["scientific_name"] == species), {})
    public_base = browser_base(base, dashboard_url)
    result = dict(scientific_name=species, common_name=printed.get("common_name", species), date=day,
                  lifetime=None, daily=dict(count=None, print_count=printed.get("count"),
                                           max_confidence=printed.get("max_confidence"),
                                           hourly_counts=printed.get("hourly_counts", [])),
                  detections=[], detections_total=0, limited=False,
                  artworks=[a for a in artworks if a.get("scientific_name") == species],
                  birdnet_url=(public_base + "/ui/detections?" + urlencode({"species": species, "date": day})) if public_base else None,
                  source_note="BirdNET-reported detections, not confirmed bird visits. Source totals can include lower-confidence detections than the print.",
                  demo=demo)
    if demo:
        result["source_note"] = "Demonstration observations; no live recordings or lifetime totals."
        result["birdnet_url"] = None
        return result
    summary = fetch(base, "/api/v2/analytics/species/summary?limit=1000")
    if not isinstance(summary, list) or len(summary) > 2000:
        raise ValueError("Invalid BirdNET species summary")
    for row in summary:
        if isinstance(row, dict) and row.get("scientific_name") == species:
            count = row.get("count")
            if type(count) is not int or not 0 <= count <= 1_000_000_000:
                raise ValueError("Invalid BirdNET lifetime count")
            result["common_name"] = text(row.get("common_name")) or result["common_name"]
            result["lifetime"] = dict(count=count, first_heard=text(row.get("first_heard"), 50),
                                      last_heard=text(row.get("last_heard"), 50),
                                      avg_confidence=confidence(row.get("avg_confidence")),
                                      max_confidence=confidence(row.get("max_confidence")))
            break
    response = fetch(base, "/api/v2/detections?" + urlencode({"species": species, "date": day, "limit": 30, "offset": 0}))
    if not isinstance(response, dict) or not isinstance(response.get("data"), list) or len(response["data"]) > 30:
        raise ValueError("Invalid BirdNET detection response")
    total = response.get("total")
    if type(total) is not int or not 0 <= total <= 1_000_000_000:
        raise ValueError("Invalid BirdNET detection count")
    records = []
    for row in response["data"]:
        if not isinstance(row, dict) or row.get("scientificName") != species or row.get("date") != day:
            raise ValueError("BirdNET returned detections for another species or day")
        identifier, score, clock = row.get("id"), confidence(row.get("confidence")), row.get("time")
        if (type(identifier) is not int or not 0 < identifier < 2**63 or score is None
                or not isinstance(clock, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d", clock)):
            raise ValueError("Invalid BirdNET detection")
        records.append(dict(id=identifier, date=day, time=clock, timestamp=text(row.get("timestamp"), 50),
                            confidence=score, verified=text(row.get("verified"), 32),
                            detail_url=f"{public_base}/ui/detections/{identifier}" if public_base else None))
    result.update(detections=records, detections_total=total, limited=total > len(records))
    result["daily"]["count"] = total
    return result
