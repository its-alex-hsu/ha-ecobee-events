"""Exercise hold reporting without importing or installing Home Assistant."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
import sys
import unittest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "custom_components/ecobee_events/holds.py"
)
SPEC = importlib.util.spec_from_file_location("ecobee_hold_reporting", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
holds_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = holds_module
SPEC.loader.exec_module(holds_module)
build_hold_report = holds_module.build_hold_report

NOW = datetime(2026, 11, 2, 21, 0, tzinfo=UTC)
IDENTIFIER = "test-thermostat"
STALE_AFTER = timedelta(minutes=10)


def hold(**overrides):
    """Make an API-shaped, finite hold."""
    return {
        "type": "hold",
        "name": "auto",
        "running": True,
        "startDate": "2026-11-02",
        "startTime": "13:00:00",
        "endDate": "2026-11-02",
        "endTime": "18:00:00",
        "isIndefinite": False,
        "isTemperatureRelative": False,
        "isTemperatureAbsolute": True,
        "holdClimateRef": "work",
        "heatHoldTemp": 680,
        "coolHoldTemp": 770,
        "fan": "auto",
        "fanMinOnTime": 15,
        **overrides,
    }


def thermostat(events):
    """Make a fresh Core cache snapshot."""
    return {
        "identifier": IDENTIFIER,
        "utcTime": "2026-11-02 20:59:00",
        "events": events,
    }


class HoldReportingTests(unittest.TestCase):
    def report(self, snapshot, now=NOW):
        return build_hold_report(snapshot, IDENTIFIER, now, STALE_AFTER)

    def test_schedule_and_identically_named_hold_are_distinct(self):
        scheduled = self.report(thermostat([]))
        held = self.report(thermostat([hold()]))
        self.assertEqual(scheduled.state, "none")
        self.assertEqual(held.state, "hold")
        self.assertEqual(held.attributes["current_event"]["holdClimateRef"], "work")

    def test_temporary_and_indefinite_holds_keep_complete_metadata(self):
        for indefinite in (False, True):
            with self.subTest(indefinite=indefinite):
                event = hold(isIndefinite=indefinite)
                report = self.report(thermostat([event]))
                self.assertTrue(report.available)
                self.assertEqual(report.attributes["hold_events"], [event])
                self.assertEqual(report.attributes["current_event"], event)
                self.assertEqual(
                    report.attributes["event_time_basis"], "thermostat_local"
                )

    def test_first_running_event_wins_not_latest_start_time(self):
        first = hold(name="priority", startTime="10:00:00")
        later = hold(name="later", startTime="13:00:00")
        queued = hold(name="queued", running=False, startTime="09:00:00")
        report = self.report(thermostat([queued, first, later]))
        self.assertEqual(report.attributes["current_event"], first)
        self.assertEqual(report.attributes["hold_events"], [queued, first, later])

    def test_vacation_and_utility_priority_do_not_hide_underlying_holds(self):
        for event, expected in (
            (hold(type="vacation", name="trip"), "vacation"),
            ({"type": "touSetback", "running": True}, "other"),
        ):
            with self.subTest(expected=expected):
                report = self.report(thermostat([event, hold()]))
                self.assertEqual(report.state, expected)
                self.assertTrue(report.attributes["hold_present"])
                self.assertEqual(report.attributes["current_event"], event)

    def test_queued_hold_is_visible_but_not_promoted_by_wall_clock(self):
        report = self.report(thermostat([hold(running=False)]))
        self.assertEqual(report.state, "none")
        self.assertTrue(report.attributes["hold_present"])
        self.assertIsNone(report.attributes["current_event"])

    def test_unknown_stale_and_future_freshness_never_mean_no_hold(self):
        for timestamp, status in (
            (None, "freshness_unknown"),
            ("bad", "freshness_unknown"),
            ("2026-11-02 20:49:59", "stale"),
            ("2026-11-02 21:00:01", "source_time_in_future"),
        ):
            with self.subTest(timestamp=timestamp):
                snapshot = thermostat([])
                snapshot["utcTime"] = timestamp
                snapshot["lastModified"] = "2026-11-02 20:59:00"
                report = self.report(snapshot)
                self.assertFalse(report.available)
                self.assertIsNone(report.state)
                self.assertEqual(report.status, status)
                self.assertNotIn("hold_present", report.attributes)

    def test_reading_does_not_refresh_source_timestamp(self):
        snapshot = thermostat([hold()])
        first = self.report(snapshot)
        second = self.report(snapshot, NOW + timedelta(minutes=10))
        self.assertEqual(
            first.attributes["source_data_updated"],
            second.attributes["source_data_updated"],
        )
        self.assertEqual(second.status, "stale")

    def test_wrong_thermostat_is_never_substituted(self):
        snapshot = thermostat([])
        snapshot["identifier"] = "someone-else"
        report = self.report(snapshot)
        self.assertFalse(report.available)
        self.assertEqual(report.status, "thermostat_missing")

    def test_malformed_events_are_not_an_empty_list(self):
        for events in (
            None,
            {},
            [None],
            [{}],
            [{"type": "hold", "running": "false"}],
            [hold(isIndefinite="false")],
            [hold(startDate="bad")],
            [hold(endTime="25:00:00")],
            [hold(heatHoldTemp=True)],
        ):
            with self.subTest(events=events):
                report = self.report(thermostat(events))
                self.assertFalse(report.available)
                self.assertEqual(report.status, "events_malformed")
                self.assertIsNone(report.state)

    def test_snapshots_do_not_alias_or_mutate_core_cache(self):
        event = hold(extra={"nested": ["original"]})
        snapshot = thermostat([event])
        before = deepcopy(snapshot)
        report = self.report(snapshot)
        self.assertEqual(snapshot, before)
        snapshot["events"][0]["extra"]["nested"][0] = "changed"
        self.assertEqual(
            report.attributes["current_event"]["extra"]["nested"], ["original"]
        )

    def test_replacement_snapshot_replaces_hold_report(self):
        held = self.report(thermostat([hold()]))
        resumed = self.report(thermostat([]))
        self.assertEqual(held.state, "hold")
        self.assertEqual(resumed.state, "none")
        self.assertEqual(resumed.attributes["hold_events"], [])


if __name__ == "__main__":
    unittest.main()
