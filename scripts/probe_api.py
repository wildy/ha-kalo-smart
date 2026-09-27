#!/usr/bin/env python3
"""Probe the KALO Smart API against a real account.

Read-only by default: it logs in and dumps what the backend reports, which is
how you confirm the reverse-engineered payloads in docs/API.md match your own
installation before trusting the Home Assistant integration.

    pip install pycognito aiohttp
    python3 scripts/probe_api.py --email you@example.com

Add --write-temp ROOM_ID=21.5 to also exercise one write. Writes travel over
LoRaWAN and take minutes to show up, so the script will not see the change.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

import aiohttp

BACKEND = "https://api.beyonnex.io/homer/"
RESIDENT = "https://api.beyonnex.io/resident-data/"
USER_POOL_ID = "eu-central-1_OQPAAY4lb"
CLIENT_ID = "7r3k7jf5a5eg23cu275ha3vbi5"
APP_VERSION = "1.7.3"

# Fields worth redacting from a dump someone might paste into an issue.
SENSITIVE_KEYS = {"serial", "thingId", "id", "roomGroupId", "displayName", "email"}


def login(email: str, password: str) -> str:
    """Return a Cognito access token."""
    from pycognito import Cognito

    user = Cognito(USER_POOL_ID, CLIENT_ID, username=email)
    user.authenticate(password=password)
    return user.access_token


def redact(obj: object, enabled: bool) -> object:
    """Blank out identifiers so a dump can be shared."""
    if not enabled:
        return obj
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if k in SENSITIVE_KEYS and v else redact(v, enabled))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(v, enabled) for v in obj]
    return obj


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", help="prompted for when omitted")
    parser.add_argument(
        "--redact", action="store_true", help="blank identifiers in the output"
    )
    parser.add_argument(
        "--write-temp",
        metavar="ROOM_ID=TEMP",
        help="set one room's target temperature, to test a write",
    )
    args = parser.parse_args()

    password = args.password or getpass.getpass("KALO Smart password: ")

    loop = asyncio.get_running_loop()
    try:
        token = await loop.run_in_executor(None, login, args.email, password)
    except Exception as err:
        print(f"Login failed: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    print("Login OK\n")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "App-Version": APP_VERSION,
    }

    async with aiohttp.ClientSession(headers=headers) as session:

        async def get(url: str) -> object:
            async with session.get(url) as response:
                print(f"GET {url} -> {response.status}")
                if response.status >= 400:
                    print(f"  {(await response.text())[:300]}")
                    return None
                return await response.json()

        homes = await get(f"{RESIDENT}api/v1/room-groups")
        rooms = await get(f"{BACKEND}v2/rooms")
        devices = await get(f"{BACKEND}v2/devices")

        names: dict[str, object] = {}
        for home in homes or []:
            if home.get("id"):
                names[home["id"]] = await get(
                    f"{RESIDENT}api/v1/room-groups/{home['id']}/room-names"
                )

        print("\n=== room-groups ===")
        print(json.dumps(redact(homes, args.redact), indent=2, ensure_ascii=False))
        print("\n=== room-names ===")
        print(json.dumps(redact(names, args.redact), indent=2, ensure_ascii=False))
        print("\n=== rooms ===")
        print(json.dumps(redact(rooms, args.redact), indent=2, ensure_ascii=False))
        print("\n=== devices ===")
        print(json.dumps(redact(devices, args.redact), indent=2, ensure_ascii=False))

        print("\n=== summary ===")
        for room in rooms or []:
            room_devices = [d for d in devices or [] if d.get("roomId") == room.get("id")]
            print(
                f"  {room.get('id'):30} {room.get('usageType', ''):16} "
                f"now={room.get('averageTemperature')}°C "
                f"target={room.get('maximumTargetTemperature')}°C "
                f"valve={room.get('radiatorStatus')} "
                f"off={room.get('isTurnedOff')} schedule={room.get('isScheduleActive')} "
                f"devices={len(room_devices)}"
            )

        if args.write_temp:
            room_id, _, value = args.write_temp.partition("=")
            url = f"{BACKEND}rooms/{room_id}/temperature"
            async with session.put(url, json=float(value)) as response:
                body = await response.text()
                print(f"\nPUT {url} json={float(value)} -> {response.status} {body[:200]}")
            print(
                "The backend queues this for the next LoRaWAN downlink; check the "
                "app in a few minutes."
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
