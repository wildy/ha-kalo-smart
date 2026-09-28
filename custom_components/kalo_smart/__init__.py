"""The KALO Smart integration."""

from __future__ import annotations

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import KaloSmartApiClient, KaloSmartAuthError, KaloSmartError
from .coordinator import KaloSmartConfigEntry, KaloSmartCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: KaloSmartConfigEntry) -> bool:
    """Set up KALO Smart from a config entry."""
    client = KaloSmartApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        hass=hass,
    )

    try:
        await client.async_login()
    except KaloSmartAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except KaloSmartError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = KaloSmartCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KaloSmartConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
