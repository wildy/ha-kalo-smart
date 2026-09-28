# KALO Smart for Home Assistant

Home Assistant integration for **KALO Smart** radiator thermostats
(KALORIMETA GmbH / Beyonnex), the system behind the
[`de.kalo.smart`](https://play.google.com/store/apps/details?id=de.kalo.smart) app.

There is no public API, so the integration talks to the same undocumented cloud
endpoints the app uses. How they were found and what they look like is written
up in [`docs/API.md`](docs/API.md).

> Unofficial and not affiliated with KALORIMETA GmbH or Beyonnex. The backend
> can change without notice.

## How it works

```
Home Assistant ──HTTPS──► api.beyonnex.io ──► LoRaWAN gateway ──► thermostats
```

The thermostats are **LoRaWAN** devices reached through a gateway in your
building, not directly. Two consequences shape the whole integration:

- **Commands are slow.** A setpoint change is queued in the cloud and pushed on
  the next downlink. Expect **minutes**, not seconds. Entities show the value
  you asked for immediately and let the backend correct them later.
- **State is coarse.** The backend polls its own devices; readings update on the
  order of minutes. Polling faster than the app's own 60 s gains nothing.

## What you get

Entities follow the app's model: you control **rooms**, and the thermostats in
them report their own readings.

| Entity | Per | Notes |
| --- | --- | --- |
| `climate` | room | Setpoint, off / manual / schedule, heating state, humidity |
| `sensor` temperature, humidity | thermostat | Per-device, finer than the room average |
| `sensor` target temperature | thermostat | Diagnostic |
| `sensor` battery | thermostat | Diagnostic |
| `sensor` radiator status | room | The backend's coarse valve state |
| `binary_sensor` window open | room | The thermostats' own detection, not a contact sensor |
| `switch` away | home | The app's away profile |
| `switch` open window detection | room | Config |
| `switch` child lock | thermostat | Config |

### HVAC modes

| Mode | Meaning |
| --- | --- |
| `off` | Room switched off |
| `heat` | Holding a fixed setpoint |
| `auto` | Following the schedule configured in the app |

Setting a temperature while in `auto` overrides the schedule until its next
transition, exactly as in the app. Schedules themselves are not editable from
Home Assistant — use the app.

Devices are registered as **home → room → thermostat**, so rooms land in areas
cleanly and each physical thermostat keeps its own serial number.

## Installation

### HACS

Add this repository as a custom repository of type *Integration*, install
**KALO Smart**, restart Home Assistant, then add the integration from
*Settings → Devices & services*.

### Manual

Copy `custom_components/kalo_smart` into your `config/custom_components/` and
restart Home Assistant.

Requires **Home Assistant 2025.3 or newer** — that is the release that
introduced `AddConfigEntryEntitiesCallback`, which the platforms are built on.

## Configuration

Sign in with the email and password you use in the KALO Smart app. Accounts that
sign in through Google or Apple, or that have MFA enabled, are **not supported**
— the integration authenticates directly against Cognito with a password.

### Options

*Configure* on the integration exposes one setting:

| Option | Default | Range |
| --- | --- | --- |
| Update interval | 60 s | 30 s – 1 h |

The default matches the app. Because readings travel over LoRaWAN and only
reach the backend every few minutes, polling faster buys almost nothing, while
raising it to a few minutes cuts requests with little loss of freshness.
Saving reloads the integration.

## Checking it against your own account

Before trusting the integration, you can confirm the endpoints behave the way
`docs/API.md` describes on your installation:

```bash
pip install pycognito aiohttp
python3 scripts/probe_api.py --email you@example.com --redact
```

It logs in read-only and dumps what the backend reports. Add
`--write-temp ROOM_ID=21.5` to exercise one write.

`--redact` blanks serials and ids so the output is safe to paste into an issue.

## Development

```bash
pip install homeassistant pycognito pytest ruff
pytest tests/          # data-model tests over captured payloads
ruff check .
```

The tests run without Home Assistant fixtures: the payload-stitching logic is a
pure function (`coordinator.build_data`) exercised against the payload shapes in
`tests/fixtures/api_payloads.json`.

To re-derive the API after an app update:

```bash
python3 docs/recover-sources.py path/to/de.kalo.smart.apk -o ./kalo-src
```

The app is Ionic/Angular under Capacitor and ships source maps with
`sourcesContent`, so this recovers its original TypeScript.

## Status

Built from a complete read of the app's own source. **Running against a live
account**, installed through HACS. Also covered by unit tests, and the 2025.3
floor was checked symbol by symbol against that release's source rather than
assumed.

Two details were never settled from the app's source alone, and both are still
guesses unless your own installation says otherwise. If one looks wrong, the
probe script's output is the most useful thing to attach to an issue:

- Whether `{"mode": ""}` is the right way to turn a room back on, or whether
  writing a setpoint alone suffices.
- Whether `battery` is a percentage. The app's own demo data reports `0.0` for
  every device, so the unit is a guess.
