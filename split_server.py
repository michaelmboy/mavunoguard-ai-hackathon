import os
from pathlib import Path

SERVER = Path("server")
SERVER.mkdir(exist_ok=True)

models_code = """from pydantic import BaseModel
from typing import List, Optional

class RegisterRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    phone number:str

class LoginRequest(BaseModel):
    username: str
    password: str

class GoogleAuthRequest(BaseModel):
    credential: str

class FarmRequest(BaseModel):
    farm_name: str = "My Farm"
    latitude: float
    longitude: float
    farm_size: Optional[float] = None
    farm_unit: str = "Acres"
    crop: str = "maize"
    variety: Optional[str] = None
    planting_date: str
    harvest_date: Optional[str] = None
    soil: Optional[str] = None
    drainage: Optional[str] = None
    flood_history: Optional[str] = None
    slope: Optional[str] = None
    health: Optional[str] = None
    problems: List[str] = []
    noticed: Optional[str] = None

class AnalyzeRequest(FarmRequest):
    use_ai: bool = True
"""

db_code = """import json
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

DB = DATA / "farms.json"
USERS_DB = DATA / "users.json"

if not DB.exists(): DB.write_text("[]")
if not USERS_DB.exists(): USERS_DB.write_text("[]")

def read_db():
    try: return json.loads(DB.read_text())
    except Exception: return []

def write_db(rows):
    DB.write_text(json.dumps(rows, indent=2, ensure_ascii=False))

def read_users():
    try: return json.loads(USERS_DB.read_text())
    except Exception: return []

def write_users(users):
    USERS_DB.write_text(json.dumps(users, indent=2, ensure_ascii=False))

# Optional Supabase Integration placeholder
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

def get_supabase_client():
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            from supabase import create_client
            return create_client(SUPABASE_URL, SUPABASE_KEY)
        except ImportError:
            pass
    return None
"""

auth_code = """from fastapi import APIRouter, HTTPException, Header
import uuid, datetime, hashlib, secrets, json
from datetime import timezone
from .models import RegisterRequest, LoginRequest, GoogleAuthRequest
from .db import read_users, write_users

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSIONS: dict[str, str] = {}

def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000)
    return key.hex(), salt

@router.post("/register")
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
        "created_at": datetime.datetime.now(timezone.utc).isoformat()
    }
    users.append(user)
    write_users(users)
    token = secrets.token_hex(24)
    SESSIONS[token] = user["id"]
    return {"token": token, "user": {"id": user["id"], "username": user["username"], "email": user["email"]}}

@router.post("/login")
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

@router.post("/google")
def google_auth(req: GoogleAuthRequest):
    users = read_users()
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
            "created_at": datetime.datetime.now(timezone.utc).isoformat()
        }
        users.append(user)
        write_users(users)

    token = secrets.token_hex(24)
    SESSIONS[token] = user["id"]
    return {"token": token, "user": {"id": user["id"], "username": user["username"], "email": user.get("email")}}

@router.get("/me")
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

@router.post("/logout")
def logout(authorization: str | None = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        SESSIONS.pop(token, None)
    return {"ok": True}
"""

