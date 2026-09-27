"""Binary sensor platform for KALO Smart."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import KaloSmartConfigEntry, KaloSmartCoordinator
from .entity import KaloRoomEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KaloSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up an open-window sensor for every room that detects it."""
    coordinator = entry.runtime_data
    async_add_entities(
        KaloWindowOpenSensor(coordinator, room_id) for room_id in coordinator.data.rooms
    )


class KaloWindowOpenSensor(KaloRoomEntity, BinarySensorEntity):
    """The thermostats' own open-window detection for a room.

    This is inferred from a sudden temperature drop, not a real contact sensor,
    so it lags an actually opened window by a few minutes.
    """

    _attr_device_class = BinarySensorDeviceClass.WINDOW
    _attr_translation_key = "window_open"

    def __init__(self, coordinator: KaloSmartCoordinator, room_id: str) -> None:
        """Initialise the window sensor."""
        super().__init__(coordinator, room_id)
        self._attr_unique_id = f"{room_id}_window_open"

    @property
    def is_on(self) -> bool:
        """True when the room believes a window is open."""
        return self.room.is_window_open

    @property
    def available(self) -> bool:
        """Unavailable while detection is switched off for the room."""
        return super().available and self.room.is_window_detection_enabled
