from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
import json, math, os, re, uuid, asyncio, hashlib, secrets
import httpx
from PIL import Image, ExifTags
from dotenv import load_dotenv
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
DATA = ROOT / "data"
UPLOADS = ROOT / "uploads"
DATA.mkdir(exist_ok=True); UPLOADS.mkdir(exist_ok=True)
load_dotenv(ROOT / ".env")

app = FastAPI(title="MavunoGuard AI API", version="2.6.0")
app.mount("/static", StaticFiles(directory=PUBLIC), name="static")

DB = DATA / "farms.json"
USERS_DB = DATA / "users.json"
if not DB.exists(): DB.write_text("[]")
if not USERS_DB.exists(): USERS_DB.write_text("[]")

# In-memory sessions token -> user_id
SESSIONS: dict[str, str] = {}

def read_db():
    try: return json.loads(DB.read_text())
    except Exception: return []

def write_db(rows): DB.write_text(json.dumps(rows, indent=2, ensure_ascii=False))

def read_users():
    try: return json.loads(USERS_DB.read_text())
    except Exception: return []

def write_users(users): USERS_DB.write_text(json.dumps(users, indent=2, ensure_ascii=False))

def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000)
    return key.hex(), salt

class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str | None = None

class LoginRequest(BaseModel):
    username: str
    password: str

class GoogleAuthRequest(BaseModel):
    credential: str

class FarmRequest(BaseModel):
    farm_name: str = "My Farm"
    latitude: float
    longitude: float
    farm_size: float | None = None
    farm_unit: str = "Acres"
    crop: str = "maize"
    variety: str | None = None
    planting_date: str
    harvest_date: str | None = None
    soil: str | None = None
    drainage: str | None = None
    flood_history: str | None = None
    slope: str | None = None
    health: str | None = None
    problems: list[str] = []
    noticed: str | None = None

class AnalyzeRequest(FarmRequest):
    use_ai: bool = True

@app.get("/")
def index(): return FileResponse(PUBLIC / "index.html")

@app.get("/sw.js")
def service_worker(): return FileResponse(PUBLIC / "sw.js", media_type="application/javascript")

@app.get("/api/health")
def health():
    return {"ok": True, "service": "MavunoGuard AI", "version": "2.6.0", "time": datetime.now(timezone.utc).isoformat()}

@app.get("/api/config")
def config():
    return {
        "weather": True,
        "satellite_metadata": True,
        "satellite_ndvi": bool(os.getenv("SENTINEL_CLIENT_ID") and os.getenv("SENTINEL_CLIENT_SECRET")),
        "ai": bool(os.getenv("OPENAI_API_KEY")),
        "offline": True,
        "garmin_import": True
    }

