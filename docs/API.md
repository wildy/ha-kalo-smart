# KALO Smart — reverse-engineered API

Recovered from the Android app `de.kalo.smart` v1.7.3 (versionCode 134054942).

## How the reverse engineering was done

The app is an **Ionic/Angular app wrapped in Capacitor** — all business logic is
JavaScript under `assets/public/` in the APK, and the build ships **unstripped
source maps** with `sourcesContent`. That yields the original TypeScript
verbatim, so no bytecode decompilation or TLS interception was needed.

```
apk → unzip assets/public/*.js.map → JSON .sourcesContent → 226 original .ts files
```

`docs/recover-sources.py` reproduces the extraction.

## Topology

```
App ──HTTPS──► api.beyonnex.io ──► LoRaWAN gateway (basement/stairwell) ──► MClimate radiator thermostats
```

Commands are **not** real-time: they are queued in the cloud and pushed to the
thermostats over LoRaWAN. Expect a round trip on the order of minutes, and
reported state to lag behind a write. The app itself polls every **60 s**.

The backend is a white-label platform ("homer") operated by Beyonnex; the app is
the `kalo` tenant of it.

## Hosts

| Purpose | Base URL |
| --- | --- |
| Device/room control ("homer") | `https://api.beyonnex.io/homer/` |
| Resident data | `https://api.beyonnex.io/resident-data/` |
| Cognito hosted UI | `https://resident-services-auth-prod.beyonnex.io/` |
| Public assets | `https://homer-assets-prod.s3.eu-central-1.amazonaws.com/` |

## Authentication

AWS Cognito user pool, SRP (`USER_SRP_AUTH`) via `amazon-cognito-identity-js`:

| Field | Value |
| --- | --- |
| Region | `eu-central-1` |
| User pool ID | `eu-central-1_OQPAAY4lb` |
| App client ID | `7r3k7jf5a5eg23cu275ha3vbi5` |
| Client secret | none (public client) |

Federated Google/Apple login uses OAuth2 authorization-code + PKCE against
`{cognitoDomain}oauth2/authorize/` and `/oauth2/token/`, scopes
`profile email openid`, redirect `homerapp://sso-auth/auth-return/`.

Every API request carries:

```
Authorization: Bearer <cognito access token>
Content-Type: application/json
App-Version: 1.7.3
```

## Endpoints

Request bodies are frequently **bare JSON scalars** (`22.5`, `true`), not objects.

### Rooms — `api.beyonnex.io/homer/`

| Method | Path | Body | Purpose |
| --- | --- | --- | --- |
| GET | `v2/rooms` | — | All rooms with state |
| PUT | `rooms/{roomId}/temperature` | `22.5` | Set target temperature |
| PUT | `rooms/{roomId}/openWindowDetection` | `true` | Toggle open-window detection |
| PUT | `v2/rooms/{roomId}/operational-mode` | `{"mode":"OFF"}` | Turn room off (`""` = manual/auto) |
| PATCH | `rooms/{roomId}` | Room | Update room |
| GET | `schedulers/room/{roomId}` | — | Schedule for room |
| PUT | `v2/rooms/{roomId}/schedule` | Schedule | Replace schedule |
| POST | `schedulers/{roomId}/state` | `true` | Enable/disable schedule (auto mode) |

### Devices — `api.beyonnex.io/homer/`

| Method | Path | Body | Purpose |
| --- | --- | --- | --- |
| GET | `v2/devices` | — | All devices with telemetry |
| PUT | `devices/{eui}/childLock` | `true` | Toggle child lock |

`{eui}` is the bare hex EUI — `thingId.split("eui")[1]`, i.e. for
`io.beyonnex.connect:eui70b3d52dd3002171` it is `70b3d52dd3002171`.

### Room groups (homes) — `api.beyonnex.io/resident-data/`

