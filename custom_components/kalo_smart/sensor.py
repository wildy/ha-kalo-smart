"""Sensor platform for KALO Smart."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import KaloDevice, KaloRoom, KaloSmartConfigEntry, KaloSmartCoordinator
from .entity import KaloDeviceEntity, KaloRoomEntity


@dataclass(frozen=True, kw_only=True)
class KaloDeviceSensorDescription(SensorEntityDescription):
    """Describes a sensor reading one field off a device."""

    value_fn: Callable[[KaloDevice], float | None]


@dataclass(frozen=True, kw_only=True)
class KaloRoomSensorDescription(SensorEntityDescription):
    """Describes a sensor reading one field off a room."""

    value_fn: Callable[[KaloRoom], str | float | None]


DEVICE_SENSORS: tuple[KaloDeviceSensorDescription, ...] = (
    KaloDeviceSensorDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.temperature,
    ),
    KaloDeviceSensorDescription(
        key="target_temperature",
        translation_key="target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.target_temperature,
    ),
    KaloDeviceSensorDescription(
        key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.humidity,
    ),
    KaloDeviceSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda device: device.battery,
    ),
)

ROOM_SENSORS: tuple[KaloRoomSensorDescription, ...] = (
    KaloRoomSensorDescription(
        key="radiator_status",
        translation_key="radiator_status",
        device_class=SensorDeviceClass.ENUM,
        # The backend's own spelling; "lew" is its abbreviation for lukewarm.
        options=["off", "lew", "warm", "hot"],
        value_fn=lambda room: (room.radiator_status or "").lower() or None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KaloSmartConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for every room and every device that reports telemetry."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        KaloRoomSensor(coordinator, room_id, description)
        for room_id in coordinator.data.rooms
        for description in ROOM_SENSORS
    ]

    for thing_id, device in coordinator.data.devices.items():
        entities.extend(
            KaloDeviceSensor(coordinator, thing_id, description)
            for description in DEVICE_SENSORS
            # Thermostats always get the full set, since a reading that is
            # missing at startup will arrive on a later poll. Meters and
            # gateways share the same feed but never report telemetry, so they
            # only get sensors for fields they actually populate.
            if device.is_thermostat or description.value_fn(device) is not None
        )

    async_add_entities(entities)


class KaloRoomSensor(KaloRoomEntity, SensorEntity):
    """A sensor reading one field off a room."""

    entity_description: KaloRoomSensorDescription

    def __init__(
        self,
        coordinator: KaloSmartCoordinator,
        room_id: str,
        description: KaloRoomSensorDescription,
    ) -> None:
        """Initialise the room sensor."""
        super().__init__(coordinator, room_id)
        self.entity_description = description
        self._attr_unique_id = f"{room_id}_{description.key}"

    @property
    def native_value(self) -> str | float | None:
        """Return the current reading."""
        return self.entity_description.value_fn(self.room)


class KaloDeviceSensor(KaloDeviceEntity, SensorEntity):
    """A sensor reading one field off a device."""

    entity_description: KaloDeviceSensorDescription

    def __init__(
        self,
        coordinator: KaloSmartCoordinator,
        thing_id: str,
        description: KaloDeviceSensorDescription,
    ) -> None:
        """Initialise the device sensor."""
        super().__init__(coordinator, thing_id)
        self.entity_description = description
        self._attr_unique_id = f"{thing_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        """Return the current reading."""
        return self.entity_description.value_fn(self.device)
