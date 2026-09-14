"""
Weather data fetching and risk scoring for MavunoGuard.

Key design decisions:
- We fetch past_days=7 alongside the 14-day forecast so the risk engine
  can see antecedent rainfall (soil already saturated = higher flood risk).
- precipitation_hours is fetched — hours of rain per day distinguishes
  brief downpours from all-day drizzle that saturates soil.
- The risk thresholds are calibrated for equatorial East Africa (Kenya/
  Tanzania/Uganda), where sustained 5-15 mm/day for multiple consecutive
  days IS a waterlogging/flood signal, not just single 25mm+ events.
  The old formula was tuned for temperate climates and consistently
  under-scored flood risk and over-scored drought in the region.
"""
import httpx
from datetime import datetime, date, timedelta


async def _http_json_get(url, params=None, headers=None, label="weather", timeout=20.0):
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=8.0), follow_redirects=True
        ) as client:
            r = await client.get(url, params=params, headers=headers or {})
            if r.status_code >= 400:
                raise RuntimeError(f"{label} returned HTTP {r.status_code}")
            return r.json(), {"status": "ok", "http_status": r.status_code, "url": str(r.url).split("?")[0]}
    except Exception as exc:
        raise RuntimeError(f"{label} failed: {exc}")


async def _open_meteo_get(lat, lon, params, label):
    return await _http_json_get(
        "https://api.open-meteo.com/v1/forecast",
        params=params,
        headers={"Accept": "application/json"},
        label=f"Open-Meteo {label}",
    )


async def _met_no_forecast(lat, lon):
    data, diagnostic = await _http_json_get(
        "https://api.met.no/weatherapi/locationforecast/2.0/compact",
        params={"lat": lat, "lon": lon},
        headers={"Accept": "application/json", "User-Agent": "MavunoGuard-AI/3.0"},
        label="MET Norway fallback",
    )
    timeseries = data.get("properties", {}).get("timeseries", [])
    by_day: dict = {}
    for item in timeseries:
        t = item.get("time")
        if not t:
            continue
        day = str(t)[:10]
        pr = float(
            (item.get("data") or {}).get("next_1_hours", {}).get("details", {}).get("precipitation_amount") or 0
        )
        temp = ((item.get("data") or {}).get("instant", {}).get("details") or {}).get("air_temperature")
        rec = by_day.setdefault(day, {"rain": 0.0, "temps": [], "wet_hours": 0})
        rec["rain"] += max(0.0, pr)
        if pr > 0.1:
            rec["wet_hours"] += 1
        if temp is not None:
            rec["temps"].append(float(temp))

    # Include today and 13 future days; skip past days (MET Norway only gives ~10 days ahead)
    today_str = date.today().isoformat()
    dates = sorted(d for d in by_day if d >= today_str)[:14]
    return {
        "latitude": lat,
        "longitude": lon,
        "elevation": None,
        "daily": {
            "time": dates,
            "temperature_2m_max": [
                max(by_day[d]["temps"]) if by_day[d]["temps"] else None for d in dates
            ],
            "precipitation_sum": [round(by_day[d]["rain"], 2) for d in dates],
            "precipitation_probability_max": [None for _ in dates],
            "precipitation_hours": [by_day[d]["wet_hours"] for d in dates],
            "past_precipitation_sum": [],
        },
        "_live": True,
        "_source": "MET Norway fallback forecast",
        "_diagnostic": diagnostic,
    }