weather_code = """import httpx, asyncio
from datetime import datetime

async def _http_json_get(url, params=None, headers=None, label="weather", timeout=20.0):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0), follow_redirects=True) as client:
            r = await client.get(url, params=params, headers=headers or {})
            content_type = r.headers.get("content-type", "")
            if r.status_code >= 400: raise RuntimeError(f"{label} returned HTTP {r.status_code}")
            return r.json(), {"status": "ok", "http_status": r.status_code, "url": str(r.url).split("?")[0]}
    except Exception as exc:
        raise RuntimeError(f"{label} failed: {exc}")

async def _open_meteo_get(lat, lon, params, label):
    return await _http_json_get("https://api.open-meteo.com/v1/forecast", params=params, headers={"Accept": "application/json"}, label=f"Open-Meteo {label}")

async def _met_no_forecast(lat, lon):
    data, diagnostic = await _http_json_get(
        "https://api.met.no/weatherapi/locationforecast/2.0/compact",
        params={"lat": lat, "lon": lon},
        headers={"Accept": "application/json", "User-Agent": "MavunoGuard-AI/2.6"},
        label="MET Norway fallback"
    )
    timeseries = data.get("properties", {}).get("timeseries", [])
    by_day = {}
    for item in timeseries:
        t = item.get("time")
        if not t: continue
        day = str(t)[:10]
        pr = float(item.get("data", {}).get("next_1_hours", {}).get("details", {}).get("precipitation_amount") or 0)
        temp = item.get("data", {}).get("instant", {}).get("details", {}).get("air_temperature")
        rec = by_day.setdefault(day, {"rain": 0.0, "temps": []})
        rec["rain"] += max(0.0, pr)
        if temp is not None: rec["temps"].append(float(temp))
    
    dates = sorted(by_day)[:14]
    return {
        "latitude": lat, "longitude": lon, "elevation": None,
        "daily": {
            "time": dates,
            "temperature_2m_max": [max(by_day[d]["temps"]) if by_day[d]["temps"] else None for d in dates],
            "precipitation_sum": [round(by_day[d]["rain"], 2) for d in dates],
            "precipitation_probability_max": [None for _ in dates],
        },
        "_live": True, "_source": "MET Norway fallback forecast", "_diagnostic": diagnostic
    }

async def weather_data(lat, lon):
    core_params = {"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 14, "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max"}
    try:
        data, diagnostic = await _open_meteo_get(lat, lon, core_params, "forecast")
        daily = data.get("daily") or {}
        try:
            soil, soil_diag = await _open_meteo_get(lat, lon, {"latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 14, "hourly": "soil_moisture_0_to_1cm"}, "soil")
            ht = soil.get("hourly", {}).get("time", [])
            hm = soil.get("hourly", {}).get("soil_moisture_0_to_1cm", [])
            sbd = {}
            for t, v in zip(ht, hm):
                if v is not None: sbd.setdefault(str(t)[:10], []).append(float(v))
            daily["soil_moisture_0_to_1cm_mean"] = [round(sum(sbd[d])/len(sbd[d]),4) if sbd.get(d) else None for d in daily.get("time",[])]
        except:
            daily["soil_moisture_0_to_1cm_mean"] = [None]*len(daily.get("time",[]))
            soil_diag = {"status": "failed"}
        
        data["_live"] = True
        data["_source"] = "Open-Meteo forecast"
        data["_diagnostic"] = {**diagnostic, "soil_moisture": soil_diag}
        return data
    except Exception:
        return await _met_no_forecast(lat, lon)

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
    
    series = [{"date": dates[i], "rain": rain[i], "temp": temps[i], "prob": probs[i] if i < len(probs) else None, "soil_moisture": soil[i] if i < len(soil) else None} for i in range(n)]
    
    return {
        "live": bool(w.get("_live")), "source": w.get("_source"),
        "rain7_mm": round(rain7, 1), "max_temp_7d_c": round(max_temp, 1) if max_temp else None,
        "wet_days_7d": wet_days, "heavy_rain_days_7d": heavy_days,
        "drought_risk": round(drought), "flood_risk": round(flood), "series": series
    }
"""

