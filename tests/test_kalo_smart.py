"""Tests for the reverse-engineered KALO Smart data model.

The fixtures are the payload shapes the app itself uses in demo mode, so these
tests pin down the mapping from the backend's vocabulary to Home Assistant's.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.util import dt as dt_util

from custom_components.kalo_smart.api import device_eui
from custom_components.kalo_smart.climate import (
    PENDING_TIMEOUT,
    KaloSmartClimate,
    _Pending,
)
from custom_components.kalo_smart.coordinator import build_data

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "api_payloads.json").read_text())


@pytest.fixture
def data():
    """Build a snapshot the way the coordinator does."""
    return build_data(
        FIXTURES["room_groups"],
        FIXTURES["rooms"],
        FIXTURES["devices"],
        FIXTURES["room_names"],
    )


# -- device addressing ------------------------------------------------------


@pytest.mark.parametrize(
    ("thing_id", "expected"),
    [
        ("io.beyonnex.connect:eui70b3d52dd3002171", "70b3d52dd3002171"),
        ("io.beyonnex.connect:eui", None),
        ("no-eui-here", None),
        (None, None),
    ],
)
def test_device_eui(thing_id, expected):
    """The childLock endpoint wants the bare EUI, not the thingId."""
    assert device_eui(thing_id) == expected


# -- stitching --------------------------------------------------------------


def test_counts(data):
    """Every home, room and device in the feeds is represented."""
    assert len(data.homes) == 1
    assert len(data.rooms) == 3
    assert len(data.devices) == 3


def test_devices_attach_to_their_room(data):
    """Devices are grouped by roomId."""
    assert [d.serial for d in data.rooms["1_badezimmer"].devices] == ["VA0123456789"]
    assert [d.serial for d in data.rooms["2_wohnzimmer"].devices] == ["VA9876543210"]
    # The gateway has no roomId and must not be attached to a room.
    assert data.rooms["3_schlafzimmer"].devices == []


def test_room_name_comes_from_resident_data(data):
    """The rooms feed leaves displayName empty; names live on another service."""
    assert data.rooms["1_badezimmer"].name == "Bad"
    assert data.rooms["2_wohnzimmer"].name == "Wohnzimmer"


def test_room_name_falls_back_to_usage_type(data):
    """A room with no custom name is labelled by what it is used for."""
    assert data.rooms["3_schlafzimmer"].name == "Bedroom"


def test_unknown_room_keeps_its_id_as_name():
    """A room with neither a name nor a known usage type stays identifiable."""
    rooms = [{"id": "weird", "roomGroupId": "x", "usageType": "SAUNA"}]
    built = build_data([], rooms, [], {})
    assert built.rooms["weird"].name == "weird"


def test_rooms_without_id_are_skipped():
    """Malformed entries must not produce entities with no unique id."""
    built = build_data([], [{"usageType": "OFFICE"}], [], {})
    assert built.rooms == {}


# -- mode semantics ---------------------------------------------------------


def test_manual_room(data):
    """Not off and no schedule means a fixed setpoint."""
    room = data.rooms["1_badezimmer"]
    assert not room.is_off
    assert not room.is_schedule_active
    assert room.target_temperature == 22.0
    assert room.min_temp == 6
    assert room.max_temp == 28


def test_scheduled_room(data):
    """IsScheduleActive marks the room as following its schedule."""
    room = data.rooms["2_wohnzimmer"]
    assert room.is_schedule_active
    assert not room.is_off


def test_off_room(data):
    """IsTurnedOff wins over everything else."""
    assert data.rooms["3_schlafzimmer"].is_off


@pytest.mark.parametrize(
    ("status", "heating"),
    [("off", False), ("lew", True), ("warm", True), ("hot", True), ("HOT", True)],
)
def test_radiator_status_means_heating(status, heating):
    """RadiatorStatus is the only signal for whether the valve is open."""
    rooms = [{"id": "r", "roomGroupId": "x", "radiatorStatus": status}]
    assert build_data([], rooms, [], {}).rooms["r"].is_heating is heating


def test_missing_limits_fall_back_to_app_defaults():
    """A room that reports no limits still gets the dial range the app uses."""
    built = build_data([], [{"id": "r", "roomGroupId": "x"}], [], {})
    assert built.rooms["r"].min_temp == 6.0
    assert built.rooms["r"].max_temp == 28.0


# -- window detection -------------------------------------------------------


def test_window_state(data):
    """Open-window state and whether detection is even on are separate flags."""
    assert data.rooms["2_wohnzimmer"].is_window_open
    assert data.rooms["2_wohnzimmer"].is_window_detection_enabled
    assert not data.rooms["3_schlafzimmer"].is_window_detection_enabled


# -- devices ----------------------------------------------------------------


def test_thermostat_telemetry(data):
    """Per-device readings are finer grained than the room average."""
    device = data.devices["io.beyonnex.connect:eui70b3d52dd3002171"]
    assert device.is_thermostat
    assert device.temperature == 22.0
    assert device.target_temperature == 23.0
    assert device.humidity == 56.0
    assert device.battery == 87.0
    assert device.child_lock is False
    assert device.eui == "70b3d52dd3002171"


def test_gateway_reports_no_telemetry(data):
    """Non-thermostats share the feed but carry no readings."""
    gateway = data.devices["io.beyonnex.connect:eui70b3d52dd30021ff"]
    assert not gateway.is_thermostat
    assert gateway.temperature is None
    assert gateway.battery is None
    assert gateway.room_id is None


# -- homes ------------------------------------------------------------------


def test_home_profile(data):
    """The away switch reads the home's profile."""
    home = data.homes["DunderMifflinScranton"]
    assert home.name == "Demo Zuhause"
    assert not home.is_away
    assert home.profile_in_sync


