"""Client for the KALO Smart (Beyonnex "homer") cloud API.

The API is undocumented; see docs/API.md for how it was recovered and what the
payloads look like.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from json import loads as json_loads
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout

from .const import (
    APP_VERSION,
    BACKEND_BASE_URL,
    COGNITO_CLIENT_ID,
    COGNITO_USER_POOL_ID,
    MODE_OFF,
    MODE_ON,
    RESIDENT_DATA_BASE_URL,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = ClientTimeout(total=30)

# A rejected token looks like either of these, depending on the service.
_AUTH_STATUSES = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN})

# How long to pause when a 429 arrives without a Retry-After header.
DEFAULT_RETRY_AFTER = 5.0
# Beyond this, waiting inside a single update is not worth it: the
# coordinator polls again on its own schedule, and a user-triggered write
# should fail visibly rather than hang.
MAX_RETRY_AFTER_WAIT = 30.0


class KaloSmartError(Exception):
    """Base error for this client."""


class KaloSmartAuthError(KaloSmartError):
    """Credentials were rejected."""


class KaloSmartConnectionError(KaloSmartError):
    """The backend could not be reached."""


class KaloSmartRateLimitError(KaloSmartError):
    """The backend asked us to slow down."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        """Keep how long the backend asked us to wait, for the caller to log."""
        super().__init__(message)
        self.retry_after = retry_after


async def _decode(response: Any, method: str, url: str) -> Any:
    """Turn a successful response into the value the caller expects."""
    try:
        response.raise_for_status()
    except ClientResponseError as err:
        body = await response.text()
        raise KaloSmartError(
            f"{method} {url} failed with {response.status}: {body[:200]}"
        ) from err

    if response.status == HTTPStatus.NO_CONTENT:
        return None

    # Several write endpoints answer 200 with an empty body, and a few reads
    # answer with a bare string rather than JSON, so decode from the text
    # instead of trusting Content-Length.
    body = await response.text()
    if not body:
        return None
    if "application/json" in (response.headers.get("Content-Type") or ""):
        return json_loads(body)
    return body


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header into seconds, or None if it is unusable.

    RFC 9110 allows either a delay in seconds or an HTTP date, and both
    forms turn up in the wild.
    """
    if not value:
        return None
    value = value.strip()

    try:
        seconds = float(value)
    except ValueError:
        pass
    else:
        # Reject nan/inf, which float() otherwise accepts.
        return max(seconds, 0.0) if math.isfinite(seconds) else None

    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    # A date already in the past means "go ahead now".
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)