# --- Authentication Endpoints ---
@app.post("/api/auth/register")
def register(req: RegisterRequest):
    users = read_users()
    if any(u["username"].lower() == req.username.lower() for u in users):
        raise HTTPException(400, "Username already exists")
    p_hash, salt = hash_password(req.password)
    user = {
        "id": uuid.uuid4().hex,
        "username": req.username,
        "email": req.email,
        "password_hash": p_hash,
        "salt": salt,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    users.append(user)
    write_users(users)
    token = secrets.token_hex(24)
    SESSIONS[token] = user["id"]
    return {"token": token, "user": {"id": user["id"], "username": user["username"], "email": user["email"]}}

@app.post("/api/auth/login")
def login(req: LoginRequest):
    users = read_users()
    user = next((u for u in users if u["username"].lower() == req.username.lower()), None)
    if not user or "password_hash" not in user:
        raise HTTPException(401, "Invalid username or password")
    p_hash, _ = hash_password(req.password, user["salt"])
    if p_hash != user["password_hash"]:
        raise HTTPException(401, "Invalid username or password")
    token = secrets.token_hex(24)
    SESSIONS[token] = user["id"]
    return {"token": token, "user": {"id": user["id"], "username": user["username"], "email": user.get("email")}}

@app.post("/api/auth/google")
def google_auth(req: GoogleAuthRequest):
    # Process Google Credential JWT
    users = read_users()
    # Decode credential header/payload without strict signature verification for client-side token payload
    try:
        parts = req.credential.split('.')
        if len(parts) < 2:
            raise HTTPException(400, "Invalid Google credential format")
        import base64
        padding = '=' * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
        google_id = payload.get("sub")
        email = payload.get("email")
        name = payload.get("name") or payload.get("given_name") or "Google User"
    except Exception:
        raise HTTPException(400, "Failed to parse Google OAuth credential")

    user = next((u for u in users if u.get("google_id") == google_id or (email and u.get("email") == email)), None)
    if not user:
        username = email.split('@')[0] if email else f"google_{google_id[:8]}"
        base_username = username
        idx = 1
        while any(u["username"].lower() == username.lower() for u in users):
            username = f"{base_username}_{idx}"
            idx += 1
        user = {
            "id": uuid.uuid4().hex,
            "google_id": google_id,
            "username": username,
            "email": email,
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        users.append(user)
        write_users(users)

    token = secrets.token_hex(24)
    SESSIONS[token] = user["id"]
    return {"token": token, "user": {"id": user["id"], "username": user["username"], "email": user.get("email")}}

@app.get("/api/auth/me")
def get_me(authorization: str | None = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    token = authorization.split(" ")[1]
    user_id = SESSIONS.get(token)
    if not user_id:
        raise HTTPException(401, "Invalid session token")
    users = read_users()
    user = next((u for u in users if u["id"] == user_id), None)
    if not user:
        raise HTTPException(401, "User not found")
    return {"id": user["id"], "username": user["username"], "email": user.get("email")}

@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        SESSIONS.pop(token, None)
    return {"ok": True}

async def _http_json_get(url, params=None, headers=None, label="weather", timeout=20.0):
    """Fetch JSON with diagnostics suitable for a hosted deployment."""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=8.0),
            follow_redirects=True,
        ) as client:
            r = await client.get(url, params=params, headers=headers or {})
            content_type = r.headers.get("content-type", "")
            body_preview = r.text[:500] if r.text else ""
            if r.status_code >= 400:
                raise RuntimeError(
                    f"{label} returned HTTP {r.status_code}: {body_preview}"
                )
            try:
                data = r.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"{label} returned non-JSON data (content-type: {content_type})"
                ) from exc
            return data, {
                "status": "ok",
                "http_status": r.status_code,
                "content_type": content_type,
                "url": str(r.url).split("?")[0],
            }
    except httpx.TimeoutException as exc:
        raise RuntimeError(f"{label} timed out: {type(exc).__name__}") from exc
    except httpx.RequestError as exc:
        raise RuntimeError(
            f"{label} network error: {type(exc).__name__}: {str(exc)[:180]}"
        ) from exc


async def _open_meteo_get(lat, lon, params, label):
    return await _http_json_get(
        "https://api.open-meteo.com/v1/forecast",
        params=params,
        headers={
            "Accept": "application/json",
            "User-Agent": "MavunoGuard-AI/2.6",
        },
        label=f"Open-Meteo {label}",
    )


async def _met_no_forecast(lat, lon):
    """Fallback forecast using Norway MET's public locationforecast API.

    It is intentionally used only when Open-Meteo fails. The endpoint returns
    hourly data, which we aggregate to daily precipitation and maximum
    temperature so the rest of MavunoGuard keeps the same data contract.
    """
    data, diagnostic = await _http_json_get(
        "https://api.met.no/weatherapi/locationforecast/2.0/compact",
        params={"lat": lat, "lon": lon},
        headers={
            "Accept": "application/json",
            "User-Agent": "MavunoGuard-AI/2.6 https://mavunoguard-ai-live.onrender.com",
        },
        label="MET Norway fallback",
        timeout=25.0,
    )
    props = data.get("properties") or {}
    timeseries = props.get("timeseries") or []
    if not timeseries:
        raise RuntimeError("MET Norway returned zero forecast points")

    by_day = {}
    for item in timeseries:
        t = item.get("time")
        if not t:
            continue
        day = str(t)[:10]
        instant = (item.get("data") or {}).get("instant") or {}
        details = instant.get("details") or {}
        next1 = (item.get("data") or {}).get("next_1_hours") or {}
        n1 = next1.get("details") or {}
        precip = float(n1.get("precipitation_amount") or 0)
        temp = details.get("air_temperature")
        rec = by_day.setdefault(day, {"rain": 0.0, "temps": []})
        rec["rain"] += max(0.0, precip)
        if temp is not None:
            rec["temps"].append(float(temp))

    dates = sorted(by_day)[:14]
    if not dates:
        raise RuntimeError("MET Norway returned no usable daily forecast")

    daily = {
        "time": dates,
        "temperature_2m_max": [max(by_day[d]["temps"]) if by_day[d]["temps"] else None for d in dates],
        "temperature_2m_min": [min(by_day[d]["temps"]) if by_day[d]["temps"] else None for d in dates],
        "precipitation_sum": [round(by_day[d]["rain"], 2) for d in dates],
        "rain_sum": [round(by_day[d]["rain"], 2) for d in dates],
        "precipitation_probability_max": [None for _ in dates],
        "et0_fao_evapotranspiration": [None for _ in dates],
    }
    return {
        "latitude": data.get("geometry", {}).get("coordinates", [lon, lat])[1] if data.get("geometry") else lat,
        "longitude": data.get("geometry", {}).get("coordinates", [lon, lat])[0] if data.get("geometry") else lon,
        "elevation": None,
        "daily": daily,
        "_live": True,
        "_source": "MET Norway fallback forecast",
        "_diagnostic": {**diagnostic, "provider": "MET Norway", "forecast_days": len(dates)},
    }


