"""Haze Watch hourly job.

Pulls fire hotspots (NASA FIRMS, keyless regional files), the wind/rain forecast and
CAMS PM2.5 forecast (Open-Meteo), and station readings (WAQI); tracks smoke from every
fire cluster along the 850 hPa wind; writes site/data/latest.json for the app; appends
history for the forecast check; sends the Telegram digest and early warnings.

Standard library only. Environment variables (GitHub secrets):
  WAQI_TOKEN          optional, adds observed station readings
  TELEGRAM_BOT_TOKEN  optional, enables the digest and warnings
  TELEGRAM_CHAT_ID    optional, your chat with the bot
  HAZE_FIXTURES       testing only: folder of saved responses instead of live calls
"""
import csv, io, json, math, os, random, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config.json")))
OUT_DIR = os.path.join(ROOT, "site", "data")
DATA_DIR = os.path.join(ROOT, "data")
H = 3600
FIX = os.environ.get("HAZE_FIXTURES")
WAQI = os.environ.get("WAQI_TOKEN", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TZ = timezone(timedelta(hours=CFG.get("timezone_offset_hours", 8)))

FIRMS_FILES = {
    "j1": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_SouthEast_Asia_48h.csv",
    "sn": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_SouthEast_Asia_48h.csv",
    "j2": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_SouthEast_Asia_48h.csv",
}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(url, name, binary=False, tries=3):
    """Fetch a URL (or read the saved fixture called `name`)."""
    if FIX:
        with open(os.path.join(FIX, name), "rb" if binary else "r") as f:
            return f.read()
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "haze-watch/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                b = r.read()
                return b if binary else b.decode("utf-8")
        except Exception as e:  # network hiccup: back off and retry
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"fetch failed for {name}: {last}")


# ---------------------------------------------------------------- geography
def region_of(lat, lon):
    if 99.5 <= lon <= 104.6 and 1.2 <= lat <= 6.8:
        return "Peninsular Malaysia"
    if 95 <= lon <= 106.2 and -6.2 <= lat <= 6:
        return "Sumatra"
    if 105 <= lon <= 115 and -9 <= lat <= -5.8:
        return "Java"
    if 108.5 <= lon <= 119.5 and -4.5 <= lat <= 7.5:
        if lat > 4.0 and lon > 115.3:
            return "Sabah"
        if lat > 0.9 and lon < 115.3 and lat > 1.0 + (lon - 109.6) * 0.25:
            return "Sarawak / Brunei"
        return "Kalimantan"
    if 118.5 <= lon <= 125.5 and -6 <= lat <= 2:
        return "Sulawesi"
    if lat > 5.5 and lon < 106:
        return "Southern Thailand"
    return "Other"


def b36(n, width=0):
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    s = ""
    n = int(n)
    while True:
        s = chars[n % 36] + s
        n //= 36
        if n == 0:
            break
    return s.rjust(width, "0")


