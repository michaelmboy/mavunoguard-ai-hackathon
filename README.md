# MavunoGuard AI

**MavunoGuard AI: An AI-Powered Climate Risk and Crop Advisory System for Smallholder Farmers.**

This version keeps the existing farm assessment, GPS/Leaflet map, risk scoring, charts, optional AI layer and offline fallback, while repairing the live-data pipeline and adding Chrome PWA install capabilities as well as user authentication (Registration, Login, and Google OAuth).

## Features & PWA Capabilities

- **Chrome PWA Install Support:** Web App Manifest and service worker enabled for installation to desktop and mobile home screens, including a custom installation prompt banner and header button.
- **User Authentication:** Support for traditional username/password registration and login (PBKDF2 password hashing) alongside Google OAuth 2.0 integration.
- **Weather:** Open-Meteo `/v1/forecast` using the farm latitude/longitude. No API key is required.
- **Satellite discovery:** Copernicus Data Space public STAC search for recent Sentinel-2 L2A scenes.
- **NDVI:** Copernicus Data Space Sentinel Hub Statistical API using Sentinel-2 L2A B04/B08 and an authenticated OAuth client.
- **Frontend:** HTML/CSS/JavaScript with Leaflet and SVG charts.
- **Backend:** FastAPI + httpx.
- **Deployment:** GitHub → Render.

Open-Meteo supports forecasts up to 16 days, including daily precipitation, temperature and precipitation probability. MavunoGuard requests 14 days and also requests hourly 0–1 cm soil moisture, which is aggregated into daily values when available.

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn server.app:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000`.

## Environment Variables Configuration

Copy `.env.example` (or set these environment variables in your deployment platform like Render):

```bash
# Optional AI Integration
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4o-mini

# Copernicus Sentinel-2 Satellite Integration
SENTINEL_CLIENT_ID=your_sentinel_client_id
SENTINEL_CLIENT_SECRET=your_sentinel_client_secret
SENTINEL_TOKEN_URL=https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token
SENTINEL_STATS_URL=https://sh.dataspace.copernicus.eu/statistics/v1
SATELLITE_MAX_CLOUD=35

# Google OAuth Integration
GOOGLE_CLIENT_ID=your_google_oauth_client_id
```

## Render deployment

Build command:

```text
pip install -r requirements.txt
```

Start command:

```text
python -m uvicorn server.app:app --host 0.0.0.0 --port $PORT
```

The included `render.yaml` contains the same deployment settings.

### Render environment variables

Required for weather: **none**.

Optional AI:
- `OPENAI_API_KEY`
- `OPENAI_MODEL`

Sentinel-2:
- `SENTINEL_CLIENT_ID`
- `SENTINEL_CLIENT_SECRET`
- `SENTINEL_TOKEN_URL`
- `SENTINEL_STATS_URL`
- `SATELLITE_MAX_CLOUD`

Google OAuth:
- `GOOGLE_CLIENT_ID`

Use these Sentinel defaults unless you have a deliberate reason to override them:

```text
SENTINEL_TOKEN_URL=https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token
SENTINEL_STATS_URL=https://sh.dataspace.copernicus.eu/statistics/v1
SATELLITE_MAX_CLOUD=35
```

**Never put Sentinel secrets, OpenAI keys, tokens or passwords in HTML, JavaScript, GitHub, or public repos.** Add secret values only in Render Environment Variables or local `.env` files.

## Testing the live weather service

After deployment, open:

```text
https://YOUR-RENDER-DOMAIN/api/health
```

Then test weather for a farm point:

```text
https://YOUR-RENDER-DOMAIN/api/weather?latitude=-0.6752&longitude=34.7888
```

A successful response contains `daily.time`, `daily.precipitation_sum`, `daily.temperature_2m_max`, `daily.precipitation_probability_max`, and other requested values.

For a compact service diagnostic:

```text
https://YOUR-RENDER-DOMAIN/api/diagnostics?latitude=-0.6752&longitude=34.7888
```

## Testing Sentinel-2 / NDVI

First add the Sentinel OAuth client ID and secret to Render. Do not send the secret through ChatGPT.

Then open:

```text
https://YOUR-RENDER-DOMAIN/api/diagnostics?latitude=-0.6752&longitude=34.7888&planting_date=2026-06-01
```

The diagnostic distinguishes:
- `not_configured` — credentials are missing.
- `authenticated` — OAuth succeeded.
- `authentication_failed` — OAuth failed.
- `authenticated_no_observation` — authentication worked but no valid NDVI was returned for the location/time window.
- statistics errors — authentication worked but the statistics request failed.

The main `/api/analyze` response also reports whether weather and Sentinel-2 NDVI were live.

## Online vs offline behavior

The app deliberately distinguishes three states:

1. **Online + live data:** `Live analysis`.
2. **Online + server/live-data failure:** `Live data temporarily unavailable`. Local field inputs are shown, but no fake weather or satellite values are invented.
3. **Genuinely offline:** `Offline farm check`.

The browser's `navigator.onLine` state is therefore not used as proof that an upstream API is healthy.

## Risk scoring

The existing scoring concept is preserved:
- Dryness
- Heavy rain
- Crop stress
- Overall risk

When live weather is available, forecast rainfall/temperature/probability feed the weather risk. When valid Sentinel-2 NDVI is available, its latest value and trend feed vegetation stress. If a live source is unavailable, the system does not invent a measurement.

## Security

Secrets are read only by the FastAPI backend from environment variables. Credentials are hashed using PBKDF2 with salt. The frontend never receives sensitive keys.

## Current storage note

User credentials are saved in `data/users.json` and farm history is stored in `data/farms.json`, which is appropriate for demonstration/small deployment. For production, move to PostgreSQL/PostGIS.
