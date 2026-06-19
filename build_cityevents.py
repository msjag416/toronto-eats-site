#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CityEvents daily feed builder.

Pulls Toronto events from the City of Toronto Open Data "Festivals and Events"
dataset (a CKAN data portal) and writes events.json in the exact shape that
cityevents.html reads. No API key is required.

This runs once a day on GitHub Actions. It is written defensively: if a field
is missing it is skipped, and if the feed cannot be read at all the existing
events.json is left untouched so the page never goes blank.

On the first run it prints the field names it sees to the log, so the mapping
below can be fine-tuned to the live data.
"""

import json
import math
import sys
import urllib.request
import urllib.parse
from datetime import datetime, date

CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca"
PACKAGE = "festivals-events"
OUT = "events.json"
MAX_RECORDS = 1500
MAX_OUTPUT = 150          # cap the feed so the page stays light
DESC_LIMIT = 240          # trim long descriptions

# Neighbourhood centroids (lat, lon) matching the ZONES in cityevents.html.
ZONES = {
    "highpark":      (43.6465, -79.4637),
    "annex":         (43.6700, -79.4040),
    "yorkville":     (43.6710, -79.3900),
    "kensington":    (43.6547, -79.4005),
    "queenwest":     (43.6470, -79.4010),
    "downtown":      (43.6540, -79.3807),
    "distillery":    (43.6503, -79.3596),
    "stlawrence":    (43.6487, -79.3716),
    "danforth":      (43.6779, -79.3496),
    "leslieville":   (43.6627, -79.3340),
    "entertainment": (43.6457, -79.3900),
    "liberty":       (43.6378, -79.4202),
    "bentway":       (43.6395, -79.4045),
    "harbourfront":  (43.6385, -79.3817),
}
DEFAULT_ZONE = "downtown"

# Keyword -> our category. First match wins; order matters (specific first).
CAT_RULES = [
    ("concert", "Music"), ("music", "Music"), ("jazz", "Music"), ("band", "Music"),
    ("film", "Film"), ("movie", "Film"), ("cinema", "Film"),
    ("theatre", "Art & Culture"), ("theater", "Art & Culture"), ("dance", "Art & Culture"),
    ("art", "Art & Culture"), ("exhibit", "Art & Culture"), ("gallery", "Art & Culture"),
    ("culture", "Art & Culture"), ("heritage", "Art & Culture"),
    ("market", "Markets"), ("bazaar", "Markets"), ("vendor", "Markets"),
    ("food", "Food & Drink"), ("taste", "Food & Drink"), ("culinary", "Food & Drink"),
    ("drink", "Food & Drink"), ("beer", "Food & Drink"), ("wine", "Food & Drink"),
    ("sport", "Sports & Fitness"), ("fitness", "Sports & Fitness"), ("run", "Sports & Fitness"),
    ("marathon", "Sports & Fitness"), ("yoga", "Sports & Fitness"), ("skate", "Sports & Fitness"),
    ("family", "Family"), ("kid", "Family"), ("child", "Family"),
    ("night", "Nightlife"),
    ("parade", "Festivals"), ("fair", "Festivals"), ("festival", "Festivals"),
    ("community", "Community"),
]
DEFAULT_CAT = "Festivals"

UA = {"User-Agent": "CityEvents-feed/1.0 (GitHub Actions; static site)"}


def http_text(url):
    req = urllib.request.Request(url, headers=dict(UA, **{"Accept": "application/json, text/plain, */*"}))
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read().decode("utf-8-sig", errors="replace").strip()


def http_json(url):
    return json.loads(http_text(url))


def unwrap(item):
    """Toronto wraps each event in a single key like {'calEvent': {...}}; unwrap it."""
    if isinstance(item, dict) and len(item) == 1:
        only = next(iter(item.values()))
        if isinstance(only, dict):
            return only
    return item


def coerce_records(raw):
    """Turn a downloaded data file into a flat list of event dicts."""
    if isinstance(raw, list):
        return [unwrap(r) for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        # GeoJSON FeatureCollection: flatten properties + keep geometry
        if isinstance(raw.get("features"), list):
            out = []
            for f in raw["features"]:
                if not isinstance(f, dict):
                    continue
                props = dict(f.get("properties") or {})
                if f.get("geometry"):
                    props["geometry"] = f["geometry"]
                out.append(props)
            return out
        for key in ("records", "events", "result", "value", "data", "rows"):
            v = raw.get(key)
            if isinstance(v, list):
                return [unwrap(r) for r in v if isinstance(r, dict)]
            if isinstance(v, dict) and isinstance(v.get("records"), list):
                return [unwrap(r) for r in v["records"] if isinstance(r, dict)]
    return []


def fetch_records():
    """Get event records however Toronto serves this dataset (datastore or file)."""
    data = http_json("{}/api/3/action/package_show?id={}".format(CKAN, PACKAGE))
    resources = data["result"]["resources"]
    print("Resources in dataset:",
          [(r.get("name"), r.get("format"), r.get("datastore_active")) for r in resources])

    # 1) queryable datastore tables
    for res in resources:
        if res.get("datastore_active"):
            rid = res["id"]
            try:
                url = "{}/api/3/action/datastore_search?{}".format(
                    CKAN, urllib.parse.urlencode({"resource_id": rid, "limit": MAX_RECORDS}))
                recs = http_json(url)["result"]["records"]
                if recs:
                    print("Loaded", len(recs), "records via datastore resource", rid)
                    return recs
            except Exception as e:
                print("datastore_search failed for", rid, ":", e)

    # 2) downloadable JSON / GeoJSON file resources
    for res in resources:
        fmt = (res.get("format") or "").lower()
        url = res.get("url")
        if url and fmt in ("json", "geojson"):
            print("Trying file resource", res.get("id"), "format", fmt)
            print("URL:", url)
            try:
                text = http_text(url)
            except Exception as e:
                print("could not download:", e)
                continue
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                print("Response was NOT valid JSON. First 300 characters:")
                print(repr(text[:300]))
                continue
            recs = coerce_records(raw)
            if recs:
                print("Loaded", len(recs), "records via", fmt, "file", res.get("id"))
                return recs
            print("Parsed JSON but found no records. Top-level type:", type(raw).__name__)
            if isinstance(raw, dict):
                print("Top-level keys:", list(raw.keys())[:20])
            elif isinstance(raw, list) and raw:
                print("First item keys:", list(raw[0].keys())[:20] if isinstance(raw[0], dict) else type(raw[0]).__name__)

    print("No usable resource found in the dataset.")
    return []


def pick(rec, *names):
    """Case-insensitive lookup that tolerates the City's varying field names."""
    low = {k.lower(): v for k, v in rec.items()}
    for n in names:
        v = low.get(n.lower())
        if v not in (None, "", [], {}):
            return v
    return None


