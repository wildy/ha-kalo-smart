"""Constants for the KALO Smart integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "kalo_smart"

# --- Backend ---------------------------------------------------------------
# Recovered from the app bundle; see docs/API.md.
BACKEND_BASE_URL: Final = "https://api.beyonnex.io/homer/"
RESIDENT_DATA_BASE_URL: Final = "https://api.beyonnex.io/resident-data/"

COGNITO_REGION: Final = "eu-central-1"
COGNITO_USER_POOL_ID: Final = "eu-central-1_OQPAAY4lb"
COGNITO_CLIENT_ID: Final = "7r3k7jf5a5eg23cu275ha3vbi5"

# Sent by the app on every request. Kept in sync with the app version we mirror.
APP_VERSION: Final = "1.7.3"

# The app itself polls once a minute. Commands travel over LoRaWAN, so state
# changes take minutes to show up regardless; polling faster only adds load.
UPDATE_INTERVAL: Final = timedelta(seconds=60)

# --- Room state ------------------------------------------------------------
ATTR_RADIATOR_STATUS: Final = "radiator_status"
ATTR_USAGE_TYPE: Final = "usage_type"
ATTR_FLOOR_LEVEL: Final = "floor_level"

# `radiatorStatus` — how hard the valve is working. "lew" is the backend's
# spelling for lukewarm.
RADIATOR_STATUS_OFF: Final = "off"
RADIATOR_STATUS_ACTIVE: Final = ("lew", "warm", "hot")

MODE_OFF: Final = "OFF"
MODE_ON: Final = ""

PROFILE_AWAY: Final = "PROFILE_APP_AWAY"
PROFILE_SCHEDULE: Final = "PROFILE_APP_SCHEDULE"
PROFILE_NONE: Final = "NO_PROFILE"

DEVICE_TYPE_THERMOSTAT: Final = "SMART_RADIATOR_THERMOSTAT"

# Human-readable model names for the device registry.
DEVICE_TYPE_NAMES: Final[dict[str, str]] = {
    "COLD_WATER_METER": "Cold water meter",
    "DIRECT_METER_GATEWAY": "Direct meter gateway",
    "HEAT_COST_ALLOCATOR": "Heat cost allocator",
    "HEAT_ENERGY_METER": "Heat energy meter",
    "LORAWAN_GATEWAY": "LoRaWAN gateway",
    "SMART_RADIATOR_THERMOSTAT": "Smart radiator thermostat",
    "SMOKE_DETECTOR": "Smoke detector",
    "WARM_WATER_METER": "Warm water meter",
}

# `usageType` → the room name we fall back to when the account has no custom
# name set for a room.
ROOM_USAGE_NAMES: Final[dict[str, str]] = {
    "BATHROOM": "Bathroom",
    "BEDROOM": "Bedroom",
    "CHILDRENS_ROOM": "Children's room",
    "DINING_ROOM": "Dining room",
    "KITCHEN": "Kitchen",
    "LIVING_ROOM": "Living room",
    "OFFICE": "Office",
    "OTHER": "Room",
}

# Fallbacks for the rare room that reports no limits of its own.
DEFAULT_MIN_TEMP: Final = 6.0
DEFAULT_MAX_TEMP: Final = 28.0
