"""Switch platform for KALO Smart."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import PROFILE_AWAY, PROFILE_SCHEDULE
from .coordinator import KaloSmartConfigEntry, KaloSmartCoordinator
from .entity import KaloDeviceEntity, KaloHomeEntity, KaloRoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KaloSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switches the app offers: away, window detection, child lock."""
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = [
        KaloAwaySwitch(coordinator, home_id) for home_id in coordinator.data.homes
    ]
    entities.extend(
        KaloOpenWindowDetectionSwitch(coordinator, room_id)
        for room_id in coordinator.data.rooms
    )
    entities.extend(
        KaloChildLockSwitch(coordinator, thing_id)
        for thing_id, device in coordinator.data.devices.items()
        # Only thermostats have a dial to lock, and only they carry the flag.
        if device.is_thermostat and device.eui is not None
    )
    async_add_entities(entities)


class KaloAwaySwitch(KaloHomeEntity, SwitchEntity):
    """The home's away profile, which drops every room to a holding level."""

    _attr_translation_key = "away"

    def __init__(self, coordinator: KaloSmartCoordinator, home_id: str) -> None:
        """Initialise the away switch."""
        super().__init__(coordinator, home_id)
        self._attr_unique_id = f"home_{home_id}_away"

    @property
    def is_on(self) -> bool:
        """True while the away profile is selected."""
        return self.home.is_away

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Select the away profile."""
        await self.coordinator.async_apply(
            self.coordinator.client.async_set_room_group_profile(
                self._home_id, PROFILE_AWAY
            )
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Hand the rooms back to their schedules."""
        await self.coordinator.async_apply(
            self.coordinator.client.async_set_room_group_profile(
                self._home_id, PROFILE_SCHEDULE
            )
        )


class KaloOpenWindowDetectionSwitch(KaloRoomEntity, SwitchEntity):
    """Whether a room lowers its valve when it thinks a window opened."""

    _attr_translation_key = "open_window_detection"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: KaloSmartCoordinator, room_id: str) -> None:
        """Initialise the detection switch."""
        super().__init__(coordinator, room_id)
        self._attr_unique_id = f"{room_id}_open_window_detection"

    @property
    def is_on(self) -> bool:
        """True while detection is enabled for the room."""
        return self.room.is_window_detection_enabled

    async def _async_set(self, enabled: bool) -> None:
        await self.coordinator.async_apply(
            self.coordinator.client.async_set_open_window_detection(
                self._room_id, enabled
            )
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable open-window detection."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable open-window detection."""
        await self._async_set(False)


class KaloChildLockSwitch(KaloDeviceEntity, SwitchEntity):
    """The child lock on one thermostat's dial."""

    _attr_translation_key = "child_lock"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: KaloSmartCoordinator, thing_id: str) -> None:
        """Initialise the child lock switch."""
        super().__init__(coordinator, thing_id)
        self._attr_unique_id = f"{thing_id}_child_lock"
        # Addressed by bare EUI rather than thingId, unlike every other call.
        self._eui = self.device.eui

    @property
    def is_on(self) -> bool:
        """True while the dial is locked."""
        return self.device.child_lock

    async def _async_set(self, enabled: bool) -> None:
        await self.coordinator.async_apply(
            self.coordinator.client.async_set_child_lock(self._eui, enabled)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Lock the dial."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unlock the dial."""
        await self._async_set(False)
