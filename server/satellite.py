import httpx, os, math
from datetime import date, timedelta

async def satellite_metadata(lat, lon):
    url = "https://stac.dataspace.copernicus.eu/v1/search"
    today = date.today()
    start_date = today - timedelta(days=90)
    cloud = float(os.getenv("SATELLITE_MAX_CLOUD", "35"))
    # CDSE STAC requires full RFC3339 datetime strings, not bare dates
    dt_range = f"{start_date.isoformat()}T00:00:00Z/{today.isoformat()}T23:59:59Z"
    body = {
        "collections": ["sentinel-2-l2a"],
        "intersects": {"type": "Point", "coordinates": [lon, lat]},
        "datetime": dt_range,
        # Use OGC CQL2 filter instead of legacy "query" extension
        "filter": {"op": "lt", "args": [{"property": "eo:cloud_cover"}, cloud]},
        "filter-lang": "cql2-json",
        "limit": 5,
    }
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(url, json=body, headers={"Accept": "application/geo+json", "Content-Type": "application/json"})
            if r.status_code in (400, 422):
                # Fallback: GET items endpoint — also needs RFC3339
                b = [lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01]
                r = await client.get(
                    "https://stac.dataspace.copernicus.eu/v1/collections/sentinel-2-l2a/items",
                    params={"bbox": ",".join(f"{x:.7f}" for x in b), "datetime": dt_range, "limit": "5"},
                )
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