satellite_code = """import httpx, os, math
from datetime import date, timedelta

async def satellite_metadata(lat, lon):
    url = "https://stac.dataspace.copernicus.eu/v1/search"
    today = date.today()
    start_date = today - timedelta(days=90)
    cloud = float(os.getenv("SATELLITE_MAX_CLOUD", "35"))
    body = {
        "collections": ["sentinel-2-l2a"],
        "intersects": {"type": "Point", "coordinates": [lon, lat]},
        "datetime": f"{start_date.isoformat()}/{today.isoformat()}",
        "query": {"eo:cloud_cover": {"lt": cloud}},
        "limit": 5,
    }
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(url, json=body, headers={"Accept": "application/geo+json"})
            if r.status_code == 400:
                b = [lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01]
                r = await client.get("https://stac.dataspace.copernicus.eu/v1/collections/sentinel-2-l2a/items", params={"bbox": ",".join(f"{x:.7f}" for x in b), "datetime": body["datetime"], "limit": "5"})
            r.raise_for_status()
            data = r.json()
        items = [{"id": f.get("id"), "datetime": (f.get("properties") or {}).get("datetime"), "cloud_cover": (f.get("properties") or {}).get("eo:cloud_cover")} for f in data.get("features", [])]
        return {"available": True, "scenes": items, "source": "CDSE STAC"}
    except Exception as exc:
        return {"available": False, "scenes": [], "source": "CDSE STAC", "reason": str(exc)}

async def sentinel_token():
    cid = os.getenv("SENTINEL_CLIENT_ID")
    secret = os.getenv("SENTINEL_CLIENT_SECRET")
    if not cid or not secret: return None
    url = os.getenv("SENTINEL_TOKEN_URL", "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(url, data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret})
        r.raise_for_status()
        return r.json().get("access_token")

def farm_bbox(lat, lon, farm_size, farm_unit):
    if farm_size and farm_size > 0:
        hectares = farm_size * (0.404686 if farm_unit.lower().startswith("acre") else 1.0)
        side_km = max(0.06, min(1.5, math.sqrt(hectares)))
        half_lat = (side_km/2)/111.0
        half_lon = half_lat / max(0.2, math.cos(math.radians(lat)))
    else:
        half_lat = 0.0025; half_lon = 0.0025
    return [lon-half_lon, lat-half_lat, lon+half_lon, lat+half_lat]

async def ndvi_series(lat, lon, planting_date, farm_size=None, farm_unit="Acres"):
    if not os.getenv("SENTINEL_CLIENT_ID"):
        return {"available": False, "status": "not_configured", "series": [], "reason": "No credentials"}
    try:
        token = await sentinel_token()
        d1 = date.today()
        try: d0 = date.fromisoformat(planting_date)
        except: d0 = d1 - timedelta(days=90)
        if d0 > d1 or (d1 - d0).days < 60: d0 = d1 - timedelta(days=90)
        
        evalscript = '''//VERSION=3
function setup() {
  return { input: [{ bands: ["B04", "B08", "SCL", "dataMask"] }], output: [ { id: "data", bands: 1, sampleType: "FLOAT32" }, { id: "dataMask", bands: 1 } ] };
}
function evaluatePixel(s) {
  let den = s.B08 + s.B04;
  let valid = den > 0 && s.SCL !== 6 && s.SCL !== 0;
  return { data: [valid ? (s.B08 - s.B04) / den : 0], dataMask: [s.dataMask * (valid ? 1 : 0)] };
}'''
        payload = {
            "input": {"bounds": {"bbox": farm_bbox(lat, lon, farm_size, farm_unit), "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}}, "data": [{"type": "sentinel-2-l2a", "dataFilter": {"mosaickingOrder": "leastCC"}}]},
            "aggregation": {"timeRange": {"from": d0.isoformat() + "T00:00:00Z", "to": d1.isoformat() + "T23:59:59Z"}, "aggregationInterval": {"of": "P14D"}, "evalscript": evalscript, "resx": 10, "resy": 10},
        }
        stats_url = os.getenv("SENTINEL_STATS_URL", "https://sh.dataspace.copernicus.eu/statistics/v1")
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(stats_url, json=payload, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            r.raise_for_status()
            data = r.json()
        
        out = []
        for row in data.get("data", []):
            mean = row.get("outputs", {}).get("data", {}).get("bands", {}).get("B0", {}).get("stats", {}).get("mean")
            if mean is not None:
                out.append({"date": str(row.get("interval", {}).get("from", ""))[:10], "ndvi": round(float(mean), 3)})
        
        return {"available": True, "status": "ok", "series": out} if out else {"available": False, "status": "no_observation", "series": []}
    except Exception as exc:
        return {"available": False, "status": "error", "series": [], "reason": str(exc)}
"""