# ---------------------------------------------------------------- inputs
def load_fires(now):
    R = CFG["region"]
    fires = []
    for sat, url in FIRMS_FILES.items():
        try:
            text = get(url, f"firms_{sat}.csv")
        except Exception as e:
            log("FIRMS", sat, "skipped:", e)
            continue
        for row in csv.DictReader(io.StringIO(text)):
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (KeyError, ValueError):
                continue
            if not (R["south"] <= lat <= R["north"] and R["west"] <= lon <= R["east"]):
                continue
            t = row["acq_time"].zfill(4)
            ts = datetime.strptime(row["acq_date"] + t, "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc).timestamp()
            fires.append({"lat": lat, "lon": lon, "ts": ts, "conf": row.get("confidence", ""),
                          "frp": float(row.get("frp") or 0), "sat": sat})
    if not fires:
        raise RuntimeError("no fire data at all; keeping the previous forecast")
    log("fires", len(fires))
    return fires


def load_grid():
    """1-degree grid of 850 hPa wind and rain, hourly, 2 days back to 4 days ahead."""
    R = CFG["region"]
    G = {"lat0": R["south"], "lon0": R["west"], "nlat": R["north"] - R["south"] + 1,
         "nlon": R["east"] - R["west"] + 1, "step": 1}
    pts = [(G["lat0"] + i, G["lon0"] + j) for i in range(G["nlat"]) for j in range(G["nlon"])]
    res = []
    for c, k in enumerate(range(0, len(pts), 160)):
        chunk = pts[k:k + 160]
        q = urllib.parse.urlencode({
            "latitude": ",".join(str(p[0]) for p in chunk), "longitude": ",".join(str(p[1]) for p in chunk),
            "hourly": "wind_speed_850hPa,wind_direction_850hPa,precipitation", "wind_speed_unit": "ms",
            "past_days": 2, "forecast_days": 4, "timezone": "GMT"})
        j = json.loads(get("https://api.open-meteo.com/v1/forecast?" + q, f"grid_{c}.json"))
        res.extend(j if isinstance(j, list) else [j])
    times = res[0]["hourly"]["time"]
    NT, NG = len(times), len(pts)
    U = [[0.0] * NG for _ in range(NT)]
    V = [[0.0] * NG for _ in range(NT)]
    P = [[0.0] * NG for _ in range(NT)]
    for g, w in enumerate(res):
        hs, hd, hp = w["hourly"]["wind_speed_850hPa"], w["hourly"]["wind_direction_850hPa"], w["hourly"]["precipitation"]
        for h in range(NT):
            sp, d = hs[h] or 0.0, math.radians(hd[h] or 0.0)
            U[h][g] = -sp * math.sin(d)
            V[h][g] = -sp * math.cos(d)
            P[h][g] = hp[h] or 0.0
    t0 = datetime.strptime(times[0], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp()
    return G, t0, NT, U, V, P, times


def load_cams(places, times):
    q = urllib.parse.urlencode({
        "latitude": ",".join(str(p["lat"]) for p in places), "longitude": ",".join(str(p["lon"]) for p in places),
        "hourly": "pm2_5", "past_days": 2, "forecast_days": 4, "timezone": "GMT", "domains": "cams_global"})
    j = json.loads(get("https://air-quality-api.open-meteo.com/v1/air-quality?" + q, "cams.json"))
    arr = j if isinstance(j, list) else [j]
    out = []
    for a in arr:  # align on the grid's time axis
        m = dict(zip(a["hourly"]["time"], a["hourly"]["pm2_5"]))
        out.append([m.get(t) for t in times])
    return out


# US AQI (PM2.5) back to concentration. WAQI publishes AQI, not micrograms.
# Breakpoints: US EPA pre-2024 table, which WAQI has historically used (ASSUMPTION-VERIFY).
US_BP = [(0, 50, 0.0, 12.0), (51, 100, 12.1, 35.4), (101, 150, 35.5, 55.4), (151, 200, 55.5, 150.4),
         (201, 300, 150.5, 250.4), (301, 500, 250.5, 500.4)]


def usaqi_to_pm(a):
    try:
        a = float(a)
    except (TypeError, ValueError):
        return None
    for ilo, ihi, clo, chi in US_BP:
        if a <= ihi:
            return round(clo + (max(a, ilo) - ilo) * (chi - clo) / (ihi - ilo), 1)
    return 500.0


def load_stations(places):
    if not WAQI:
        return [], [None] * len(places)
    R = CFG["region"]
    stations = []
    try:
        j = json.loads(get("https://api.waqi.info/map/bounds?" + urllib.parse.urlencode(
            {"latlng": f"{R['south']},{R['west']},{R['north']},{R['east']}", "networks": "all", "token": WAQI}), "waqi_bounds.json"))
        for s in j.get("data", []):
            pm = usaqi_to_pm(s.get("aqi"))
            if pm is not None:
                stations.append({"lat": round(s["lat"], 3), "lon": round(s["lon"], 3), "pm": pm,
                                 "name": (s.get("station") or {}).get("name", "")[:60]})
    except Exception as e:
        log("WAQI bounds skipped:", e)
    obs = []
    for i, p in enumerate(places):
        try:
            j = json.loads(get(f"https://api.waqi.info/feed/geo:{p['lat']};{p['lon']}/?token={WAQI}", f"waqi_{p['id']}.json"))
            d = j.get("data") or {}
            v = ((d.get("iaqi") or {}).get("pm25") or {}).get("v")
            geo = (d.get("city") or {}).get("geo") or [p["lat"], p["lon"]]
            km = math.hypot((geo[0] - p["lat"]) * 111, (geo[1] - p["lon"]) * 111 * math.cos(math.radians(p["lat"])))
            when = (d.get("time") or {}).get("iso")
            obs.append({"pm": usaqi_to_pm(v), "station": (d.get("city") or {}).get("name", "")[:60], "km": round(km, 1),
                        "time": when} if v is not None and km < 40 else None)
        except Exception as e:
            log("WAQI", p["id"], "skipped:", e)
            obs.append(None)
    log("stations", len(stations), "place readings", sum(o is not None for o in obs))
    return stations, obs


# ---------------------------------------------------------------- model
def run_model(fires, G, t0, NT, U, V, P, places, now):
    NG = G["nlat"] * G["nlon"]
    lat0, lon0, nlat, nlon = G["lat0"], G["lon0"], G["nlat"], G["nlon"]

    def samp(A, h, lat, lon):
        y, x = lat - lat0, lon - lon0
        i = max(0, min(nlat - 2, int(math.floor(y))))
        j = max(0, min(nlon - 2, int(math.floor(x))))
        fy, fx = min(1, max(0, y - i)), min(1, max(0, x - j))
        row = A[h]
        a00, a01 = row[i * nlon + j], row[i * nlon + j + 1]
        a10, a11 = row[(i + 1) * nlon + j], row[(i + 1) * nlon + j + 1]
        return (a00 * (1 - fx) + a01 * fx) * (1 - fy) + (a10 * (1 - fx) + a11 * fx) * fy

    now_idx = int((now - t0) // H)
    H0, H1 = now_idx - 24, now_idx + 72
    n_h = H1 - H0 + 1

    # sources: 0.25-degree cells x 6-hour bins
    src = {}
    for f in fires:
        hi = (f["ts"] - t0) / H
        if hi < 0:
            continue
        key = (math.floor(f["lat"] * 4), math.floor(f["lon"] * 4), math.floor(hi / 6))
        s = src.setdefault(key, {"lat": 0.0, "lon": 0.0, "n": 0, "frp": 0.0, "h": math.floor(hi / 6) * 6 + 3,
                                 "reg": region_of(f["lat"], f["lon"])})
        s["lat"] += f["lat"]; s["lon"] += f["lon"]; s["n"] += 1; s["frp"] += f["frp"]
    sources = [dict(s, lat=s["lat"] / s["n"], lon=s["lon"] / s["n"]) for s in src.values()]

    rng = random.Random(12345)
    lam, R2 = math.log(2) / 36, 0.45 ** 2
    score = [[0.0] * n_h for _ in places]
    src_reg = [{} for _ in places]
    frames = [[0.0] * NG for _ in range(25)]
    coslat = [math.cos(math.radians(p["lat"])) for p in places]
    for s in sources:
        for r in range(4):
            hs = round(s["h"] + r * 3)
            if hs >= NT - 1:
                continue
            lat = s["lat"] + rng.gauss(0, 0.05)
            lon = s["lon"] + rng.gauss(0, 0.05)
            w = s["frp"] / 4
            for h in range(hs, min(H1, NT - 2) + 1):
                if h >= H0:
                    k = h - H0
                    for pi, p in enumerate(places):
                        dy, dx = lat - p["lat"], (lon - p["lon"]) * coslat[pi]
                        if dy * dy + dx * dx < R2:
                            score[pi][k] += w
                            if now_idx < h <= now_idx + 24:
                                src_reg[pi][s["reg"]] = src_reg[pi].get(s["reg"], 0) + w
                    if h >= now_idx and (h - now_idx) % 3 == 0:
                        fi = (h - now_idx) // 3
                        i, j = round(lat - lat0), round(lon - lon0)
                        if 0 <= i < nlat and 0 <= j < nlon:
                            frames[fi][i * nlon + j] += w
                u, v, pr = samp(U, h, lat, lon), samp(V, h, lat, lon), samp(P, h, lat, lon)
                lat += v * 3.6 / 111 + rng.gauss(0, 0.04)
                lon += u * 3.6 / (111 * math.cos(math.radians(lat))) + rng.gauss(0, 0.04)
                w *= math.exp(-lam - 0.15 * pr)
                if (lat < lat0 - 0.5 or lat > lat0 + nlat - 0.5 or lon < lon0 - 0.5 or lon > lon0 + nlon - 0.5
                        or w < 0.01):
                    break

    def q(w):
        return 0 if w < 10 else min(9, 1 + int(math.floor(math.log(w / 10) / math.log(2.2))))
    smoke = ["".join(str(q(w)) for w in f) for f in frames]

    def enc(x):
        return chr(77 + max(-12, min(12, round(x))))
    wind = []
    for k in range(25):
        h = min(now_idx + 3 * k, NT - 1)
        wind.append("".join(enc(U[h][i * nlon + j]) + enc(V[h][i * nlon + j])
                            for i in range(0, nlat, 2) for j in range(0, nlon, 2)))

    # hotspot cells (0.25 degree), last 48 h
    cells = {}
    for f in fires:
        age = (now - f["ts"]) / H
        if age > 48:
            continue
        a = 0 if age <= 24 else 1
        key = (a, math.floor(f["lat"] * 4), math.floor(f["lon"] * 4))
        c = cells.setdefault(key, {"frp": 0.0, "hi": 0})
        c["frp"] += f["frp"]
        if f["conf"] in ("high", "h"):
            c["hi"] = 1
    hot = " ".join(str(a * 2 + c["hi"]) + b36(i + 40, 2) + b36(j - 360, 2) + b36(max(1, round(c["frp"])))
                   for (a, i, j), c in cells.items())

    # tracks from the 16 strongest 0.5-degree clusters in the last 24 h
    cl = {}
    for f in fires:
        if now - f["ts"] > 24 * H or f["frp"] <= 0:
            continue
        c = cl.setdefault((math.floor(f["lat"] * 2), math.floor(f["lon"] * 2)), {"lat": 0, "lon": 0, "n": 0, "frp": 0})
        c["lat"] += f["lat"] * f["frp"]; c["lon"] += f["lon"] * f["frp"]; c["n"] += 1; c["frp"] += f["frp"]
    top = sorted(({"lat": c["lat"] / c["frp"], "lon": c["lon"] / c["frp"], "n": c["n"], "frp": c["frp"]}
                  for c in cl.values()), key=lambda c: -c["frp"])[:16]
    tracks = []
    for c in top:
        lat, lon = c["lat"], c["lon"]
        pts = [[round(lat, 2), round(lon, 2)]]
        for h in range(now_idx, min(now_idx + 72, NT - 1)):
            u, v = samp(U, h, lat, lon), samp(V, h, lat, lon)
            lat += v * 3.6 / 111
            lon += u * 3.6 / (111 * math.cos(math.radians(lat)))
            if (h + 1 - now_idx) % 3 == 0:
                pts.append([round(lat, 2), round(lon, 2)])
            if lat < -8 or lat > 10 or lon < 93 or lon > 122:
                break
        tracks.append({"lat": round(c["lat"], 2), "lon": round(c["lon"], 2), "reg": region_of(c["lat"], c["lon"]),
                       "n": c["n"], "frp": round(c["frp"]), "pts": pts})

    return {"now_idx": now_idx, "H0": H0, "H1": H1, "score": score, "src_reg": src_reg,
            "smoke": smoke, "wind": wind, "hot": hot, "tracks": tracks, "samp": samp}


# ---------------------------------------------------------------- outputs
def my_api(pm):
    """PM2.5 to approximate Malaysian API (breakpoints ASSUMPTION-VERIFY, same as the app)."""
    if pm is None:
        return None
    if pm <= 15: return pm / 15 * 50
    if pm <= 35: return 50 + (pm - 15) / 20 * 50
    if pm <= 150: return 100 + (pm - 35) / 115 * 100
    if pm <= 250: return 200 + (pm - 150)
    return 300 + (pm - 250) / 250 * 200


BAND_NAMES = ["good", "moderate", "unhealthy", "very unhealthy", "hazardous"]


def band(a):
    for i, m in enumerate([50, 100, 200, 300]):
        if a <= m:
            return i
    return 4


def accuracy(hist_rows, place_ids):
    """24-hour-ahead error: CAMS forecast vs 'same as yesterday', against station readings."""
    by = {}
    for r in hist_rows:
        by[(r["hour"], r["place"])] = r
    out = {}
    for pid in place_ids:
        e_cams, e_pers = [], []
        for (hour, p), r in by.items():
            if p != pid or r["obs_pm"] in ("", None):
                continue
            prev = (datetime.fromisoformat(hour) - timedelta(hours=24)).isoformat()
            pr = by.get((prev, pid))
            if not pr:
                continue
            obs = float(r["obs_pm"])
            if pr.get("cams_plus24") not in ("", None):
                e_cams.append(abs(float(pr["cams_plus24"]) - obs))
            if pr.get("obs_pm") not in ("", None):
                e_pers.append(abs(float(pr["obs_pm"]) - obs))
        if e_cams:
            out[pid] = {"n": len(e_cams), "cams": round(sum(e_cams) / len(e_cams), 1),
                        "persist": round(sum(e_pers) / len(e_pers), 1) if e_pers else None}
    return out


def telegram(text):
    if not (TG_TOKEN and TG_CHAT):
        log("telegram not configured; message was:\n" + text)
        return False
    if FIX:
        log("telegram (fixture mode, not sent):\n" + text)
        return True
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data, timeout=30)
        return True
    except Exception as e:
        log("telegram failed:", e)
        return False


def fmt_time(ts):
    d = datetime.fromtimestamp(ts, TZ)
    return d.strftime("%-I%p").lower() if d.minute == 0 else d.strftime("%-I:%M%p").lower()


def main():
    now = time.time() if not FIX else json.load(open(os.path.join(FIX, "now.json")))["now"]
    places = CFG["places"]
    fires = load_fires(now)
    G, t0, NT, U, V, P, times = load_grid()
    cams = load_cams(places, times)
    stations, obs = load_stations(places)
    M = run_model(fires, G, t0, NT, U, V, P, places, now)
    ni, H0, H1, samp = M["now_idx"], M["H0"], M["H1"], M["samp"]
    now_hour = t0 + ni * H

    out_places = []
    for i, p in enumerate(places):
        pm = [None if cams[i][h] is None else round(cams[i][h]) for h in range(H0, H1 + 1)]
        sc = [round(x) for x in M["score"][i]]
        rn = [round(samp(P, min(h, NT - 1), p["lat"], p["lon"]), 1) for h in range(H0, H1 + 1)]
        wd = []
        for h in range(ni, H1 + 1, 3):
            hh = min(h, NT - 1)
            u, v = samp(U, hh, p["lat"], p["lon"]), samp(V, hh, p["lat"], p["lon"])
            wd.append([round((math.degrees(math.atan2(-u, -v)) + 360) % 360), round(math.hypot(u, v), 1)])
        sr = M["src_reg"][i]
        tot = sum(sr.values()) or 1
        srcs = [[k, round(100 * v / tot)] for k, v in sorted(sr.items(), key=lambda e: -e[1]) if v / tot > 0.1]
        out_places.append({"id": p["id"], "name": p["name"], "lat": p["lat"], "lon": p["lon"],
                           "pm": pm, "sc": sc, "rn": rn, "wd": wd, "srcs": srcs, "obs": obs[i]})

    regions = {}
    for f in fires:
        if now - f["ts"] < 24 * H:
            r = regions.setdefault(region_of(f["lat"], f["lon"]), {"n": 0, "frp": 0, "hi": 0})
            r["n"] += 1; r["frp"] += f["frp"]; r["hi"] += f["conf"] in ("high", "h")
    for r in regions.values():
        r["frp"] = round(r["frp"])

    # history + forecast check
    os.makedirs(DATA_DIR, exist_ok=True)
    hist_path = os.path.join(DATA_DIR, "history.csv")
    fields = ["hour", "place", "obs_pm", "obs_station", "cams_now", "cams_plus24", "score_now", "score_plus24"]
    rows = []
    if os.path.exists(hist_path):
        rows = list(csv.DictReader(open(hist_path)))
    hour_iso = datetime.fromtimestamp(now_hour, timezone.utc).replace(tzinfo=None).isoformat()
    rows = [r for r in rows if r["hour"] != hour_iso]
    for p in out_places:
        o = p["obs"] or {}
        rows.append({"hour": hour_iso, "place": p["id"], "obs_pm": o.get("pm", ""), "obs_station": o.get("station", ""),
                     "cams_now": p["pm"][24], "cams_plus24": p["pm"][48], "score_now": p["sc"][24], "score_plus24": p["sc"][48]})
    cutoff = (datetime.fromtimestamp(now_hour, timezone.utc).replace(tzinfo=None) - timedelta(days=90)).isoformat()
    rows = [r for r in rows if r["hour"] >= cutoff]
    with open(hist_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    acc = accuracy(rows[-24 * 15 * len(places):], [p["id"] for p in places])

    # station readings for the last 24 hours, aligned with the forecast series (index 0 = 24 h ago, 24 = now)
    start = datetime.fromtimestamp(now_hour, timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    for p in out_places:
        series = [None] * 25
        for r in rows:
            if r["place"] != p["id"] or r["obs_pm"] in ("", None):
                continue
            k = int(round((datetime.fromisoformat(r["hour"]) - start).total_seconds() / 3600))
            if 0 <= k <= 24:
                series[k] = round(float(r["obs_pm"]), 1)
        p["obsHist"] = series

    latest = {
        "meta": {"generated": datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z"),
                 "nowISO": datetime.fromtimestamp(now_hour, timezone.utc).isoformat().replace("+00:00", "Z"),
                 "H0off": -24, "mode": "live",
                 "hotspots24": sum(1 for f in fires if now - f["ts"] < 24 * H),
                 "hotspots48": sum(1 for f in fires if now - f["ts"] < 48 * H),
                 "latestFire": datetime.fromtimestamp(max(f["ts"] for f in fires), timezone.utc).isoformat().replace("+00:00", "Z"),
                 "grid": G, "stationsUsed": bool(WAQI)},
        "regions": regions, "places": out_places, "tracks": M["tracks"], "hot": M["hot"],
        "smoke": M["smoke"], "wind": M["wind"], "stations": stations, "accuracy": acc,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "latest.json"), "w") as f:
        json.dump(latest, f, separators=(",", ":"))
    log("wrote latest.json")

    notify(latest, now)


def notify(latest, now):
    state_path = os.path.join(DATA_DIR, "state.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    local = datetime.fromtimestamp(now, TZ)
    A = CFG["alerts"]
    url = CFG.get("app_url") or ""
    now_hour = datetime.fromisoformat(latest["meta"]["nowISO"].replace("Z", "+00:00")).timestamp()

    def summary(p):
        nowa = my_api(p["pm"][24]) or 0
        nxt = [(k, p["pm"][k]) for k in range(25, 49) if p["pm"][k] is not None]
        pk, pv = max(nxt, key=lambda e: e[1]) if nxt else (24, p["pm"][24])
        pa = my_api(pv) or 0
        line = f"{p['name']}: API ~{round(nowa)} ({BAND_NAMES[band(nowa)]}), peak ~{round(pa)} around {fmt_time(now_hour + (pk - 24) * H)}"
        if p["srcs"] and max(p["sc"][25:49]) >= 100:
            line += f"; smoke from {' and '.join(s[0] for s in p['srcs'])}"
        if p.get("obs") and p["obs"].get("pm") is not None:
            line += f"; station now ~{round(my_api(p['obs']['pm']))}"
        return line, nowa, pa, pk

    today = local.strftime("%Y-%m-%d")
    # morning digest
    if local.hour == A["digest_hour_local"] and state.get("digest") != today:
        lines = [f"Haze Watch · {local.strftime('%a %-d %b')}"] + [summary(p)[0] for p in latest["places"]]
        if url:
            lines.append(url)
        if telegram("\n".join(lines)):
            state["digest"] = today

    # early warnings: a place forecast to cross the warning level within 24 h
    qs, qe = A.get("quiet_hours_local", [22, 6])
    quiet = local.hour >= qs or local.hour < qe
    alerted = state.setdefault("alerted", {})
    for p in latest["places"]:
        line, nowa, pa, pk = summary(p)
        last = alerted.get(p["id"], 0)
        if pa >= A["warn_api"] and nowa < A["warn_api"] and now - last > 12 * H and not quiet:
            if telegram(f"Haze heads-up · {line}" + (f"\n{url}" if url else "")):
                alerted[p["id"]] = now
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(state_path, "w") as f:
        json.dump(state, f)


if __name__ == "__main__":
    main()
