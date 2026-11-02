"""Exercise the observer through real Home Assistant coordinator/entity classes."""

from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.ecobee_events import EcobeeEventsCoordinator
from custom_components.ecobee_events.sensor import (
    EcobeeActiveEventSensor,
    EcobeeHoldStatusSensor,
    async_setup_entry,
)

from test_holds import IDENTIFIER, NOW, hold, thermostat


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.hass = HomeAssistant(str(Path(self.directory.name)))
        self.client = SimpleNamespace(thermostats=[thermostat([hold()])])
        self.core_entry = SimpleNamespace(
            runtime_data=SimpleNamespace(ecobee=self.client)
        )
        self.hass.config_entries = Mock()
        self.hass.config_entries.async_loaded_entries.return_value = [self.core_entry]
        self.entry = ConfigEntry(
            domain="ecobee_events",
            data={"thermostat_id": IDENTIFIER},
            discovery_keys=MappingProxyType({}),
            minor_version=1,
            options={},
            source="user",
            subentries_data=None,
            title="Test observer",
            unique_id=IDENTIFIER,
            version=1,
        )
        self.coordinator = EcobeeEventsCoordinator(self.hass, self.entry, IDENTIFIER)
        self.entry.runtime_data = self.coordinator
        self.clock = patch.object(dt_util, "utcnow", return_value=NOW)
        self.local_clock = patch.object(
            dt_util, "now", return_value=NOW.astimezone(dt_util.DEFAULT_TIME_ZONE)
        )
        self.clock.start()
        self.local_clock.start()
        self.sensor = EcobeeHoldStatusSensor(self.coordinator)
        self.utility_sensor = EcobeeActiveEventSensor(self.coordinator)

    async def asyncTearDown(self):
        self.clock.stop()
        self.local_clock.stop()
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    async def test_platform_registers_hold_sensor_alongside_original(self):
        entities = []
        await async_setup_entry(self.hass, self.entry, entities.extend)
        self.assertEqual(len(entities), 2)
        self.assertIsInstance(entities[0], EcobeeActiveEventSensor)
        self.assertIsInstance(entities[1], EcobeeHoldStatusSensor)
        self.assertNotEqual(entities[0].unique_id, entities[1].unique_id)

    async def test_core_list_replacement_reaches_entity(self):
        await self.coordinator.async_refresh()
        self.assertTrue(self.sensor.available)
        self.assertEqual(self.sensor.native_value, "hold")
        self.assertEqual(self.utility_sensor.native_value, "none")
        self.assertEqual(self.utility_sensor.extra_state_attributes["events"], [])
        self.client.thermostats = [thermostat([])]
        await self.coordinator.async_refresh()
        self.assertTrue(self.sensor.available)
        self.assertEqual(self.sensor.native_value, "none")
        self.assertEqual(self.sensor.extra_state_attributes["hold_events"], [])
        self.assertFalse(
            self.utility_sensor.extra_state_attributes["permanent_hold_present"]
        )

    async def test_stale_data_only_disables_new_hold_surface(self):
        await self.coordinator.async_refresh()
        snapshot = thermostat([hold()])
        snapshot["utcTime"] = (NOW - timedelta(minutes=11)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        self.client.thermostats = [snapshot]
        await self.coordinator.async_refresh()
        self.assertFalse(self.sensor.available)
        self.assertIsNone(self.sensor.native_value)
        self.assertEqual(self.sensor.extra_state_attributes["data_status"], "stale")
        self.assertTrue(self.utility_sensor.available)
        self.assertTrue(
            self.utility_sensor.extra_state_attributes["permanent_hold_present"]
        )
        self.client.thermostats = [thermostat([hold()])]
        await self.coordinator.async_refresh()
        self.assertTrue(self.sensor.available)
        self.assertEqual(self.sensor.native_value, "hold")

    async def test_legacy_identity_fallback_cannot_expose_foreign_holds(self):
        snapshot = thermostat([hold(name="foreign")])
        snapshot["identifier"] = "another-thermostat"
        self.client.thermostats = [snapshot]
        await self.coordinator.async_refresh()
        self.assertTrue(self.coordinator.last_update_success)
        self.assertFalse(self.sensor.available)
        self.assertEqual(
            self.sensor.extra_state_attributes["data_status"], "thermostat_missing"
        )
        self.assertNotIn("hold_events", self.sensor.extra_state_attributes)

    async def test_core_disappearing_hides_previously_valid_snapshot(self):
        await self.coordinator.async_refresh()
        self.assertTrue(self.sensor.available)
        self.client.thermostats = []
        await self.coordinator.async_refresh()
        self.assertFalse(self.sensor.available)
        self.assertIsNone(self.sensor.native_value)
        self.assertEqual(
            self.sensor.extra_state_attributes, {"data_status": "source_unavailable"}
        )

    async def test_malformed_source_does_not_report_hold_absence(self):
        self.client.thermostats = [thermostat(None)]
        await self.coordinator.async_refresh()
        self.assertFalse(self.sensor.available)
        self.assertEqual(
            self.sensor.extra_state_attributes["data_status"], "events_malformed"
        )


if __name__ == "__main__":
    unittest.main()