async def weather_data(lat, lon):
    """
    Fetch 7 days of past observations + 14-day forecast from Open-Meteo.
    Falls back to MET Norway (forecast only) if Open-Meteo is unreachable.
    """
    core_params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": "auto",
        "past_days": 7,          # antecedent rainfall for soil saturation
        "forecast_days": 14,
        "daily": (
            "temperature_2m_max,"
            "temperature_2m_min,"
            "precipitation_sum,"
            "precipitation_probability_max,"
            "precipitation_hours"   # hours of rain per day
        ),
    }
    try:
        data, diagnostic = await _open_meteo_get(lat, lon, core_params, "forecast")
        daily = data.get("daily") or {}

        # Separate past vs forecast days by today's date
        today_str = date.today().isoformat()
        all_dates = daily.get("time", [])
        rain_all = daily.get("precipitation_sum", [])
        past_rain = [
            float(rain_all[i] or 0)
            for i, d in enumerate(all_dates)
            if d < today_str and i < len(rain_all)
        ]

        # Attach the past-7d sum so the risk engine can use it
        daily["past_7d_precipitation_mm"] = round(sum(past_rain), 1)
        daily["past_7d_wet_days"] = sum(1 for x in past_rain if x >= 5)

        # Soil moisture (best-effort hourly → daily mean)
        try:
            soil, soil_diag = await _open_meteo_get(
                lat, lon,
                {
                    "latitude": lat, "longitude": lon,
                    "timezone": "auto", "past_days": 7, "forecast_days": 14,
                    "hourly": "soil_moisture_0_to_1cm",
                },
                "soil",
            )
            ht = soil.get("hourly", {}).get("time", [])
            hm = soil.get("hourly", {}).get("soil_moisture_0_to_1cm", [])
            sbd: dict = {}
            for t, v in zip(ht, hm):
                if v is not None:
                    sbd.setdefault(str(t)[:10], []).append(float(v))
            daily["soil_moisture_0_to_1cm_mean"] = [
                round(sum(sbd[d]) / len(sbd[d]), 4) if sbd.get(d) else None
                for d in all_dates
            ]
        except Exception:
            daily["soil_moisture_0_to_1cm_mean"] = [None] * len(all_dates)
            soil_diag = {"status": "failed"}

        data["_live"] = True
        data["_source"] = "Open-Meteo forecast"
        data["_diagnostic"] = {**diagnostic, "soil_moisture": soil_diag}
        return data

    except Exception:
        return await _met_no_forecast(lat, lon)