async def weather_data(lat, lon):
    """Fetch a live forecast with robust fallbacks.

    Open-Meteo is the primary source. The core request deliberately uses only
    the most stable daily variables; optional soil moisture is fetched only
    after the core forecast succeeds. MET Norway is an independent fallback.
    """
    core_params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": "auto",
        "forecast_days": 14,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
    }

    last_error = None
    for attempt in range(3):
        try:
            data, diagnostic = await _open_meteo_get(lat, lon, core_params, "forecast")
            daily = data.get("daily") or {}
            required = ("time", "precipitation_sum", "temperature_2m_max")
            if not all(isinstance(daily.get(k), list) for k in required):
                raise RuntimeError("Open-Meteo returned an incomplete daily forecast")
            if not daily.get("time"):
                raise RuntimeError("Open-Meteo returned zero forecast days")

            # Optional enrichment. It can fail without affecting the forecast.
            daily["rain_sum"] = list(daily.get("precipitation_sum") or [])
            daily["et0_fao_evapotranspiration"] = [None] * len(daily.get("time", []))
            soil_diag = {"status": "not_requested"}
            try:
                soil_params = {
                    "latitude": lat,
                    "longitude": lon,
                    "timezone": "auto",
                    "forecast_days": 14,
                    "hourly": "soil_moisture_0_to_1cm",
                }
                soil_data, soil_diag = await _open_meteo_get(lat, lon, soil_params, "soil moisture")
                hourly = soil_data.get("hourly") or {}
                ht = hourly.get("time") or []
                hm = hourly.get("soil_moisture_0_to_1cm") or []
                soil_by_day = {}
                for t, value in zip(ht, hm):
                    if value is None:
                        continue
                    soil_by_day.setdefault(str(t)[:10], []).append(float(value))
                daily["soil_moisture_0_to_1cm_mean"] = [
                    round(sum(soil_by_day[d]) / len(soil_by_day[d]), 4) if soil_by_day.get(d) else None
                    for d in daily.get("time", [])
                ]
            except Exception as exc:
                daily["soil_moisture_0_to_1cm_mean"] = [None] * len(daily.get("time", []))
                soil_diag = {"status": "optional_failed", "error": str(exc)[:300]}

            data["daily"] = daily
            data["_live"] = True
            data["_source"] = "Open-Meteo forecast"
            data["_diagnostic"] = {
                **diagnostic, "status": "ok", "provider": "Open-Meteo",
                "forecast_days": len(daily.get("time", [])), "soil_moisture": soil_diag,
            }
            return data
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:400]}"
            if attempt < 2:
                await asyncio.sleep(0.8 * (attempt + 1))

    try:
        return await _met_no_forecast(lat, lon)
    except Exception as fallback_exc:
        raise RuntimeError(
            f"Weather providers unavailable. Open-Meteo failed after 3 attempts ({last_error}); "
            f"MET Norway fallback also failed: {str(fallback_exc)[:300]}"
        ) from fallback_exc

def weather_risk(w):
    d = w.get("daily", {})
    rain = d.get("precipitation_sum", []) or []
    temps = d.get("temperature_2m_max", []) or []
    probs = d.get("precipitation_probability_max", []) or []
    dates = d.get("time", []) or []
    soil = d.get("soil_moisture_0_to_1cm_mean", []) or []
    n = min(len(dates), len(rain), len(temps))
    rain7 = sum(float(x or 0) for x in rain[:7])
    rain14 = sum(float(x or 0) for x in rain[:14])
    valid_temps = [float(x) for x in temps[:7] if x is not None]
    max_temp = max(valid_temps) if valid_temps else None
    wet_days = sum(1 for x in rain[:7] if x is not None and float(x) >= 5)
    heavy_days = sum(1 for x in rain[:7] if x is not None and float(x) >= 25)
    drought = min(95, max(5, 70 - rain7 * 2.2 + (max(0, max_temp - 27) * 4 if max_temp is not None else 0)))
    flood = min(95, max(5, heavy_days * 22 + max(0, wet_days - 3) * 9))
    series = []
    for i in range(n):
        series.append({
            "date": dates[i],
            "rain": rain[i],
            "temp": temps[i],
            "prob": probs[i] if i < len(probs) else None,
            "soil_moisture_0_to_1cm": soil[i] if i < len(soil) else None,
        })
    return {
        "live": bool(w.get("_live")),
        "source": w.get("_source"),
        "diagnostic": w.get("_diagnostic"),
        "error": w.get("_error"),
        "rain7_mm": round(rain7, 1),
        "rain14_mm": round(rain14, 1),
        "max_temp_7d_c": round(max_temp, 1) if max_temp is not None else None,
        "wet_days_7d": wet_days,
        "heavy_rain_days_7d": heavy_days,
        "drought_risk": round(drought),
        "flood_risk": round(flood),
        "series": series,
    }

