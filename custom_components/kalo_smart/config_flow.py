"""Config flow for KALO Smart."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import (
    KaloSmartApiClient,
    KaloSmartAuthError,
    KaloSmartConnectionError,
    KaloSmartError,
    KaloSmartRateLimitError,
)
from .const import (
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import resolve_scan_interval

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): cv.string})


class KaloSmartConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the KALO Smart config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> KaloSmartOptionsFlow:
        """Return the options flow for this entry."""
        return KaloSmartOptionsFlow()

    async def _async_validate(self, email: str, password: str) -> dict[str, str]:
        """Try the credentials and confirm the account has something to control.

        Returns a dict of form errors, empty when everything checked out.
        """
        client = KaloSmartApiClient(
            async_get_clientsession(self.hass), email, password, hass=self.hass
        )
        try:
            await client.async_login()
            rooms = await client.async_get_rooms()
        except KaloSmartAuthError:
            return {"base": "invalid_auth"}
        except KaloSmartConnectionError:
            return {"base": "cannot_connect"}
        except KaloSmartRateLimitError:
            return {"base": "rate_limited"}
        except KaloSmartError:
            _LOGGER.exception("Unexpected error validating KALO Smart credentials")
            return {"base": "unknown"}

        if not rooms:
            return {"base": "no_rooms"}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

            errors = await self._async_validate(email, user_input[CONF_PASSWORD])
            if not errors:
                return self.async_create_entry(
                    title=email,
                    data={CONF_EMAIL: email, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle an expired or changed password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            errors = await self._async_validate(
                entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={CONF_EMAIL: entry.data[CONF_EMAIL]},
            errors=errors,
        )


class KaloSmartOptionsFlow(OptionsFlowWithReload):
    """Let the user choose how often the backend is polled.

    Reloading on save is handled by OptionsFlowWithReload, so the integration
    registers no update listener of its own.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the poll interval."""
        if user_input is not None:
            # The number selector hands back a float.
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )

        # Go through the same resolver the coordinator uses, so the form never
        # offers a value the selector's own bounds would reject.
        current = int(resolve_scan_interval(self.config_entry.options).total_seconds())
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=10,
                            unit_of_measurement="seconds",
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )
