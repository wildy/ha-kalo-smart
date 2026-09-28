"""Polling coordinator for KALO Smart."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    KaloSmartApiClient,
    KaloSmartAuthError,
    KaloSmartError,
    KaloSmartRateLimitError,
    device_eui,
)
from .const import (
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DEVICE_TYPE_THERMOSTAT,
    DOMAIN,
    PROFILE_AWAY,
    RADIATOR_STATUS_ACTIVE,
    ROOM_USAGE_NAMES,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type KaloSmartConfigEntry = ConfigEntry[KaloSmartCoordinator]


@dataclass(slots=True)
class KaloRoom:
    """One room, which is what the app actually controls."""

    raw: dict[str, Any]
    name: str
    devices: list[KaloDevice] = field(default_factory=list)

    @property
    def id(self) -> str:
        """The backend's room id, stable across renames."""
        return self.raw["id"]

    @property
    def room_group_id(self) -> str | None:
        """Id of the home this room belongs to."""
        return self.raw.get("roomGroupId")

    @property
    def current_temperature(self) -> float | None:
        """Measured temperature, averaged over the room's thermostats."""
        return self.raw.get("averageTemperature")

    @property
    def current_humidity(self) -> float | None:
        """Measured humidity, averaged over the room's thermostats."""
        return self.raw.get("averageHumidity")

    @property
    def target_temperature(self) -> float | None:
        """The room's setpoint."""
        return self.raw.get("maximumTargetTemperature")

    @property
    def min_temp(self) -> float:
        """Lowest setpoint the backend accepts for this room."""
        return self.raw.get("minimumTemperature") or DEFAULT_MIN_TEMP

    @property
    def max_temp(self) -> float:
        """Highest setpoint the backend accepts for this room."""
        return self.raw.get("maximumTemperature") or DEFAULT_MAX_TEMP

    @property
    def is_off(self) -> bool:
        """True when the room's heating is switched off entirely."""
        return bool(self.raw.get("isTurnedOff"))

    @property
    def is_schedule_active(self) -> bool:
        """True when the room follows its schedule rather than a fixed setpoint."""
        return bool(self.raw.get("isScheduleActive"))

    @property
    def is_heating(self) -> bool:
        """True while the valve is open, per the backend's coarse status."""
        return (self.raw.get("radiatorStatus") or "").lower() in RADIATOR_STATUS_ACTIVE

    @property
    def radiator_status(self) -> str | None:
        """The backend's coarse valve state: off, lew, warm or hot."""
        return self.raw.get("radiatorStatus")

    @property
    def is_window_open(self) -> bool:
        """True when the room believes a window is open."""
        return bool(self.raw.get("isWindowOpen"))

    @property
    def is_window_detection_enabled(self) -> bool:
        """True when open-window detection is switched on."""
        return bool(self.raw.get("isWindowOpenDetectionEnabled"))

    @property
    def usage_type(self) -> str | None:
        """What the room is used for, e.g. BATHROOM."""
        return self.raw.get("usageType")

    @property
    def floor_level(self) -> str | None:
        """Floor the room is on, when the account records one."""
        return self.raw.get("floorLevel") or None


@dataclass(slots=True)
class KaloDevice:
    """One physical device behind the LoRaWAN gateway."""

    raw: dict[str, Any]

    @property
    def thing_id(self) -> str:
        """Fully qualified device id, the key everything else uses."""
        return self.raw["thingId"]

    @property
    def eui(self) -> str | None:
        """Bare EUI-64, which is how the child-lock endpoint addresses the device."""
        return device_eui(self.raw.get("thingId"))

    @property
    def serial(self) -> str | None:
        """Serial number printed on the device."""
        return self.raw.get("serial")

    @property
    def room_id(self) -> str | None:
        """Id of the room the device is mounted in, if any."""
        return self.raw.get("roomId")

    @property
    def type(self) -> str | None:
        """Device type, e.g. SMART_RADIATOR_THERMOSTAT."""
        return self.raw.get("type")

    @property
    def is_thermostat(self) -> bool:
        """True for radiator thermostats, the only controllable device type."""
        return self.type == DEVICE_TYPE_THERMOSTAT

    @property
    def manufacturer(self) -> str | None:
        """Hardware vendor, e.g. MClimate."""
        return self.raw.get("manufacturer")

    @property
    def temperature(self) -> float | None:
        """Temperature this device measures."""
        return self.raw.get("temperature")

    @property
    def target_temperature(self) -> float | None:
        """Setpoint this device is working towards."""
        return self.raw.get("targetTemperature")

    @property
    def humidity(self) -> float | None:
        """Humidity this device measures."""
        return self.raw.get("humidity")

    @property
    def battery(self) -> float | None:
        """Battery level reported by the device."""
        return self.raw.get("battery")

    @property
    def child_lock(self) -> bool:
        """True while the device's dial is locked."""
        return bool(self.raw.get("childLock"))