def as_text(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v).strip()


def parse_dt(v):
    """Return (date 'YYYY-MM-DD', time 'HH:MM' or None) from many formats."""
    if v is None:
        return None, None
    s = as_text(v).strip()
    if not s:
        return None, None
    s = s.replace("Z", "").replace("/", "-")
    # try full ISO first
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m-%d-%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(s[:len(fmt) + 4] if "%H" in fmt else s[:10], fmt)
            t = "{:02d}:{:02d}".format(dt.hour, dt.minute) if "%H" in fmt and (dt.hour or dt.minute) else None
            return dt.strftime("%Y-%m-%d"), t
        except ValueError:
            continue
    # last resort: pull a YYYY-MM-DD substring
    import re
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0), None
    return None, None


def location_info(rec):
    """Pull (venue, lat, lon) from a Toronto 'locations' array if present."""
    locs = pick(rec, "locations", "location", "venues")
    if isinstance(locs, str):
        try:
            locs = json.loads(locs)
        except ValueError:
            locs = None
    if isinstance(locs, dict):
        locs = [locs]
    if isinstance(locs, list) and locs and isinstance(locs[0], dict):
        L = locs[0]
        venue = L.get("locationName") or L.get("name") or L.get("address") or L.get("addressName")
        coords = L.get("coords") if isinstance(L.get("coords"), dict) else L
        lat = coords.get("lat") if isinstance(coords, dict) else None
        lon = (coords.get("lng") or coords.get("lon") or coords.get("long")) if isinstance(coords, dict) else None
        try:
            lat = float(lat) if lat is not None else None
            lon = float(lon) if lon is not None else None
        except (TypeError, ValueError):
            lat = lon = None
        return (str(venue).strip() if venue else None), lat, lon
    return None, None, None


def latlon(rec):
    """Extract (lat, lon) from a geometry field, lat/long columns, or a locations array."""
    g = pick(rec, "geometry", "geo", "location_geometry")
    if g:
        if isinstance(g, str):
            try:
                g = json.loads(g)
            except ValueError:
                g = None
        if isinstance(g, dict):
            c = g.get("coordinates")
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                try:
                    return float(c[1]), float(c[0])  # GeoJSON is [lon, lat]
                except (TypeError, ValueError):
                    pass
    lat = pick(rec, "lat", "latitude", "y")
    lon = pick(rec, "long", "lng", "lon", "longitude", "x")
    try:
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    except (TypeError, ValueError):
        pass
    _, la, lo = location_info(rec)
    return la, lo


def nearest_zone(lat, lon):
    if lat is None or lon is None:
        return DEFAULT_ZONE
    best, bestd = DEFAULT_ZONE, 1e9
    for key, (zlat, zlon) in ZONES.items():
        d = (lat - zlat) ** 2 + (lon - zlon) ** 2
        if d < bestd:
            best, bestd = key, d
    return best


def map_category(text):
    t = (text or "").lower()
    for kw, cat in CAT_RULES:
        if kw in t:
            return cat
    return DEFAULT_CAT


