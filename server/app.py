from fastapi import FastAPI, UploadFile, File, HTTPException, Header
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
from .models import AnalyzeRequest, ClinicRequest
from .db import read_db, write_db
from .weather import weather_data
from .satellite import satellite_metadata, ndvi_series
from .ai import ai_explain
from .analysis import rule_risk, parse_gpx, parse_kml, parse_exif_gps
from .clinic import clinic_diagnose

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

@app.post('/api/clinic')
async def clinic(req: ClinicRequest, authorization: str | None = Header(None)):
    """
    MavunoGuard AI Clinic — image-based diagnosis endpoint.

    Accepts a base64-encoded image and context metadata, sends it to
    Gemini Vision, and returns a structured diagnostic report covering
    crops, livestock, produce, and soil.
    """
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(503, "AI Clinic requires a Gemini API key. Set GEMINI_API_KEY in your environment.")
    
    result = await clinic_diagnose(
        image_b64=req.image_b64,
        mime_type=req.mime_type,
        category=req.category,
        description=req.description,
        location=req.location,
        crop_or_animal=req.crop_or_animal,
    )

    # Surface a 422 only on hard model errors, not on low-confidence results
    if result.get("error") and "API error" in result.get("confidence_note", ""):
        raise HTTPException(502, detail={"message": result["summary"], "reason": result.get("confidence_note")})

    return {
        "id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "category": req.category,
        "subject": req.crop_or_animal,
        "diagnosis": result,
    }

@app.get("/api/diagnostics")
async def diagnostics(latitude: float = 0.0, longitude: float = 0.0, planting_date: str | None = None):
    out = {"ok": True, "weather": {"live": False}, "sentinel": {"configured": bool(os.getenv("SENTINEL_CLIENT_ID") and os.getenv("SENTINEL_CLIENT_SECRET"))}}
    try:
        w = await weather_data(latitude, longitude)
        out["weather"] = {"live": True, "source": w.get("_source"), "days": len((w.get("daily") or {}).get("time", [])), "status": "ok", "diagnostic": w.get("_diagnostic")}
    except Exception as exc:
        out["weather"] = {"live": False, "status": "upstream_unavailable", "error": str(exc), "service": "Weather providers"}

    if not out["sentinel"]["configured"]:
        out["sentinel"].update({"status": "not_configured"})
        return out
    
    try:
        from .satellite import sentinel_token
        token = await sentinel_token()
        out["sentinel"].update({"token": bool(token), "status": "authenticated"})
        if planting_date:
            ndvi = await ndvi_series(latitude, longitude, planting_date)
            out["sentinel"]["statistics"] = {"status": ndvi.get("status"), "available": ndvi.get("available")}
    except Exception as exc:
        out["sentinel"].update({"token": False, "status": "authentication_failed", "error": str(exc)})
    return out