class KaloSmartApiClient:
    """Talks to the KALO Smart backend on behalf of one account."""

    def __init__(
        self,
        session: ClientSession,
        email: str,
        password: str,
        *,
        hass: Any = None,
    ) -> None:
        """Store credentials; no network traffic happens here."""
        self._session = session
        self._email = email
        self._password = password
        self._hass = hass
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._id_token: str | None = None
        self._auth_lock = asyncio.Lock()

    # -- authentication -----------------------------------------------------

    async def _run_blocking(self, func: Any, *args: Any) -> Any:
        """Run pycognito's blocking boto3 calls off the event loop."""
        if self._hass is not None:
            return await self._hass.async_add_executor_job(func, *args)
        return await asyncio.get_running_loop().run_in_executor(None, func, *args)

    def _authenticate_blocking(self) -> tuple[str, str, str]:
        """Perform the Cognito SRP login. Runs in an executor."""
        # Imported lazily so that merely loading the module does not pull in
        # boto3, and so a missing dependency surfaces as a setup error.
        from botocore.exceptions import ClientError as BotoClientError  # noqa: PLC0415
        from pycognito import Cognito  # noqa: PLC0415
        from pycognito.exceptions import (  # noqa: PLC0415
            ForceChangePasswordException,
            MFAChallengeException,
        )

        user = Cognito(
            COGNITO_USER_POOL_ID,
            COGNITO_CLIENT_ID,
            username=self._email,
        )
        try:
            user.authenticate(password=self._password)
        except MFAChallengeException as err:
            raise KaloSmartAuthError(
                "Account requires multi-factor authentication, which this "
                "integration cannot complete"
            ) from err
        except ForceChangePasswordException as err:
            raise KaloSmartAuthError(
                "Cognito requires this account to set a new password; do that "
                "in the KALO Smart app first"
            ) from err
        except BotoClientError as err:
            code = err.response.get("Error", {}).get("Code", "")
            if code in (
                "NotAuthorizedException",
                "UserNotFoundException",
                "UserNotConfirmedException",
            ):
                raise KaloSmartAuthError(f"Login rejected by Cognito: {code}") from err
            raise KaloSmartConnectionError(f"Cognito error: {code or err}") from err
        except (OSError, ClientError) as err:
            raise KaloSmartConnectionError(f"Cannot reach Cognito: {err}") from err

        return user.access_token, user.refresh_token, user.id_token

    def _refresh_blocking(self) -> tuple[str, str, str]:
        """Exchange the refresh token for a fresh access token."""
        from botocore.exceptions import ClientError as BotoClientError  # noqa: PLC0415
        from pycognito import Cognito  # noqa: PLC0415

        user = Cognito(
            COGNITO_USER_POOL_ID,
            COGNITO_CLIENT_ID,
            username=self._email,
            id_token=self._id_token,
            refresh_token=self._refresh_token,
            access_token=self._access_token,
        )
        try:
            user.renew_access_token()
        except BotoClientError as err:
            code = err.response.get("Error", {}).get("Code", "")
            # A revoked or expired refresh token means a full re-login.
            raise KaloSmartAuthError(f"Token refresh rejected: {code or err}") from err
        except (OSError, ClientError) as err:
            raise KaloSmartConnectionError(f"Cannot reach Cognito: {err}") from err

        return user.access_token, user.refresh_token or self._refresh_token, user.id_token

    async def async_login(self) -> None:
        """Authenticate, replacing any existing session."""
        async with self._auth_lock:
            (
                self._access_token,
                self._refresh_token,
                self._id_token,
            ) = await self._run_blocking(self._authenticate_blocking)
            _LOGGER.debug("Authenticated with Cognito as %s", self._email)

    async def _async_reauthenticate(self) -> None:
        """Refresh the access token, falling back to a full login."""
        async with self._auth_lock:
            if self._refresh_token:
                try:
                    (
                        self._access_token,
                        self._refresh_token,
                        self._id_token,
                    ) = await self._run_blocking(self._refresh_blocking)
                    return
                except KaloSmartAuthError:
                    _LOGGER.debug("Refresh token no longer valid, logging in again")

            (
                self._access_token,
                self._refresh_token,
                self._id_token,
            ) = await self._run_blocking(self._authenticate_blocking)

    # -- transport ----------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        _auth_retry: bool = True,
        _rate_limit_retry: bool = True,
    ) -> Any:
        """Send one authenticated request.

        Renews the token once on a 401/403, and waits out a 429 once when the
        backend asks for a short enough pause.
        """
        if self._access_token is None:
            await self.async_login()

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "App-Version": APP_VERSION,
        }

        try:
            response = await self._session.request(
                method,
                url,
                headers=headers,
                json=json,
                timeout=REQUEST_TIMEOUT,
            )
        except TimeoutError as err:
            raise KaloSmartConnectionError(f"Timeout calling {url}") from err
        except ClientError as err:
            raise KaloSmartConnectionError(f"Error calling {url}: {err}") from err

        wait_for: float | None = None

        async with response:
            if response.status in _AUTH_STATUSES:
                if not _auth_retry:
                    raise KaloSmartAuthError(f"Not authorised for {url}")
                # Renewed below, once the connection is released.

            elif response.status == HTTPStatus.TOO_MANY_REQUESTS:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                wait_for = DEFAULT_RETRY_AFTER if retry_after is None else retry_after
                if not _rate_limit_retry or wait_for > MAX_RETRY_AFTER_WAIT:
                    raise KaloSmartRateLimitError(
                        f"{method} {url} was rate limited"
                        + (
                            f"; backend asked to retry after {retry_after:.0f}s"
                            if retry_after is not None
                            else " without a Retry-After header"
                        ),
                        retry_after=retry_after,
                    )

            else:
                return await _decode(response, method, url)

        # Both retries happen out here, so the connection is back in the pool
        # rather than held open across a renewal or a Retry-After pause.
        if response.status in _AUTH_STATUSES:
            _LOGGER.debug("Got %s from %s, renewing token", response.status, url)
            await self._async_reauthenticate()
            return await self._request(
                method,
                url,
                json=json,
                _auth_retry=False,
                _rate_limit_retry=_rate_limit_retry,
            )

        _LOGGER.debug("Rate limited by %s, waiting %.1fs before one retry", url, wait_for)
        await asyncio.sleep(wait_for if wait_for is not None else DEFAULT_RETRY_AFTER)
        return await self._request(
            method,
            url,
            json=json,
            _auth_retry=_auth_retry,
            _rate_limit_retry=False,
        )

    # -- reads --------------------------------------------------------------

    async def async_get_room_groups(self) -> list[dict[str, Any]]:
        """Return the homes this account can see."""
        result = await self._request("GET", f"{RESIDENT_DATA_BASE_URL}api/v1/room-groups")
        return result or []

    async def async_get_rooms(self) -> list[dict[str, Any]]:
        """Return every room with its current state."""
        result = await self._request("GET", f"{BACKEND_BASE_URL}v2/rooms")
        return result or []

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return every device with its telemetry."""
        result = await self._request("GET", f"{BACKEND_BASE_URL}v2/devices")
        return result or []

    async def async_get_room_names(self, room_group_id: str) -> dict[str, str]:
        """Return the user's custom room names as {room_id: name}."""
        result = await self._request(
            "GET",
            f"{RESIDENT_DATA_BASE_URL}api/v1/room-groups/{room_group_id}/room-names",
        )
        return result or {}

    # -- writes -------------------------------------------------------------

    async def async_set_target_temperature(self, room_id: str, temperature: float) -> None:
        """Set a room's setpoint. The body is a bare JSON number."""
        await self._request(
            "PUT", f"{BACKEND_BASE_URL}rooms/{room_id}/temperature", json=temperature
        )

    async def async_set_room_off(self, room_id: str, off: bool) -> None:
        """Turn a room off, or hand control back to manual/schedule."""
        await self._request(
            "PUT",
            f"{BACKEND_BASE_URL}v2/rooms/{room_id}/operational-mode",
            json={"mode": MODE_OFF if off else MODE_ON},
        )

    async def async_set_schedule_active(self, room_id: str, active: bool) -> None:
        """Enable or disable a room's schedule (auto mode)."""
        await self._request(
            "POST", f"{BACKEND_BASE_URL}schedulers/{room_id}/state", json=active
        )

    async def async_set_open_window_detection(self, room_id: str, enabled: bool) -> None:
        """Toggle open-window detection for a room."""
        await self._request(
            "PUT", f"{BACKEND_BASE_URL}rooms/{room_id}/openWindowDetection", json=enabled
        )

    async def async_set_child_lock(self, eui: str, enabled: bool) -> None:
        """Toggle the child lock on one thermostat, addressed by bare EUI."""
        await self._request(
            "PUT", f"{BACKEND_BASE_URL}devices/{eui}/childLock", json=enabled
        )

    async def async_set_room_group_profile(self, room_group_id: str, profile: str) -> None:
        """Switch a home between the away and schedule profiles."""
        await self._request(
            "PUT",
            f"{RESIDENT_DATA_BASE_URL}api/v1/room-groups/{room_group_id}/profile",
            json={"name": profile},
        )


# thingIds look like `io.beyonnex.connect:eui70b3d52dd3002171`; the childLock
# endpoint is addressed by the bare EUI-64 that follows the `:eui` marker.
_EUI_RE = re.compile(r":eui([0-9a-fA-F]+)\Z")


def device_eui(thing_id: str | None) -> str | None:
    """Extract the bare EUI the childLock endpoint expects from a thingId.

    `io.beyonnex.connect:eui70b3d52dd3002171` -> `70b3d52dd3002171`
    """
    if not thing_id:
        return None
    match = _EUI_RE.search(thing_id)
    return match.group(1) if match else None