ai_code = """import os, json, httpx

async def ai_explain(req, risk, ndvi):
    # Try Gemini first
    gemini_key = os.getenv('GEMINI_API_KEY')
    openai_key = os.getenv('OPENAI_API_KEY')
    
    system_instruction = "You are MavunoGuard AI, a Kenyan smallholder-farm decision assistant. Give practical, low-cost actions based only on the supplied evidence. Never say 'seek agronomic advice'. Never invent measurements. Clearly distinguish observed data from interpretation. Use short simple sentences understandable at a glance. Return JSON with keys headline, explanation, actions (array of strings), watch_next (array), confidence_note."
    user_content = json.dumps({"farm": req.model_dump(), "risk": risk, "satellite": ndvi})
    
    if gemini_key:
        model = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"parts": [{"text": user_content}]}],
            "generationConfig": {"responseMimeType": "application/json"}
        }
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                r = await client.post(url, json=payload, headers={'Content-Type': 'application/json'})
                r.raise_for_status()
                data = r.json()
            
            text = data.get('candidates', [])[0].get('content', {}).get('parts', [])[0].get('text', '')
            if text:
                text = text.strip().removeprefix('```json').removesuffix('```').strip()
                return json.loads(text)
        except Exception:
            pass

    # Fallback to OpenAI if Gemini fails or is not provided, but OpenAI is provided
    if openai_key:
        model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_content}
            ],
            "response_format": {"type": "json_object"}
        }
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                r = await client.post('https://api.openai.com/v1/chat/completions', json=payload, headers={'Authorization': f'Bearer {openai_key}', 'Content-Type': 'application/json'})
                r.raise_for_status()
                data = r.json()
            text = data['choices'][0]['message']['content']
            return json.loads(text)
        except Exception:
            pass
            
    return None
"""

