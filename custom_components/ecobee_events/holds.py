"""Strict, read-only snapshots of native Ecobee hold and vacation events."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(slots=True)
class HoldReport:
    """A report that distinguishes an empty event list from unreadable data."""

    state: str | None
    status: str
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        """Only fresh, valid data can establish the current override."""
        return self.status == "ok"


def _valid_local_datetime(event: dict[str, Any], prefix: str) -> bool:
    """Validate native local fields without assigning HA's timezone to them."""
    date = event.get(f"{prefix}Date")
    time = event.get(f"{prefix}Time")
    if not isinstance(date, str) or not isinstance(time, str):
        return False
    try:
        datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return True


def _valid_event(event: Any) -> bool:
    """Reject ambiguous flags rather than coercing strings to booleans."""
    if not isinstance(event, dict):
        return False
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        return False
    if not isinstance(event.get("running"), bool):
        return False
    for key in (
        "isIndefinite",
        "isTemperatureRelative",
        "isTemperatureAbsolute",
        "isOptional",
    ):
        if key in event and not isinstance(event[key], bool):
            return False
    if event_type.strip().lower() not in ("hold", "vacation"):
        return True
    if not isinstance(event.get("isIndefinite"), bool):
        return False
    if not _valid_local_datetime(event, "start"):
        return False
    if not event["isIndefinite"] and not _valid_local_datetime(event, "end"):
        return False
    for key in ("heatHoldTemp", "coolHoldTemp"):
        if type(event.get(key)) is not int:
            return False
    return True


def build_hold_report(
    thermostat: dict[str, Any],
    identifier: str,
    now: datetime,
    stale_after: timedelta,
) -> HoldReport:
    """Read one snapshot without writes, clock promotion, or identity fallback."""
    attributes: dict[str, Any] = {
        "thermostat_identifier": identifier,
        "source_data_updated": None,
        "source_data_stale": True,
        "event_time_basis": "thermostat_local",
    }

    def unavailable(status: str) -> HoldReport:
        return HoldReport(None, status, dict(attributes))

    if thermostat.get("identifier") != identifier:
        return unavailable("thermostat_missing")

    utc_time = thermostat.get("utcTime")
    if not isinstance(utc_time, str):
        return unavailable("freshness_unknown")
    try:
        updated = datetime.strptime(utc_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return unavailable("freshness_unknown")
    attributes["source_data_updated"] = updated.isoformat()
    age = now.astimezone(UTC) - updated
    if age < timedelta(0):
        return unavailable("source_time_in_future")
    if age > stale_after:
        return unavailable("stale")
    attributes["source_data_stale"] = False

    raw_events = thermostat.get("events")
    if not isinstance(raw_events, list) or not all(
        _valid_event(event) for event in raw_events
    ):
        return unavailable("events_malformed")

    events = deepcopy(raw_events)
    holds = [event for event in events if event["type"].strip().lower() == "hold"]
    vacations = [
        event for event in events if event["type"].strip().lower() == "vacation"
    ]
    # Ecobee orders events by priority; a later-starting hold need not be current.
    current = next((event for event in events if event["running"]), None)
    current_type = current["type"].strip().lower() if current else None
    state = (
        current_type
        if current_type in ("hold", "vacation")
        else "other"
        if current
        else "none"
    )
    attributes.update(
        {
            "hold_present": bool(holds),
            "vacation_present": bool(vacations),
            "hold_events": holds,
            "vacation_events": vacations,
            "current_event": current,
            "event_count": len(events),
        }
    )
    return HoldReport(state, "ok", attributes)