def cost_to_price(v):
    """Return (free_bool, amount, price_label)."""
    if isinstance(v, list):
        v = v[0] if v else None
    if isinstance(v, dict):
        v = v.get("description") or v.get("name") or v.get("label") or v.get("cost")
    if v is None:
        return True, 0, None
    raw = as_text(v).strip()
    s = raw.lower()
    if s in ("", "free", "no charge", "none", "0", "$0", "free admission", "free event"):
        return True, 0, None
    import re
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if m:
        return False, float(m.group(1)), raw
    return False, 0, raw


def google_url(title):
    return "https://www.google.com/search?" + urllib.parse.urlencode(
        {"q": (title or "Toronto event") + " Toronto"}
    )


def normalize_record(rec):
    """Turn one City record into the event object cityevents.html expects."""
    title = pick(rec, "eventName", "Event Name", "name", "title", "Calendar Name", "Event_Name")
    if not title:
        return None
    title = as_text(title)

    start_raw = pick(rec, "startDateTime", "Start Date", "startDate", "start_date", "startDt",
                     "dateRangeStart", "Date_Begin", "start", "Event Start Date")
    end_raw = pick(rec, "endDateTime", "End Date", "endDate", "end_date", "endDt",
                   "dateRangeEnd", "Date_End", "end", "Event End Date")

    # Some feeds bundle occurrences in a JSON "dates" array; take the first.
    if not start_raw:
        dates = pick(rec, "dates", "occurrences", "calEvent")
        if isinstance(dates, str):
            try:
                dates = json.loads(dates)
            except ValueError:
                dates = None
        if isinstance(dates, list) and dates:
            d0 = dates[0]
            if isinstance(d0, dict):
                start_raw = d0.get("startDateTime") or d0.get("start") or d0.get("startDate")
                end_raw = end_raw or d0.get("endDateTime") or d0.get("end") or d0.get("endDate")

    start, time = parse_dt(start_raw)
    if not start:
        return None
    end, _ = parse_dt(end_raw)
    if not end:
        end = start

    desc = as_text(pick(rec, "description", "Description", "eventDescription", "details") or "")
    desc = " ".join(desc.split())
    if len(desc) > DESC_LIMIT:
        desc = desc[:DESC_LIMIT].rsplit(" ", 1)[0] + "..."

    venue = pick(rec, "locationName", "Location", "venueName", "venue", "address", "Address", "PlaceName")
    if not venue:
        venue, _, _ = location_info(rec)
    venue = as_text(venue or "Toronto")
    free, amount, price_label = cost_to_price(pick(rec, "cost", "Cost", "admission", "Admission", "price"))
    fe = pick(rec, "freeEvent", "free", "isFree")
    if isinstance(fe, str) and fe.strip().lower() in ("yes", "true", "y", "1"):
        free, amount, price_label = True, 0, None
    url = pick(rec, "eventWebsite", "website", "Website", "url", "URL", "link")
    url = as_text(url) if url else google_url(title)
    if not url.lower().startswith("http"):
        url = google_url(title)

    cat_src = " ".join(filter(None, [
        as_text(pick(rec, "category", "Category", "categoryString", "eventType", "type") or ""),
        title, desc,
    ]))
    lat, lon = latlon(rec)

    ev = {
        "title": title,
        "venue": venue,
        "zone": nearest_zone(lat, lon),
        "category": map_category(cat_src),
        "source": "City of Toronto",
        "url": url,
        "start": start,
        "end": end,
        "free": free,
        "amount": amount,
        "desc": desc or (title + " in Toronto."),
    }
    if time:
        ev["time"] = time
    if price_label and not free:
        ev["priceLabel"] = price_label
    return ev


def build(records):
    today = date.today().isoformat()
    seen = set()
    out = []
    for rec in records:
        try:
            ev = normalize_record(rec)
        except Exception as e:  # never let one bad record stop the feed
            print("  skipped a record:", e)
            continue
        if not ev:
            continue
        if ev["end"] < today:           # drop events already finished
            continue
        key = (ev["title"].lower().strip(), ev["start"])
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    out.sort(key=lambda e: e["start"])
    return out[:MAX_OUTPUT]


def main():
    print("CityEvents feed builder starting...")
    try:
        records = fetch_records()
        print("Records returned:", len(records))
        if records:
            print("FIELD NAMES IN FIRST RECORD:", list(records[0].keys()))
            print("SAMPLE RECORD:", json.dumps(records[0], indent=2)[:1500])
    except Exception as e:
        print("ERROR reaching the City feed:", e)
        print("Leaving the existing events.json untouched.")
        return 0

    events = build(records)
    print("Usable upcoming events:", len(events))

    if not events:
        print("No events extracted. Leaving existing events.json untouched so the page stays populated.")
        print("Send the FIELD NAMES line above for a quick mapping fix.")
        return 0

    payload = {
        "source": "CityEvents",
        "city": "Toronto",
        "feed": "City of Toronto Open Data - Festivals and Events",
        "generated": datetime.utcnow().isoformat() + "Z",
        "count": len(events),
        "events": events,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("Wrote", OUT, "with", len(events), "events.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