def test_away_profile():
    """PROFILE_APP_AWAY is what the app's away toggle sets."""
    homes = [{"id": "h", "profile": {"name": "PROFILE_APP_AWAY", "isInSync": False}}]
    home = build_data(homes, [], [], {}).homes["h"]
    assert home.is_away
    assert not home.profile_in_sync


def test_home_without_profile_is_not_away():
    """A home that never had a profile set must not read as away."""
    home = build_data([{"id": "h"}], [], [], {}).homes["h"]
    assert home.profile is None
    assert not home.is_away


# -- climate mode mapping ---------------------------------------------------
#
# The climate entity is exercised without Home Assistant's fixtures: the mode
# properties only read coordinator data, so a bare instance with the room id
# set is enough to pin down the mapping.


def _climate(data, room_id):
    """Build a climate entity far enough to read its mode properties."""
    entity = KaloSmartClimate.__new__(KaloSmartClimate)
    entity._room_id = room_id
    entity._pending_temperature = None
    entity._pending_hvac_mode = None
    object.__setattr__(entity, "coordinator", SimpleNamespace(data=data))
    return entity


def test_manual_room_maps_to_heat(data):
    """A fixed setpoint is Home Assistant's heat mode."""
    entity = _climate(data, "1_badezimmer")
    assert entity.hvac_mode == HVACMode.HEAT
    assert entity.hvac_action == HVACAction.IDLE
    assert entity.target_temperature == 22.0
    assert entity.current_humidity == 47


def test_scheduled_room_maps_to_auto(data):
    """A room following its schedule is auto, and a hot valve is heating."""
    entity = _climate(data, "2_wohnzimmer")
    assert entity.hvac_mode == HVACMode.AUTO
    assert entity.hvac_action == HVACAction.HEATING


def test_off_room_maps_to_off(data):
    """An off room reports off for both mode and action."""
    entity = _climate(data, "3_schlafzimmer")
    assert entity.hvac_mode == HVACMode.OFF
    assert entity.hvac_action == HVACAction.OFF


def test_pending_value_wins_until_confirmed(data):
    """A requested setpoint is shown straight away, before the backend agrees."""
    entity = _climate(data, "1_badezimmer")
    entity._pending_temperature = _Pending(25.0, dt_util.utcnow() + PENDING_TIMEOUT)
    assert entity.target_temperature == 25.0

    # Once it goes stale the backend's value is trusted again.
    entity._pending_temperature = _Pending(25.0, dt_util.utcnow() - timedelta(seconds=1))
    entity._handle_coordinator_update = lambda: None  # no hass to write state to
    assert entity._pending_temperature.expired