analysis_code = """from PIL import Image, ExifTags
from pathlib import Path
import xml.etree.ElementTree as ET
from .weather import weather_risk

def rule_risk(req, weather, ndvi):
    wr = weather_risk(weather); score = 30
    reasons = []; actions = []
    
    if wr.get('drought_risk', 0) >= 60: score += 22; reasons.append("the coming 7-day rainfall outlook is relatively dry"); actions += ["Check soil moisture early in the morning for the next 3 days."]
    if wr.get('flood_risk', 0) >= 45: score += 20; reasons.append("the forecast contains several heavy-rain signals"); actions += ["Open blocked drainage paths before the next heavy rain."]
    if req.drainage and 'Poor' in req.drainage: score += 10; reasons.append("the farm is reported to have poor drainage"); actions.append("Clear the lowest drainage route and make a shallow outlet where water is collecting.")
    if req.flood_history and 'Frequently' in req.flood_history: score += 8; reasons.append("the farm has frequent flood history")
    if req.health and 'Severely' in req.health: score += 16; reasons.append("the crop is currently severely stressed")
    elif req.health and 'Poor' in req.health: score += 10; reasons.append("the crop is currently in poor condition")
    if 'Dry soil' in req.problems: score += 10; reasons.append("dry soil was reported")
    if 'Waterlogging' in req.problems: score += 10; reasons.append("waterlogging was reported")
    if 'Wilting' in req.problems: score += 6; actions.append("Inspect the wilted plants and check soil moisture 5–10 cm below the surface before irrigating.")
    
    if ndvi.get('available') and ndvi.get('series'):
        vals = [x['ndvi'] for x in ndvi['series'] if x.get('ndvi') is not None]
        if vals:
            latest = vals[-1]; score += max(0, min(20, int((0.65 - latest) * 40)))
            if latest < 0.35: reasons.append(f"recent Sentinel-2 NDVI is low ({latest:.2f})"); actions.append("Walk the low-vigor part of the field first; compare it with a healthy patch before changing inputs.")
            elif latest < 0.55: reasons.append(f"recent Sentinel-2 NDVI shows moderate vegetation vigor ({latest:.2f})")
            else: reasons.append(f"recent Sentinel-2 NDVI shows relatively strong vegetation vigor ({latest:.2f})")
    
    ndvi_trend = "not available"
    if ndvi.get("available") and len(ndvi.get("series", [])) >= 2:
        vals = [x.get("ndvi") for x in ndvi["series"] if x.get("ndvi") is not None]
        if len(vals) >= 2:
            delta = vals[-1] - vals[0]
            ndvi_trend = "rising" if delta > 0.04 else "falling" if delta < -0.04 else "stable"
            if ndvi_trend == "falling":
                score += 8; reasons.append("vegetation vigor is falling across the available Sentinel-2 observations")
    
    score = max(0, min(95, score))
    title = 'HIGH' if score >= 75 else 'MODERATE-HIGH' if score >= 60 else 'MODERATE' if score >= 45 else 'LOW'
    
    if not actions: actions = ["Walk the field along a simple zig-zag route and check 10–20 plants for changes.", "Record a photo from the same spot each week so changes can be compared over time."]
    
    return {"score": score, "title": title, "drought": wr.get('drought_risk', 0), "flood": wr.get('flood_risk', 0), "crop_stress": min(95, max(10, round(score * 0.85))), "reasons": reasons, "actions": actions[:6], "weather": wr, "ndvi_trend": ndvi_trend}

def parse_exif_gps(path):
    try:
        im = Image.open(path)
        exif = im.getexif(); gps_tag = None
        for k, v in ExifTags.TAGS.items():
            if v == "GPSInfo": gps_tag = k; break
        if not gps_tag or gps_tag not in exif: return None
        gps = exif[gps_tag]
        lat_ref = gps.get(1); lat = gps.get(2); lon_ref = gps.get(3); lon = gps.get(4)
        if not lat or not lon: return None
        def conv(vals): return sum(float(x[0])/float(x[1])/(60**i) for i, x in enumerate(vals))
        la = conv(lat); lo = conv(lon)
        if str(lat_ref).upper().startswith('S'): la = -la
        if str(lon_ref).upper().startswith('W'): lo = -lo
        return {"latitude": la, "longitude": lo}
    except Exception: return None

def parse_gpx(path):
    try:
        text = Path(path).read_text(errors='ignore'); root = ET.fromstring(text)
        pts = []
        for el in root.iter():
            if el.tag.lower().endswith('trkpt') or el.tag.lower().endswith('wpt'):
                try: pts.append({"latitude": float(el.attrib['lat']), "longitude": float(el.attrib['lon'])})
                except: pass
        return pts
    except: return []

def parse_kml(path):
    try:
        text = Path(path).read_text(errors='ignore'); root = ET.fromstring(text); pts = []
        for el in root.iter():
            if el.tag.lower().endswith('coordinates') and el.text:
                for item in re.split(r'\s+', el.text.strip()):
                    p = item.split(',')
                    if len(p) >= 2:
                        try: pts.append({"latitude": float(p[1]), "longitude": float(p[0])})
                        except: pass
        return pts
    except: return []
"""

