# MavunoGuard AI

**MavunoGuard AI: An Enterprise-Grade AI-Powered Climate Risk and Crop Advisory System for Smallholder Farmers.**

MavunoGuard is a robust, scalable decision-support platform designed to protect harvests and empower farmers with data-driven insights. Moving beyond prototype capabilities, this system incorporates enterprise-grade integrations including Gemini AI for tailored agronomic insights, Supabase for scalable cloud data management, and Copernicus Sentinel-2 satellite data for vegetation health tracking. 

## Key Features & Enterprise Capabilities

- **AI-Powered Diagnostics:** Leverages the **Google Gemini API** (`gemini-3.6-flash`) to generate concise, localized, and actionable advice from complex weather and satellite data. Falls back seamlessly if offline or if upstream APIs degrade.
- **Scalable Architecture:** A modular FastAPI backend designed for high concurrency. Data persistence is decoupled, ready to utilize **Supabase** (PostgreSQL) for large-scale distributed deployments alongside a fallback to local JSON storage for edge environments.
- **Advanced Satellite Integration (Sentinel-2):** Integrates directly with the Copernicus Data Space public STAC search for Sentinel-2 L2A scenes, processing NDVI (Normalized Difference Vegetation Index) locally to monitor crop stress dynamically.
- **Robust Weather Forecasting:** Primary integration with Open-Meteo for 14-day forecasts (including soil moisture tracking), with an independent, automatic failover to MET Norway to guarantee uptime.
- **Offline-First Progressive Web App (PWA):** Built for the realities of rural connectivity. The HTML/CSS/JavaScript frontend is fully installable on mobile devices. If a connection drops, the system transitions to an "Offline farm check" utilizing locally captured rules and GPS parameters, synchronizing once back online.
- **Security & Authentication:** Multi-layered authentication via **Supabase Auth** — email/password sign-up & login and Google OAuth 2.0 (One Tap). Supabase issues signed JWTs; the backend validates them server-side via `supabase.auth.get_user()`. No passwords are stored or hashed by the application. Secrets and API keys are strictly managed via environment variables.

## Real-World Problem Solving

MavunoGuard is designed for the genuine conditions faced by smallholders:
1. **Connectivity is fragile:** The offline-first architecture ensures that field workers and farmers can record observations and receive baseline risk scores even without a signal.
2. **Data must be actionable, not just visible:** Instead of presenting raw charts, the Gemini AI engine interprets the data and delivers clear, actionable tasks (e.g., "Check soil moisture 5-10 cm below the surface").
3. **Hardware constraints:** The frontend is extremely lightweight, heavily optimized for performance on low-end mobile devices without requiring large framework payloads. 

## Project Structure

The backend has been modularized for real-world scaling:
- `server/app.py`: Main FastAPI entry point and route definitions.
- `server/routers/auth.py`: Authentication workflows (JWT/session management).
- `server/services/ai.py`: Gemini/OpenAI integration and prompt engineering.
- `server/services/satellite.py`: Copernicus OAuth and STAC/NDVI statistical processing.
- `server/services/weather.py`: Multi-provider weather aggregation and failovers.
- `server/services/analysis.py`: Agronomic rule engine and geospatial parsing (GPX/KML/EXIF).
- `server/models.py`: Pydantic schemas for strict data validation.
- `server/db.py`: Database interfaces bridging Supabase and local edge-storage.

## Local Run Instructions

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn server.app:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000`.

## Environment Variables Configuration

Copy `.env.example` to `.env` and provide your API keys. 

```bash
# AI Integration (Primary: Gemini, Fallback: OpenAI)
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash

# Supabase Integration
# Find these in: Supabase Dashboard → Project Settings → API
SUPABASE_URL=your_supabase_project_url       # e.g. https://xyzxyz.supabase.co
SUPABASE_KEY=your_supabase_anon_key          # "anon public" key
SUPABASE_JWT_SECRET=your_supabase_jwt_secret # Project Settings → API → JWT Secret

# Copernicus Sentinel-2 Satellite Integration
SENTINEL_CLIENT_ID=your_sentinel_client_id
SENTINEL_CLIENT_SECRET=your_sentinel_client_secret
SENTINEL_TOKEN_URL=https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token
SENTINEL_STATS_URL=https://sh.dataspace.copernicus.eu/statistics/v1
SATELLITE_MAX_CLOUD=35

# Google OAuth Integration
# Add the same Client ID to Supabase Dashboard → Authentication → Providers → Google
GOOGLE_CLIENT_ID=your_google_oauth_client_id
```

### Supabase Auth Setup

1. Create a project at [supabase.com](https://supabase.com).
2. Go to **Authentication → Email** and configure whether email confirmation is required.
3. Go to **Authentication → Providers → Google**, enable it, and paste your Google Client ID and Secret.
4. Copy the **Project URL**, **anon/public key**, and **JWT Secret** from **Project Settings → API** into your `.env`.
5. No manual SQL is needed for auth — Supabase manages the `auth.users` table automatically.

## Deployment (Render & Supabase)

1. Provision a PostgreSQL instance via Supabase and add the credentials to your environment variables.
2. Push your repository to GitHub and connect it to Render.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python -m uvicorn server.app:app --host 0.0.0.0 --port $PORT`

## Testing the Infrastructure

Check API health and backend capabilities:
```text
https://YOUR-RENDER-DOMAIN/api/health
```

Test satellite OAuth and live data pipeline:
```text
https://YOUR-RENDER-DOMAIN/api/diagnostics?latitude=-0.6752&longitude=34.7888&planting_date=2026-06-01
```

## Security Posture

- **Zero Client-Side Secrets:** Gemini API keys, Sentinel secrets, and database credentials never leave the FastAPI backend.
- **Supabase Auth:** Passwords are never stored or hashed by the application. Supabase handles credential management and issues signed JWTs. The backend validates every request via `supabase.auth.get_user()`.
- **Offline Data Integrity:** GPS coordinates and farm states are captured securely on the device and synchronized with the backend.