def weather_risk(w):
    """
    Compute drought and flood risk scores (0-95) from weather data.

    Calibrated for equatorial East Africa:
    - Flood risk accounts for SUSTAINED moderate rain (5-15mm/day for
      several consecutive days) which saturates clay-heavy soils in the
      region — not just single extreme events.
    - Antecedent rainfall from the past 7 days raises flood risk when
      soils are already wet.
    - Drought risk is only elevated when the 7-day forecast is genuinely
      dry AND past soil is dry (not just 'below temperate baseline').
    - High probability of daily rain (>70%) counts even on lower-mm days
      because convective showers in the region often exceed model totals.
    """
    d = w.get("daily", {})
    rain_all = d.get("precipitation_sum", []) or []
    temps     = d.get("temperature_2m_max", []) or []
    probs     = d.get("precipitation_probability_max", []) or []
    dates     = d.get("time", []) or []
    soil      = d.get("soil_moisture_0_to_1cm_mean", []) or []
    wet_hours = d.get("precipitation_hours", []) or []

    # Split past vs forecast at today
    today_str = date.today().isoformat()
    future_idx = next((i for i, t in enumerate(dates) if t >= today_str), 0)

    # Past 7 days
    past_rain_7d   = d.get("past_7d_precipitation_mm", sum(float(rain_all[i] or 0) for i in range(future_idx)))
    past_wet_days  = d.get("past_7d_wet_days", sum(1 for i in range(future_idx) if float(rain_all[i] or 0) >= 5))

    # Forecast arrays (next 7 and 14 days)
    frain  = [float(rain_all[future_idx + i] or 0) for i in range(min(14, len(rain_all) - future_idx))]
    fprobs = [float(probs[future_idx + i] or 0)    for i in range(min(14, len(probs)    - future_idx))]
    ftemps = [float(temps[future_idx + i])          for i in range(min(7,  len(temps)    - future_idx)) if temps[future_idx + i] is not None]
    fhours = [float(wet_hours[future_idx + i] or 0) for i in range(min(7,  len(wet_hours)- future_idx))]
    fsoil  = [float(soil[future_idx + i])           for i in range(min(7,  len(soil)     - future_idx)) if soil[future_idx + i] is not None]

    rain7  = sum(frain[:7])
    rain14 = sum(frain[:14])
    max_temp = max(ftemps) if ftemps else None
    wet_days_7   = sum(1 for x in frain[:7] if x >= 5)
    heavy_days_7 = sum(1 for x in frain[:7] if x >= 25)
    # Probability-weighted rain days: count days with >=70% probability as "likely wet"
    prob_wet_7   = sum(1 for p in fprobs[:7] if p >= 70)
    # Consecutive wet days from today (persistent rain signal)
    consec_wet = 0
    for x in frain[:7]:
        if x >= 3:
            consec_wet += 1
        else:
            break
    # Average wet hours (sustained vs brief)
    avg_wet_hours = sum(fhours) / len(fhours) if fhours else 0

    # ---------------------------------------------------------------
    # DROUGHT RISK
    # Elevated only when forecast is genuinely dry AND soil is depleted.
    # Base: 55 for the region (rainy season baseline is higher than temperate).
    # Reduce for each mm of forecast rain; increase for heat.
    # Soil moisture below 0.10 m3/m3 is a real dryness signal.
    # ---------------------------------------------------------------
    soil_dry_bonus = 0
    if fsoil:
        avg_soil = sum(fsoil) / len(fsoil)
        if avg_soil < 0.10:
            soil_dry_bonus = 20
        elif avg_soil < 0.15:
            soil_dry_bonus = 10
    # If it was raining a lot in the past 7 days, soils are wet — discount drought
    past_wet_discount = min(15, past_rain_7d * 0.3)
    heat_bonus = max(0, (max_temp - 30) * 5) if max_temp else 0
    drought = min(95, max(5,
        55 - rain7 * 1.8 - past_rain_7d * 0.5
        + soil_dry_bonus + heat_bonus - past_wet_discount
    ))

    # ---------------------------------------------------------------
    # FLOOD / WATERLOGGING RISK
    # East Africa signal: 5+ mm/day for 3+ consecutive days on already-
    # saturated soils, OR any single day >= 25mm, OR high persistent
    # probability of rain on already-wet soils.
    # ---------------------------------------------------------------
    flood = max(5,
        heavy_days_7 * 25                           # extreme single events
        + max(0, consec_wet - 2) * 12               # persistent consecutive rain
        + max(0, wet_days_7 - 2) * 8                # many wet days
        + max(0, prob_wet_7 - 3) * 6                # high-probability wet days
        + (10 if past_wet_days >= 4 else 0)         # already-wet antecedent soils
        + (8 if past_rain_7d >= 40 else 0)          # very wet past week
        + (5 if avg_wet_hours >= 6 else 0)          # sustained all-day rain
    )
    flood = min(95, flood)

    # Build series for the frontend charts (forecast days only)
    n = min(len(dates) - future_idx, min(len(rain_all) - future_idx, len(temps) - future_idx))
    series = [
        {
            "date":         dates[future_idx + i],
            "rain":         rain_all[future_idx + i] if (future_idx + i) < len(rain_all) else None,
            "temp":         temps   [future_idx + i] if (future_idx + i) < len(temps)    else None,
            "prob":         probs   [future_idx + i] if (future_idx + i) < len(probs)    else None,
            "soil_moisture":soil    [future_idx + i] if (future_idx + i) < len(soil)     else None,
        }
        for i in range(n)
    ]

    return {
        "live":              bool(w.get("_live")),
        "source":            w.get("_source"),
        "rain7_mm":          round(rain7, 1),
        "rain14_mm":         round(rain14, 1),
        "past_rain_7d_mm":   round(past_rain_7d, 1),
        "max_temp_7d_c":     round(max_temp, 1) if max_temp else None,
        "wet_days_7d":       wet_days_7,
        "heavy_rain_days_7d":heavy_days_7,
        "consec_wet_days":   consec_wet,
        "prob_wet_days_7d":  prob_wet_7,
        "drought_risk":      round(drought),
        "flood_risk":        round(flood),
        "series":            series,
    }