app_code = """from fastapi import FastAPI, UploadFile, File, HTTPException, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from datetime import datetime, timezone
import uuid, os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
UPLOADS = ROOT / "uploads"
UPLOADS.mkdir(exist_ok=True)
load_dotenv(ROOT / ".env")

from .routers import auth
from .models import AnalyzeRequest
from .db import read_db, write_db
from .weather import weather_data
from .satellite import satellite_metadata, ndvi_series
from .ai import ai_explain
from .analysis import rule_risk, parse_gpx, parse_kml, parse_exif_gps

app = FastAPI(title="MavunoGuard AI API", version="3.0.0")
app.mount("/static", StaticFiles(directory=PUBLIC), name="static")
app.include_router(auth.router)

@app.get("/")
def index(): return FileResponse(PUBLIC / "index.html")

@app.get("/sw.js")
def service_worker(): return FileResponse(PUBLIC / "sw.js", media_type="application/javascript")

@app.get("/api/health")
def health(): return {"ok": True, "service": "MavunoGuard AI", "version": "3.0.0", "time": datetime.now(timezone.utc).isoformat()}

@app.get("/api/config")
def config():
    return {
        "weather": True, "satellite_metadata": True,
        "satellite_ndvi": bool(os.getenv("SENTINEL_CLIENT_ID") and os.getenv("SENTINEL_CLIENT_SECRET")),
        "ai": bool(os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")),
        "offline": True, "garmin_import": True
    }

@app.post("/api/garmin/import")
async def garmin_import(file: UploadFile = File(...)):
    suffix = Path(file.filename or '').suffix.lower(); target = UPLOADS / f"{uuid.uuid4().hex}{suffix}"
    target.write_bytes(await file.read())
    points = []; photo_gps = None
    if suffix == '.gpx': points = parse_gpx(target)
    elif suffix == '.kml': points = parse_kml(target)
    elif suffix in ['.jpg', '.jpeg', '.tif', '.tiff']: photo_gps = parse_exif_gps(target)
    else: raise HTTPException(400, "Use a GPX, KML or geo-tagged JPG/TIFF file.")
    if photo_gps: points = [photo_gps]
    if not points: raise HTTPException(422, "No GPS coordinates were found in this file.")
    lat = sum(p['latitude'] for p in points) / len(points); lon = sum(p['longitude'] for p in points) / len(points)
    return {"ok": True, "latitude": lat, "longitude": lon, "points": points[:500], "count": len(points), "source": "Garmin/GPX-KML/EXIF"}

@app.get("/api/weather")
async def weather_endpoint(latitude: float, longitude: float):
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180): raise HTTPException(400, "Invalid coordinates")
    try: return await weather_data(latitude, longitude)
    except Exception as exc: raise HTTPException(502, {"service": "Weather providers", "message": str(exc)})

@app.post('/api/analyze')
async def analyze(req: AnalyzeRequest):
    if not (-90 <= req.latitude <= 90 and -180 <= req.longitude <= 180): raise HTTPException(400, 'Invalid coordinates')
    try: weather = await weather_data(req.latitude, req.longitude)
    except Exception as exc: weather = {"_live": False, "_source": "Weather unavailable", "_error": str(exc), "daily": {}}
    
    ndvi = await ndvi_series(req.latitude, req.longitude, req.planting_date, req.farm_size, req.farm_unit)
    sat_meta = await satellite_metadata(req.latitude, req.longitude)
    risk = rule_risk(req, weather, ndvi)
    risk["monitoring_window"] = "farm-size-derived" if req.farm_size else "default local window"
    
    ai = await ai_explain(req, risk, ndvi) if req.use_ai else None
    if ai:
        risk['headline'] = ai.get('headline', risk['title'])
        risk['explanation'] = ai.get('explanation', '')
        risk['actions'] = ai.get('actions', risk['actions'])
        risk['watch_next'] = ai.get('watch_next', [])
    else:
        risk['headline'] = risk['title']
        risk['explanation'] = 'The score combines the weather outlook, your reported field conditions and—when configured—Sentinel-2 vegetation information.'
        risk['watch_next'] = ['Recheck after the next heavy rainfall or 3–5 dry days.', 'Compare the same field location over time.']
        
    result = {
        "id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "farm": req.model_dump(),
        "risk": risk,
        "satellite": {**sat_meta, "ndvi": ndvi},
        "live": {
            "weather": bool(weather.get("_live")),
            "sentinel_ndvi": bool(ndvi.get("available")),
            "metadata": bool(sat_meta.get("available")),
        },
        "sources": [weather.get("_source") if weather.get("_live") else "Weather unavailable"]
    }
    try:
        rows = read_db(); rows.append(result); write_db(rows[-200:])
    except Exception: pass
    return result

@app.get('/api/farms')
def farms(): return read_db()[-50:]
"""

(SERVER / "models.py").write_text(models_code)
(SERVER / "db.py").write_text(db_code)
(SERVER / "weather.py").write_text(weather_code)
(SERVER / "satellite.py").write_text(satellite_code)
(SERVER / "ai.py").write_text(ai_code)
(SERVER / "analysis.py").write_text(analysis_code)

(SERVER / "routers").mkdir(exist_ok=True)
(SERVER / "routers" / "__init__.py").touch()
(SERVER / "routers" / "auth.py").write_text(auth_code)

(SERVER / "app.py").write_text(app_code)

print("Server code successfully refactored.")