@dataclass(slots=True)
class KaloHome:
    """A room group — one dwelling."""

    raw: dict[str, Any]

    @property
    def id(self) -> str:
        """The backend's room-group id."""
        return self.raw["id"]

    @property
    def name(self) -> str:
        """The home's display name."""
        return self.raw.get("displayName") or "Home"

    @property
    def profile(self) -> str | None:
        """Active profile, e.g. PROFILE_APP_AWAY."""
        return (self.raw.get("profile") or {}).get("name")

    @property
    def is_away(self) -> bool:
        """True while the away profile is selected."""
        return self.profile == PROFILE_AWAY

    @property
    def profile_in_sync(self) -> bool:
        """False while the backend is still pushing the profile to the thermostats."""
        return bool((self.raw.get("profile") or {}).get("isInSync", True))


@dataclass(slots=True)
class KaloSmartData:
    """Everything one refresh produced."""

    homes: dict[str, KaloHome] = field(default_factory=dict)
    rooms: dict[str, KaloRoom] = field(default_factory=dict)
    devices: dict[str, KaloDevice] = field(default_factory=dict)


def build_data(
    homes_raw: list[dict[str, Any]],
    rooms_raw: list[dict[str, Any]],
    devices_raw: list[dict[str, Any]],
    room_names: dict[str, dict[str, str]],
) -> KaloSmartData:
    """Stitch the three feeds into one snapshot.

    Kept free of Home Assistant and network concerns so it can be tested
    against captured payloads.
    """
    homes = {home["id"]: KaloHome(home) for home in homes_raw if home.get("id")}

    devices: dict[str, KaloDevice] = {}
    for raw in devices_raw:
        if raw.get("thingId"):
            device = KaloDevice(raw)
            devices[device.thing_id] = device

    rooms: dict[str, KaloRoom] = {}
    for raw in rooms_raw:
        room_id = raw.get("id")
        if not room_id:
            continue
        names = room_names.get(raw.get("roomGroupId") or "", {})
        # The rooms feed leaves displayName empty; the real name lives on the
        # resident-data service, and failing that we label by usage type.
        name = (
            names.get(room_id)
            or raw.get("displayName")
            or ROOM_USAGE_NAMES.get(raw.get("usageType") or "")
            or room_id
        )
        room = KaloRoom(raw=raw, name=name)
        room.devices = [d for d in devices.values() if d.room_id == room_id]
        rooms[room_id] = room

    return KaloSmartData(homes=homes, rooms=rooms, devices=devices)


class KaloSmartCoordinator(DataUpdateCoordinator[KaloSmartData]):
    """Fetches rooms, devices and homes together and stitches them up."""

    config_entry: KaloSmartConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: KaloSmartConfigEntry,
        client: KaloSmartApiClient,
    ) -> None:
        """Set up the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.client = client
        # Room names live on a different service and effectively never change,
        # so they are fetched once per home and cached.
        self._room_names: dict[str, dict[str, str]] = {}

    async def _async_update_data(self) -> KaloSmartData:
        """Pull the full picture, the way the app's own polling does."""
        try:
            homes_raw, rooms_raw, devices_raw = await asyncio.gather(
                self.client.async_get_room_groups(),
                self.client.async_get_rooms(),
                self.client.async_get_devices(),
            )
        except KaloSmartAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except KaloSmartError as err:
            raise UpdateFailed(str(err)) from err

        for home in homes_raw:
            home_id = home.get("id")
            if home_id and home_id not in self._room_names:
                try:
                    self._room_names[home_id] = await self.client.async_get_room_names(home_id)
                except KaloSmartError as err:
                    # Names are cosmetic; fall back to the usage type.
                    _LOGGER.debug("Could not fetch room names for %s: %s", home_id, err)
                    self._room_names[home_id] = {}

        data = build_data(homes_raw, rooms_raw, devices_raw, self._room_names)
        _LOGGER.debug(
            "Refreshed %d home(s), %d room(s), %d device(s)",
            len(data.homes),
            len(data.rooms),
            len(data.devices),
        )
        return data

    def invalidate_room_names(self) -> None:
        """Drop the cached room names so the next refresh re-fetches them."""
        self._room_names.clear()

    async def async_apply(self, *calls: Any) -> None:
        """Run write calls, then refresh.

        Writes are queued in the cloud and forwarded over LoRaWAN, so the
        refresh that follows usually still reports the old value. Entities
        write their new value optimistically and let the next poll correct it.
        """
        pending = list(calls)
        try:
            while pending:
                await pending.pop(0)
        except KaloSmartAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except KaloSmartRateLimitError as err:
            raise HomeAssistantError(
                "KALO Smart is rate limiting requests right now; try again shortly"
            ) from err
        finally:
            # Whatever we never got to must be closed, or Python warns about a
            # coroutine that was never awaited.
            for call in pending:
                call.close()

        await self.async_request_refresh()
