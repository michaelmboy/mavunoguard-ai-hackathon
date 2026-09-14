from PIL import Image, ExifTags
from pathlib import Path
import xml.etree.ElementTree as ET
import re
from .weather import weather_risk

def rule_risk(req, weather, ndvi):
    wr = weather_risk(weather)
    score = 30
    reasons = []
    actions = []

    # --- Weather signals ---
    if wr.get('drought_risk', 0) >= 60:
        score += 22
        reasons.append("the coming 7-day rainfall outlook is relatively dry")
        actions.append("Check soil moisture early in the morning for the next 3 days.")

    # Flood/waterlogging: trigger on heavy single events OR sustained moderate rain
    flood_risk = wr.get('flood_risk', 0)
    consec_wet = wr.get('consec_wet_days', 0)
    past_rain  = wr.get('past_rain_7d_mm', 0)
    if flood_risk >= 45:
        score += 20
        if wr.get('heavy_rain_days_7d', 0) >= 1:
            reasons.append("the forecast contains heavy-rain events (≥25 mm in a day)")
        elif consec_wet >= 3:
            reasons.append(f"rain is forecast for {consec_wet} consecutive days on already-wet soils")
        else:
            reasons.append("persistent moderate rain is forecast with high probability")
        actions.append("Open blocked drainage paths before the next heavy rain.")
    elif flood_risk >= 30 and past_rain >= 30:
        score += 10
        reasons.append(f"soils are wet from {past_rain} mm of rain in the past 7 days — more rain ahead")
        actions.append("Check drainage channels and clear any blockages.")

    # Field-reported conditions
    if req.drainage and 'Poor' in req.drainage:
        score += 10
        reasons.append("the farm is reported to have poor drainage")
        actions.append("Clear the lowest drainage route and make a shallow outlet where water is collecting.")
    if req.flood_history and 'Frequently' in req.flood_history:
        score += 8
        reasons.append("the farm has frequent flood history")
    if req.health and 'Severely' in req.health:
        score += 16
        reasons.append("the crop is currently severely stressed")
    elif req.health and 'Poor' in req.health:
        score += 10
        reasons.append("the crop is currently in poor condition")
    if 'Dry soil' in req.problems:
        score += 10
        reasons.append("dry soil was reported")
    if 'Waterlogging' in req.problems:
        score += 10
        reasons.append("waterlogging was reported")
    if 'Wilting' in req.problems:
        score += 6
        actions.append("Inspect the wilted plants and check soil moisture 5–10 cm below the surface before irrigating.")

    # --- Satellite NDVI signals ---
    if ndvi.get('available') and ndvi.get('series'):
        vals = [x['ndvi'] for x in ndvi['series'] if x.get('ndvi') is not None]
        if vals:
            latest = vals[-1]
            score += max(0, min(20, int((0.65 - latest) * 40)))
            if latest < 0.35:
                reasons.append(f"recent Sentinel-2 NDVI is low ({latest:.2f}) — reduced vegetation cover")
                actions.append("Walk the low-vigor part of the field first; compare it with a healthy patch before changing inputs.")
            elif latest < 0.55:
                reasons.append(f"recent Sentinel-2 NDVI shows moderate vegetation vigor ({latest:.2f})")
            else:
                reasons.append(f"recent Sentinel-2 NDVI shows relatively strong vegetation vigor ({latest:.2f})")

    # NDVI trend
    ndvi_trend = "not available"
    if ndvi.get("available") and len(ndvi.get("series", [])) >= 2:
        vals = [x.get("ndvi") for x in ndvi["series"] if x.get("ndvi") is not None]
        if len(vals) >= 2:
            delta = vals[-1] - vals[0]
            ndvi_trend = "rising" if delta > 0.04 else "falling" if delta < -0.04 else "stable"
            if ndvi_trend == "falling":
                score += 8
                reasons.append("vegetation vigor is falling across the available Sentinel-2 observations")

    score = max(0, min(95, score))
    title = 'HIGH' if score >= 75 else 'MODERATE-HIGH' if score >= 60 else 'MODERATE' if score >= 45 else 'LOW'

    if not actions:
        actions = [
            "Walk the field along a simple zig-zag route and check 10–20 plants for changes.",
            "Record a photo from the same spot each week so changes can be compared over time.",
        ]

    return {
        "score": score,
        "title": title,
        "drought":    wr.get('drought_risk', 0),
        "flood":      wr.get('flood_risk', 0),
        "crop_stress":min(95, max(10, round(score * 0.85))),
        "reasons":    reasons,
        "actions":    actions[:6],
        "weather":    wr,
        "ndvi_trend": ndvi_trend,
    }

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