async def satellite_metadata(lat, lon):
    """Discover recent Sentinel-2 L2A scenes via the public CDSE STAC API."""
    url = "https://stac.dataspace.copernicus.eu/v1/search"
    today = date.today()
    start_date = today - timedelta(days=90)
    cloud = float(os.getenv("SATELLITE_MAX_CLOUD", "35"))
    body = {
        "collections": ["sentinel-2-l2a"],
        "intersects": {
            "type": "Point",
            "coordinates": [lon, lat],
        },
        "datetime": f"{start_date.isoformat()}/{today.isoformat()}",
        "query": {"eo:cloud_cover": {"lt": cloud}},
        "limit": 5,
    }
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            r = await client.post(url, json=body, headers={"Accept": "application/geo+json"})
            if r.status_code == 400:
                # Retry with the item collection GET endpoint. This handles
                # stricter STAC POST implementations without hiding valid coverage.
                b = [lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01]
                params = {
                    "bbox": ",".join(f"{x:.7f}" for x in b),
                    "datetime": body["datetime"],
                    "limit": "5",
                }
                r2 = await client.get(
                    "https://stac.dataspace.copernicus.eu/v1/collections/sentinel-2-l2a/items",
                    params=params,
                    headers={"Accept": "application/geo+json"},
                )
                if r2.is_success:
                    r = r2
                else:
                    r.raise_for_status()
            else:
                r.raise_for_status()
            data = r.json()
        items = []
        for feature in data.get("features", []):
            props = feature.get("properties") or {}
            items.append({
                "id": feature.get("id"),
                "datetime": props.get("datetime"),
                "cloud_cover": props.get("eo:cloud_cover"),
            })
        return {
            "available": True,
            "scenes": items,
            "source": "Copernicus Data Space Sentinel-2 L2A STAC",
            "diagnostic": {
                "status": "ok",
                "http_status": r.status_code,
                "scene_count": len(items),
                "max_cloud": cloud,
            },
        }
    except httpx.HTTPStatusError as exc:
        return {
            "available": False,
            "scenes": [],
            "source": "Copernicus Data Space Sentinel-2 L2A STAC",
            "reason": f"Scene search returned HTTP {exc.response.status_code}.",
            "diagnostic": {"status": "http_error", "http_status": exc.response.status_code},
        }
    except Exception as exc:
        return {
            "available": False,
            "scenes": [],
            "source": "Copernicus Data Space Sentinel-2 L2A STAC",
            "reason": f"Scene search failed: {type(exc).__name__}.",
            "diagnostic": {"status": "network_error", "error": str(exc)[:300]},
        }

async def sentinel_token():
    cid = os.getenv("SENTINEL_CLIENT_ID")
    secret = os.getenv("SENTINEL_CLIENT_SECRET")
    if not cid or not secret:
        return None

    url = os.getenv(
        "SENTINEL_TOKEN_URL",
        "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
    )
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": cid,
                    "client_secret": secret,
                },
                headers={"Accept": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
        token = data.get("access_token")
        if not token:
            raise RuntimeError("Copernicus token response did not contain access_token")
        return token
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"Copernicus OAuth returned HTTP {exc.response.status_code}") from exc
    except Exception as exc:
        raise RuntimeError(f"Copernicus OAuth failed: {type(exc).__name__}") from exc

def farm_bbox(lat, lon, farm_size, farm_unit):
    # Approximate a small monitoring window from farm size; exact boundaries can be added later.
    if farm_size and farm_size > 0:
        hectares = farm_size * (0.404686 if farm_unit.lower().startswith("acre") else 1.0)
        side_km = max(0.06, min(1.5, math.sqrt(hectares)))
        half_lat = (side_km/2)/111.0
        half_lon = half_lat / max(0.2, math.cos(math.radians(lat)))
    else:
        half_lat = 0.0025; half_lon = 0.0025
    return [lon-half_lon, lat-half_lat, lon+half_lon, lat+half_lat]

