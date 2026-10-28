"""Decide when to publish a new device print, without suppressing daily history.

The comparison is always against the last published print's display_stats,
not the preceding poll. This makes small changes accumulate across restarts.
Material size means each illustrated bird's share of total bird-only area:
area_weight(count, scale) / sum(area_weight). Branch area and detection totals
are deliberately excluded. A 10% threshold means a relative change of 10%
in that share, not ten percentage points or a 10% change in linear length.
"""
from datetime import datetime

from render import area_weight

NON_VISUAL_SETTINGS = frozenset(("refresh_interval_minutes", "stop_voltage", "update_mode",
                                "meaningful_max_minutes", "meaningful_change_percent"))


def display_stats(state):
    return state.get("display_stats", state["stats"])


def _birds(stats):
    return {bird["scientific_name"]: bird for bird in stats["species"] if bird.get("count", 0) > 0}


def _shown(birds):
    return {name: bird for name, bird in birds.items()
            if bird.get("displayed", bird.get("illustrated", True))}


def _area_shares(birds, scale):
    weights = {name: area_weight(bird["count"], scale) for name, bird in birds.items()}
    total = sum(weights.values())
    return {name: weight / total for name, weight in weights.items()} if total else {}


def publish_reason(previous, candidate, settings):
    """Return a publication reason, or None to retain the prior image bundle.

    Candidate and previous are state dictionaries. Missing/invalid historical
    metadata is repaired by publishing once, never by indefinitely holding.
    """
    if settings.get("update_mode", "every_check") != "meaningful":
        return "every_check"
    if previous is None:
        return "first_print"
    old, new = display_stats(previous), candidate["stats"]
    if old["date"] != new["date"]:
        return "new_day"
    old_settings = previous.get("display_settings", previous["settings"])
    if any(value != old_settings.get(key) for key, value in settings.items() if key not in NON_VISUAL_SETTINGS):
        return "settings"
    before, after = _birds(old), _birds(new)
    if before.keys() != after.keys():
        # This includes every newly heard species, even one awaiting artwork.
        return "species"
    shown_before, shown_after = _shown(before), _shown(after)
    if shown_before.keys() != shown_after.keys():
        return "selection"
    for name, bird in shown_after.items():
        earlier = shown_before[name]
        if bird.get("artwork_id") != earlier.get("artwork_id"):
            return "artwork"
        if settings.get("show_names") and bird.get("common_name") != earlier.get("common_name"):
            return "labels"
        if settings.get("show_counts") and bird["count"] != earlier["count"]:
            return "labels"
    if settings.get("style") == "classic":
        # Classic presents a featured bird and a ranked activity list; both
        # remain meaningful even though this style does not size illustrations.
        featured = lambda birds: max(birds, key=lambda name: (birds[name].get("latest_heard", ""),
                                                             birds[name]["count"], name)) if birds else None
        if featured(before) != featured(after):
            return "selection"
        rank = lambda birds: sorted(birds, key=lambda name: (-birds[name]["count"], name))
        if rank(before) != rank(after):
            return "selection"
        if settings.get("show_counts") and any(before[name]["count"] != bird["count"] for name, bird in after.items()):
            return "labels"
        if settings.get("show_names") and any(before[name].get("common_name") != bird.get("common_name") for name, bird in after.items()):
            return "labels"
        if after and not any(settings.get(key) for key in ("show_names", "show_scientific", "show_counts")):
            old_max, new_max = max(b["count"] for b in before.values()), max(b["count"] for b in after.values())
            if any(abs((bird["count"] / new_max) / (before[name]["count"] / old_max) - 1) + 1e-12
                   >= settings["meaningful_change_percent"] / 100 for name, bird in after.items()):
                return "size"
    else:
        old_area = _area_shares(shown_before, settings["scale"])
        new_area = _area_shares(shown_after, settings["scale"])
        threshold = settings["meaningful_change_percent"] / 100
        if any(abs(new_area[name] / area - 1) + 1e-12 >= threshold for name, area in old_area.items()):
            return "size"
    try:
        rendered_at = datetime.fromisoformat(previous.get("print_generated_at", old["observed_at"]))
        observed_at = datetime.fromisoformat(new["observed_at"])
        if rendered_at.tzinfo is None or observed_at.tzinfo is None:
            return "missing_timestamp"
        if (observed_at - rendered_at).total_seconds() >= settings["meaningful_max_minutes"] * 60:
            return "maximum_age"
    except (TypeError, ValueError, KeyError):
        return "missing_timestamp"
    return None


def retain_print(previous, candidate):
    """Keep exact main-image bytes/credits/hotspots, with fresh dashboard stats.

    Archive the independent candidate, not this merged dashboard bundle: its
    daily totals and its artwork describe the same observation snapshot.
    """
    prior_state, fresh_state = previous["state"], candidate["state"]
    shown = display_stats(prior_state)
    state = {**prior_state, "settings": fresh_state["settings"], "stats": fresh_state["stats"],
             "display_stats": shown,
             "print_generated_at": prior_state.get("print_generated_at", shown["observed_at"]),
             "artwork_deferred": previous["frame"] != candidate["frame"] or previous["art"] != candidate["art"]}
    return {**previous, "state": state}
