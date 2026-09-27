"""Shared entity plumbing for KALO Smart."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEVICE_TYPE_NAMES, DOMAIN
from .coordinator import KaloDevice, KaloHome, KaloRoom, KaloSmartCoordinator

# The registry mirrors the real topology: home -> room -> thermostat.


class KaloHomeEntity(CoordinatorEntity[KaloSmartCoordinator]):
    """An entity belonging to a whole dwelling."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KaloSmartCoordinator, home_id: str) -> None:
        """Initialise from the home's id."""
        super().__init__(coordinator)
        self._home_id = home_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"home_{home_id}")},
            name=self.home.name,
            manufacturer="KALORIMETA",
            model="KALO Smart home",
        )

    @property
    def home(self) -> KaloHome:
        """Return the current snapshot of this home."""
        return self.coordinator.data.homes[self._home_id]

    @property
    def available(self) -> bool:
        """Only available while the home is still in the account."""
        return super().available and self._home_id in self.coordinator.data.homes


class KaloRoomEntity(CoordinatorEntity[KaloSmartCoordinator]):
    """An entity belonging to a room, which is the unit the app controls."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KaloSmartCoordinator, room_id: str) -> None:
        """Initialise from the room's id."""
        super().__init__(coordinator)
        self._room_id = room_id

        room = self.room
        via_device = (
            (DOMAIN, f"home_{room.room_group_id}") if room.room_group_id else None
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"room_{room_id}")},
            name=room.name,
            manufacturer="KALORIMETA",
            model="KALO Smart room",
            suggested_area=room.name,
            via_device=via_device,
        )

    @property
    def room(self) -> KaloRoom:
        """Return the current snapshot of this room."""
        return self.coordinator.data.rooms[self._room_id]

    @property
    def available(self) -> bool:
        """Only available while the room is still reported by the backend."""
        return super().available and self._room_id in self.coordinator.data.rooms


class KaloDeviceEntity(CoordinatorEntity[KaloSmartCoordinator]):
    """An entity belonging to one physical device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: KaloSmartCoordinator, thing_id: str) -> None:
        """Initialise from the device's thingId."""
        super().__init__(coordinator)
        self._thing_id = thing_id

        device = self.device
        via_device = (DOMAIN, f"room_{device.room_id}") if device.room_id else None
        room = coordinator.data.rooms.get(device.room_id or "")
        model = DEVICE_TYPE_NAMES.get(device.type or "", device.type)

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, thing_id)},
            # The backend's displayName is just the thingId again, so build a
            # name a human can actually place: "Living room thermostat".
            name=f"{room.name} {model.lower()}" if room and model else thing_id,
            manufacturer=device.manufacturer or "KALORIMETA",
            model=model,
            serial_number=device.serial,
            via_device=via_device,
        )

    @property
    def device(self) -> KaloDevice:
        """Return the current snapshot of this device."""
        return self.coordinator.data.devices[self._thing_id]

    @property
    def available(self) -> bool:
        """Only available while the device is still reported by the backend."""
        return super().available and self._thing_id in self.coordinator.data.devices