async def ndvi_series(lat, lon, planting_date, farm_size=None, farm_unit="Acres"):
    cid = os.getenv("SENTINEL_CLIENT_ID")
    secret = os.getenv("SENTINEL_CLIENT_SECRET")
    source = "Copernicus Data Space Statistical API"

    if not cid or not secret:
        return {
            "available": False,
            "status": "not_configured",
            "series": [],
            "reason": "Sentinel-2 credentials are not configured on the server.",
            "source": source,
        }

    try:
        token = await sentinel_token()
        d1 = date.today()
        try:
            d0 = date.fromisoformat(planting_date)
        except Exception:
            d0 = d1 - timedelta(days=90)
        if d0 > d1 or (d1 - d0).days < 60:
            d0 = d1 - timedelta(days=90)

        bbox = farm_bbox(lat, lon, farm_size, farm_unit)

        # Statistical API: output IDs are used directly in the response
        # (outputs.data.bands.B0.stats.mean). dataMask excludes nodata/water.
        evalscript = """//VERSION=3
function setup() {
  return {
    input: [{
      bands: ["B04", "B08", "SCL", "dataMask"]
    }],
    output: [
      { id: "data", bands: 1, sampleType: "FLOAT32" },
      { id: "dataMask", bands: 1 }
    ]
  };
}
function evaluatePixel(s) {
  let den = s.B08 + s.B04;
  let valid = den > 0 && s.SCL !== 6 && s.SCL !== 0;
  let ndvi = valid ? (s.B08 - s.B04) / den : 0;
  return {
    data: [ndvi],
    dataMask: [s.dataMask * (valid ? 1 : 0)]
  };
}"""

        payload = {
            "input": {
                "bounds": {
                    "bbox": bbox,
                    "properties": {
                        "crs": "http://www.opengis.net/def/crs/EPSG/0/4326"
                    },
                },
                "data": [{
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "mosaickingOrder": "leastCC"
                    },
                }],
            },
            "aggregation": {
                "timeRange": {
                    "from": d0.isoformat() + "T00:00:00Z",
                    "to": d1.isoformat() + "T23:59:59Z",
                },
                "aggregationInterval": {"of": "P14D"},
                "evalscript": evalscript,
                "resx": 10,
                "resy": 10,
            },
        }

        stats_url = os.getenv(
            "SENTINEL_STATS_URL",
            "https://sh.dataspace.copernicus.eu/statistics/v1",
        )
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            r = await client.post(
                stats_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            r.raise_for_status()
            data = r.json()

        out = []
        for row in data.get("data", []):
            bands = (((row.get("outputs") or {}).get("data") or {}).get("bands") or {})
            band = bands.get("B0") or {}
            stats = band.get("stats") or {}
            mean = stats.get("mean")
            if mean is None:
                continue
            interval = row.get("interval") or {}
            out.append({
                "date": str(interval.get("from", ""))[:10],
                "ndvi": round(float(mean), 3),
                "samples": stats.get("sampleCount", 0),
            })

        if not out:
            return {
                "available": False,
                "status": "authenticated_no_observation",
                "series": [],
                "reason": "Sentinel-2 authentication succeeded, but no valid NDVI observation was returned for this farm and time window.",
                "source": source,
                "diagnostic": {"status": "no_observation", "intervals": len(data.get("data", []))},
            }

        return {
            "available": True,
            "status": "ok",
            "series": out,
            "source": "Sentinel-2 L2A via Copernicus Data Space Statistical API",
            "diagnostic": {"status": "ok", "observations": len(out)},
        }

    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        status = "authentication_failed" if code in (401, 403) else "statistics_http_error"
        return {
            "available": False,
            "status": status,
            "series": [],
            "reason": f"Sentinel-2 statistics returned HTTP {code}.",
            "source": source,
            "diagnostic": {"status": status, "http_status": code},
        }
    except Exception as exc:
        return {
            "available": False,
            "status": "processing_error",
            "series": [],
            "reason": f"Sentinel-2 processing failed: {type(exc).__name__}.",
            "source": source,
            "diagnostic": {"status": "processing_error", "error": str(exc)[:300]},
        }


def rule_risk(req, weather, ndvi):
    wr=weather_risk(weather); score=30
    reasons=[]; actions=[]
    if wr['drought_risk']>=60: score+=22; reasons.append("the coming 7-day rainfall outlook is relatively dry"); actions += ["Check soil moisture early in the morning for the next 3 days.","If the crop is still young and water is available, give small targeted watering rather than flooding the field."]
    if wr['flood_risk']>=45: score+=20; reasons.append("the forecast contains several heavy-rain signals"); actions += ["Open blocked drainage paths before the next heavy rain.","Move harvested produce, seed and fertilizer above floor level before heavy rain."]
    if req.drainage and 'Poor' in req.drainage: score+=10; reasons.append("the farm is reported to have poor drainage"); actions.append("Clear the lowest drainage route and make a shallow outlet where water is collecting.")
    if req.flood_history and 'Frequently' in req.flood_history: score+=8; reasons.append("the farm has frequent flood history");
    if req.health and 'Severely' in req.health: score+=16; reasons.append("the crop is currently severely stressed")
    elif req.health and 'Poor' in req.health: score+=10; reasons.append("the crop is currently in poor condition")
    if 'Dry soil' in req.problems: score+=10; reasons.append("dry soil was reported")
    if 'Waterlogging' in req.problems: score+=10; reasons.append("waterlogging was reported")
    if 'Wilting' in req.problems: score+=6; actions.append("Inspect the wilted plants and check soil moisture 5–10 cm below the surface before irrigating.")
    if 'Yellowing leaves' in req.problems: actions.append("Check whether yellowing starts on older or newer leaves and inspect the underside of leaves for pests before applying any treatment.")
    if 'Pests' in req.problems: actions.append("Inspect 10 plants in a zig-zag across the field and count affected leaves/plants before choosing a control measure.")
    if 'Weeds' in req.problems: actions.append("Remove weeds around the crop rows before they compete strongly for water and nutrients.")
    if ndvi.get('available') and ndvi.get('series'):
        vals=[x['ndvi'] for x in ndvi['series'] if x.get('ndvi') is not None]
        if vals:
            latest=vals[-1]; score += max(0,min(20,int((0.65-latest)*40)))
            if latest < 0.35: reasons.append(f"recent Sentinel-2 NDVI is low ({latest:.2f})"); actions.append("Walk the low-vigor part of the field first; compare it with a healthy patch before changing inputs.")
            elif latest < 0.55: reasons.append(f"recent Sentinel-2 NDVI shows moderate vegetation vigor ({latest:.2f})")
            else: reasons.append(f"recent Sentinel-2 NDVI shows relatively strong vegetation vigor ({latest:.2f})")
    ndvi_trend="not available"
    if ndvi.get("available") and len(ndvi.get("series",[])) >= 2:
        vals=[x.get("ndvi") for x in ndvi["series"] if x.get("ndvi") is not None]
        if len(vals)>=2:
            delta=vals[-1]-vals[0]
            ndvi_trend="rising" if delta>0.04 else "falling" if delta<-0.04 else "stable"
            if ndvi_trend=="falling":
                score+=8; reasons.append("vegetation vigor is falling across the available Sentinel-2 observations"); actions.append("Walk the lower-vigor zone first and compare it with a healthy part of the same field.")
    score=max(0,min(95,score)); title='HIGH' if score>=75 else 'MODERATE-HIGH' if score>=60 else 'MODERATE' if score>=45 else 'LOW'
    crop_guidance = {
        "maize": {
            "name": "maize",
            "focus": "root-zone moisture, leaf stress and early pest pressure",
            "dry": "For maize, check soil moisture around the root zone and watch for leaf rolling during hot periods.",
            "wet": "For maize, inspect low areas for standing water and blocked drainage; waterlogged roots can quickly reduce crop vigor.",
        },
        "beans": {
            "name": "beans",
            "focus": "soil moisture balance, leaf health and pod development",
            "dry": "For beans, check soil moisture and watch for midday wilting; keep weeds from competing for water.",
            "wet": "For beans, check for standing water and leaf disease symptoms after wet weather.",
        },
        "sorghum": {
            "name": "sorghum",
            "focus": "moisture stress and leaf condition",
            "dry": "For sorghum, check soil moisture and look for persistent leaf rolling or wilting.",
            "wet": "For sorghum, inspect low areas for standing water and maintain drainage.",
        },
        "potato": {
            "name": "Irish potato",
            "focus": "soil moisture, drainage and leaf health",
            "dry": "For Irish potato, check soil moisture around the root zone and avoid prolonged dry stress during tuber development.",
            "wet": "For Irish potato, prioritize drainage and inspect leaves after wet weather for worsening symptoms.",
        },
        "banana": {
            "name": "banana",
            "focus": "root-zone moisture, drainage and leaf condition",
            "dry": "For banana, check soil moisture around the mat and maintain organic ground cover where practical.",
            "wet": "For banana, clear blocked drainage and watch low-lying areas for prolonged waterlogging.",
        },
        "vegetables": {
            "name": "vegetables",
            "focus": "frequent moisture checks and leaf health",
            "dry": "For vegetables, check moisture frequently because shallow roots can respond quickly to dry conditions.",
            "wet": "For vegetables, remove standing water where practical and inspect leaves after prolonged wetness.",
        },
    }.get(req.crop, {
        "name": req.crop,
        "focus": "soil moisture and visible crop condition",
        "dry": "Check soil moisture and watch for persistent wilting.",
        "wet": "Check low areas for standing water and maintain drainage.",
    })
    if wr["drought_risk"] >= 45:
        actions.append(crop_guidance["dry"])
    if wr["flood_risk"] >= 35 or req.drainage == "Poor":
        actions.append(crop_guidance["wet"])
    if not actions: actions=["Walk the field along a simple zig-zag route and check 10–20 plants for changes.","Use the next rainfall window for timely field operations rather than working immediately before heavy rain.","Record a photo from the same spot each week so changes can be compared over time."]
    return {"score":score,"title":title,"drought":wr['drought_risk'],"flood":wr['flood_risk'],"crop_stress":min(95,max(10,round(score*0.85))),"reasons":reasons,"actions":actions[:6],"weather":wr,"ndvi_trend":ndvi_trend,"crop_guidance":crop_guidance}

async def ai_explain(req, risk, ndvi):
    key=os.getenv('OPENAI_API_KEY')
    if not key: return None
    model=os.getenv('OPENAI_MODEL','gpt-5.6-luna')
    payload={"model":model,"input":[{"role":"system","content":"You are MavunoGuard AI, a Kenyan smallholder-farm decision assistant. Give practical, low-cost actions based only on the supplied evidence. Never say 'seek agronomic advice'. Never invent measurements. Clearly distinguish observed data from interpretation. Use short simple sentences understandable at a glance. Return JSON with keys headline, explanation, actions (array of strings), watch_next (array), confidence_note."},{"role":"user","content":json.dumps({"farm":req.model_dump(),"risk":risk,"satellite":ndvi})}]}
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r=await client.post('https://api.openai.com/v1/responses',json=payload,headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'}); r.raise_for_status(); data=r.json()
        text=data.get('output_text','')
        if not text:
            for item in data.get('output',[]):
                for c in item.get('content',[]):
                    if c.get('type')=='output_text': text += c.get('text','')
        text=text.strip().removeprefix('```json').removesuffix('```').strip()
        return json.loads(text)
    except Exception: return None

def parse_exif_gps(path):
    try:
        im=Image.open(path)
        exif=im.getexif(); gps_tag=None
        for k,v in ExifTags.TAGS.items():
            if v=="GPSInfo": gps_tag=k; break
        if not gps_tag or gps_tag not in exif: return None
        gps=exif[gps_tag]
        # Pillow may expose numeric keys; map them.
        lat_ref=gps.get(1); lat=gps.get(2); lon_ref=gps.get(3); lon=gps.get(4)
        if not lat or not lon: return None
        def conv(vals):
            return sum(float(x[0])/float(x[1])/(60**i) for i,x in enumerate(vals))
        la=conv(lat); lo=conv(lon)
        if str(lat_ref).upper().startswith('S'): la=-la
        if str(lon_ref).upper().startswith('W'): lo=-lo
        return {"latitude":la,"longitude":lo}
    except Exception: return None

def parse_gpx(path):
    text=Path(path).read_text(errors='ignore'); root=ET.fromstring(text)
    pts=[]
    for el in root.iter():
        if el.tag.lower().endswith('trkpt') or el.tag.lower().endswith('wpt'):
            try: pts.append({"latitude":float(el.attrib['lat']),"longitude":float(el.attrib['lon'])})
            except: pass
    return pts

def parse_kml(path):
    text=Path(path).read_text(errors='ignore'); root=ET.fromstring(text); pts=[]
    for el in root.iter():
        if el.tag.lower().endswith('coordinates') and el.text:
            for item in re.split(r'\s+',el.text.strip()):
                p=item.split(',')
                if len(p)>=2:
                    try: pts.append({"latitude":float(p[1]),"longitude":float(p[0])})
                    except: pass
    return pts

@app.post("/api/garmin/import")
async def garmin_import(file: UploadFile = File(...)):
    suffix=Path(file.filename or '').suffix.lower(); target=UPLOADS/f"{uuid.uuid4().hex}{suffix}"
    target.write_bytes(await file.read())
    points=[]; photo_gps=None
    if suffix=='.gpx': points=parse_gpx(target)
    elif suffix=='.kml': points=parse_kml(target)
    elif suffix in ['.jpg','.jpeg','.tif','.tiff']: photo_gps=parse_exif_gps(target)
    else: raise HTTPException(400,"Use a GPX, KML or geo-tagged JPG/TIFF file from your Garmin workflow.")
    if photo_gps: points=[photo_gps]
    if not points: raise HTTPException(422,"No GPS coordinates were found in this file.")
    lat=sum(p['latitude'] for p in points)/len(points); lon=sum(p['longitude'] for p in points)/len(points)
    return {"ok":True,"latitude":lat,"longitude":lon,"points":points[:500],"count":len(points),"source":"Garmin/GPX-KML/EXIF"}

@app.get("/api/weather")
async def weather_endpoint(latitude: float, longitude: float):
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise HTTPException(400, "Invalid coordinates")
    try:
        return await weather_data(latitude, longitude)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "service": "Weather providers",
                "status": "upstream_unavailable",
                "message": str(exc),
            },
        )


