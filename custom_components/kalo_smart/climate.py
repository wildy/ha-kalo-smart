"""Climate platform for KALO Smart — one thermostat entity per room."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, ClassVar

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import ATTR_FLOOR_LEVEL, ATTR_RADIATOR_STATUS, ATTR_USAGE_TYPE
from .coordinator import KaloSmartConfigEntry, KaloSmartCoordinator
from .entity import KaloRoomEntity

_LOGGER = logging.getLogger(__name__)

# Commands reach the thermostats over LoRaWAN, so the backend keeps reporting
# the old value for a while. Show the requested value for this long before
# giving up and trusting the backend again.
PENDING_TIMEOUT = timedelta(minutes=15)

# The app's temperature dial moves in half degrees.
TEMPERATURE_STEP = 0.5


@dataclass(slots=True)
class _Pending:
    """A value we asked for that the backend has not confirmed yet."""

    value: Any
    deadline: datetime

    @property
    def expired(self) -> bool:
        return dt_util.utcnow() >= self.deadline


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KaloSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one climate entity per room."""
    coordinator = entry.runtime_data
    async_add_entities(
        KaloSmartClimate(coordinator, room_id) for room_id in coordinator.data.rooms
    )


class KaloSmartClimate(KaloRoomEntity, ClimateEntity):
    """A room's heating, as the KALO Smart app presents it."""

    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = TEMPERATURE_STEP
    _attr_hvac_modes: ClassVar[list[HVACMode]] = [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.AUTO,
    ]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )

    def __init__(self, coordinator: KaloSmartCoordinator, room_id: str) -> None:
        """Initialise the room thermostat."""
        super().__init__(coordinator, room_id)
        self._attr_unique_id = f"{room_id}_climate"
        self._pending_temperature: _Pending | None = None
        self._pending_hvac_mode: _Pending | None = None

    # -- state --------------------------------------------------------------

    @property
    def current_temperature(self) -> float | None:
        """Average measured temperature across the room's thermostats."""
        return self.room.current_temperature

    @property
    def current_humidity(self) -> int | None:
        """Average measured humidity across the room's thermostats."""
        humidity = self.room.current_humidity
        return None if humidity is None else round(humidity)

    @property
    def target_temperature(self) -> float | None:
        """The room's setpoint, or what we last asked it to be."""
        if self._pending_temperature is not None:
            return self._pending_temperature.value
        return self.room.target_temperature

    @property
    def min_temp(self) -> float:
        """Lowest setpoint the backend accepts for this room."""
        return self.room.min_temp

    @property
    def max_temp(self) -> float:
        """Highest setpoint the backend accepts for this room."""
        return self.room.max_temp

    @property
    def hvac_mode(self) -> HVACMode:
        """Off, following its schedule, or holding a fixed setpoint."""
        if self._pending_hvac_mode is not None:
            return self._pending_hvac_mode.value
        return self._backend_hvac_mode

    @property
    def _backend_hvac_mode(self) -> HVACMode:
        """The mode the backend currently reports."""
        room = self.room
        if room.is_off:
            return HVACMode.OFF
        return HVACMode.AUTO if room.is_schedule_active else HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction:
        """Whether the valve is actually calling for heat right now."""
        room = self.room
        if room.is_off:
            return HVACAction.OFF
        return HVACAction.HEATING if room.is_heating else HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the room details that have no standard climate attribute."""
        room = self.room
        return {
            ATTR_RADIATOR_STATUS: room.radiator_status,
            ATTR_USAGE_TYPE: room.usage_type,
            ATTR_FLOOR_LEVEL: room.floor_level,
        }

    # -- commands -----------------------------------------------------------

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new setpoint for the room."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        self._pending_temperature = _Pending(
            temperature, dt_util.utcnow() + PENDING_TIMEOUT
        )
        # A room that is off ignores setpoints, so bring it back first.
        calls = []
        if self.room.is_off:
            calls.append(self.coordinator.client.async_set_room_off(self._room_id, False))
            # Clearing the mode hands the room back to whatever it was doing
            # before, so the schedule flag decides where it lands.
            self._pending_hvac_mode = _Pending(
                HVACMode.AUTO if self.room.is_schedule_active else HVACMode.HEAT,
                dt_util.utcnow() + PENDING_TIMEOUT,
            )
        calls.append(
            self.coordinator.client.async_set_target_temperature(
                self._room_id, temperature
            )
        )

        self.async_write_ha_state()
        await self.coordinator.async_apply(*calls)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch the room between off, manual and schedule."""
        if hvac_mode not in self._attr_hvac_modes:
            raise ValueError(f"Unsupported HVAC mode: {hvac_mode}")

        room = self.room
        client = self.coordinator.client
        calls = []

        if hvac_mode is HVACMode.OFF:
            calls.append(client.async_set_room_off(self._room_id, True))
        else:
            if room.is_off:
                calls.append(client.async_set_room_off(self._room_id, False))
            want_schedule = hvac_mode is HVACMode.AUTO
            if room.is_schedule_active != want_schedule:
                calls.append(client.async_set_schedule_active(self._room_id, want_schedule))

        if not calls:
            return

        self._pending_hvac_mode = _Pending(hvac_mode, dt_util.utcnow() + PENDING_TIMEOUT)
        self.async_write_ha_state()
        await self.coordinator.async_apply(*calls)

    async def async_turn_off(self) -> None:
        """Turn the room's heating off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        """Turn the room back on, resuming its schedule if it has one."""
        await self.async_set_hvac_mode(
            HVACMode.AUTO if self.room.is_schedule_active else HVACMode.HEAT
        )

    # -- pending bookkeeping ------------------------------------------------

    @callback
    def _handle_coordinator_update(self) -> None:
        """Drop optimistic values once the backend agrees, or they go stale."""
        if self._room_id in self.coordinator.data.rooms:
            pending = self._pending_temperature
            if pending is not None and (
                pending.expired or self.room.target_temperature == pending.value
            ):
                self._pending_temperature = None

            pending = self._pending_hvac_mode
            if pending is not None and (
                pending.expired or self._backend_hvac_mode is pending.value
            ):
                self._pending_hvac_mode = None

        super()._handle_coordinator_update()