| Method | Path | Body | Purpose |
| --- | --- | --- | --- |
| GET | `api/v1/room-groups` | — | Homes the user can access |
| GET | `api/v1/room-groups/{id}/room-names` | — | `{roomId: displayName}` map |
| PUT | `api/v1/room-groups/{id}/profile` | `{"name":"PROFILE_APP_AWAY"}` | Away / schedule profile |
| PATCH | `api/v1/room-groups/{id}/name` | RoomGroup | Rename home |
| PUT | `api/v1/room-groups/{id}/room-names/{roomId}` | raw string | Rename room |
| GET | `api/v1/room-groups/{id}/app-users?userType=MAIN_USER` | — | Main user |
| GET/POST/DELETE | `api/v1/room-groups/{id}/room-group-invites` | `{"email":...}` | Household invites |

Other: `api/v1/room-group-registrations` (+`/qr`), `api/v1/waitlist`,
`api/v1/account-deletion-requests`, `api/v1/app-user/consent`,
`api/v1/app-user/newsletter-subscription/`, `homer/registration-code-validation`,
`homer/zendesk-support-jwt`, `homer/logs`, `homer/user-devices`.

## Models

### Room (`GET v2/rooms`)

```jsonc
{
  "id": "1_badezimmer",
  "roomGroupId": "DunderMifflinScranton",
  "displayName": "",            // empty — resolve via room-names map
  "floorLevel": "",
  "usageType": "BATHROOM",      // BATHROOM KITCHEN CHILDRENS_ROOM OFFICE LIVING_ROOM DINING_ROOM BEDROOM
  "averageTemperature": 22.0,   // across the room's thermostats
  "averageHumidity": 47.0,
  "maximumTargetTemperature": 22.0,  // the room's setpoint
  "maximumTemperature": 28,
  "minimumTemperature": 6,
  "radiatorStatus": "off",      // off | lew | warm | hot  → valve activity
  "isScheduleActive": false,    // schedule (auto) vs manual
  "isTurnedOff": false,
  "isWindowOpen": false,
  "isWindowOpenDetectionEnabled": true,
  "schedule": { "MONDAY": { "transitions": [...] }, ... }
}
```

### Device (`GET v2/devices`)

```jsonc
{
  "serial": "VA0123456789",
  "thingId": "io.beyonnex.connect:eui70b3d52dd3002171",
  "roomId": "1_badezimmer",
  "manufacturer": "MClimate",
  "displayName": "io.beyonnex.connect:eui70b3d52dd3002171",
  "type": "SMART_RADIATOR_THERMOSTAT",
  "temperature": 22.0,
  "targetTemperature": 23.0,
  "humidity": 56.0,
  "battery": 0.0,
  "childLock": false
}
```

Device types: `SMART_RADIATOR_THERMOSTAT`, `LORAWAN_GATEWAY`, `SMOKE_DETECTOR`,
`HEAT_COST_ALLOCATOR`, `HEAT_ENERGY_METER`, `COLD_WATER_METER`,
`WARM_WATER_METER`, `DIRECT_METER_GATEWAY`.

### RoomGroup (`GET api/v1/room-groups`)

```jsonc
{
  "id": "...", "displayName": "...", "userDeviceName": "...",
  "roomIds": ["1_badezimmer"], "tenancyType": "MAIN", "userType": "MAIN_USER",
  "profile": { "name": "PROFILE_APP_SCHEDULE", "isInSync": true }
}
```

Profiles: `PROFILE_APP_AWAY`, `PROFILE_APP_SCHEDULE`, `NO_PROFILE`.

### Schedule

Per weekday (`MONDAY`…`SUNDAY`) a list of transitions, **max 6 per day**
(hard backend limit), times in 15-minute steps:

```jsonc
{ "minutesAfterMidnight": 360, "setPointCelsius": 21.0 }
```

The active setpoint is the last transition whose `minutesAfterMidnight` is
`<= now`. A `minutesAfterMidnight: 0` transition must always exist.

## Mode semantics

| State | `isTurnedOff` | `isScheduleActive` |
| --- | --- | --- |
| Off | `true` | — |
| Manual (fixed setpoint) | `false` | `false` |
| Auto (following schedule) | `false` | `true` |

Writing a target temperature while a schedule is active overrides it until the
next transition. Turning a room back on is done by clearing the operational mode
(`{"mode":""}`) or by enabling the schedule.

## Demo mode

The app ships full mock data and a demo mode toggled by `localStorage.isDemo`,
which is how the exact response shapes above could be confirmed without
touching a live account.