@app.get("/api/diagnostics")
async def diagnostics(
    latitude: float = 0.0,
    longitude: float = 0.0,
    planting_date: str | None = None,
):
    out = {
        "ok": True,
        "weather": {"live": False},
        "sentinel": {
            "configured": bool(
                os.getenv("SENTINEL_CLIENT_ID") and os.getenv("SENTINEL_CLIENT_SECRET")
            )
        },
    }

    try:
        w = await weather_data(latitude, longitude)
        out["weather"] = {
            "live": True,
            "source": w.get("_source"),
            "days": len((w.get("daily") or {}).get("time", [])),
            "status": "ok",
            "diagnostic": w.get("_diagnostic"),
        }
    except Exception as exc:
        out["weather"] = {
            "live": False,
            "status": "upstream_unavailable",
            "error": str(exc),
            "service": "Weather providers",
            "endpoint": "https://api.open-meteo.com/v1/forecast",
        }

    if not out["sentinel"]["configured"]:
        out["sentinel"].update({
            "status": "not_configured",
            "message": "Add SENTINEL_CLIENT_ID and SENTINEL_CLIENT_SECRET in Render Environment Variables.",
        })
        return out

    try:
        token = await sentinel_token()
        out["sentinel"].update({
            "token": bool(token),
            "status": "authenticated",
            "message": "Copernicus OAuth authentication succeeded.",
        })
        if planting_date:
            ndvi = await ndvi_series(latitude, longitude, planting_date)
            out["sentinel"]["statistics"] = {
                "status": ndvi.get("status"),
                "available": ndvi.get("available"),
                "observations": len(ndvi.get("series", [])),
                "reason": ndvi.get("reason"),
                "diagnostic": ndvi.get("diagnostic"),
            }
    except Exception as exc:
        out["sentinel"].update({
            "token": False,
            "status": "authentication_failed",
            "error": str(exc),
        })
    return out

@app.post('/api/analyze')
async def analyze(req: AnalyzeRequest):
    if not (-90<=req.latitude<=90 and -180<=req.longitude<=180): raise HTTPException(400,'Invalid coordinates')
    try: weather=await weather_data(req.latitude,req.longitude)
    except Exception as exc:
        weather={"_live":False,"_source":"Weather unavailable","_error":str(exc),"daily":{"time":[],"precipitation_sum":[],"temperature_2m_max":[],"precipitation_probability_max":[]}}
    ndvi=await ndvi_series(req.latitude,req.longitude,req.planting_date,req.farm_size,req.farm_unit)
    sat_meta=await satellite_metadata(req.latitude,req.longitude)
    risk=rule_risk(req,weather,ndvi)
    risk["elevation_m"]=weather.get("elevation")
    risk["monitoring_window"]="farm-size-derived" if req.farm_size else "default local window"
    ai=await ai_explain(req,risk,ndvi) if req.use_ai else None
    if ai:
        risk['headline']=ai.get('headline',risk['title']); risk['explanation']=ai.get('explanation',''); risk['actions']=ai.get('actions',risk['actions']); risk['watch_next']=ai.get('watch_next',[]); risk['confidence_note']=ai.get('confidence_note','')
    else:
        risk['headline']=risk['title']; risk['explanation']='The score combines the weather outlook, your reported field conditions and—when configured—Sentinel-2 vegetation information. It is a decision aid, not a crop-disease diagnosis.'; risk['watch_next']=['Recheck after the next heavy rainfall or 3–5 dry days.','Compare the same field location over time.']
    result={
        "id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "farm": req.model_dump(),
        "risk": risk,
        "satellite": {**sat_meta, "ndvi": ndvi},
        "live": {
            "weather": bool(weather.get("_live")),
            "sentinel_ndvi": bool(ndvi.get("available")),
            "sentinel_configured": bool(os.getenv("SENTINEL_CLIENT_ID") and os.getenv("SENTINEL_CLIENT_SECRET")),
            "metadata": bool(sat_meta.get("available")),
        },
        "sources": [
            weather.get("_source") if weather.get("_live") else "Weather unavailable",
            "Copernicus Data Space Sentinel-2" if ndvi.get("available") else "Sentinel-2 unavailable",
        ],
    }
    try:
        rows = read_db()
        rows.append(result)
        write_db(rows[-200:])
    except Exception:
        # A filesystem/database write must never turn a successful live analysis
        # into a frontend fallback on hosted read-only/ephemeral filesystems.
        pass
    return result

@app.get('/api/farms')
def farms(): return read_db()[-50:]
