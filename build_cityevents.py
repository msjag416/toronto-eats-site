import os
import json
import urllib.parse
from datetime import datetime
import xml.etree.ElementTree as ET
import requests # Ensure requests is always available

# Attempt to use httpx with http2, fall back to requests if not present
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

OUTPUT_FILE = "cityevents.json"

# Update this URL to point to your deployed Cloudflare Worker
PROXY_URL = "https://toronto-events-proxy.msjag416.workers.dev/?url="

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}

# Baseline events (always included as a fallback)
BASELINE_EVENTS = [
    {"title": "adidas Home of Soccer: World Cup Watch Parties", "venue": "STACKT market", "zone": "bentway", "category": "Sports & Fitness", "source": "STACKT Market", "url": "https://stacktmarket.com/event/adidas-home-of-soccer-toronto/", "start": "2026-06-12", "end": "2026-07-19", "free": True, "amount": 0, "desc": "Official FIFA World Cup watch parties."},
    {"title": "Dream in High Park: Twelfth Night", "venue": "High Park Amphitheatre", "zone": "highpark", "category": "Art & Culture", "source": "Canadian Stage", "url": "https://www.canadianstage.com/", "start": "2026-07-12", "end": "2026-09-06", "free": False, "amount": 35, "desc": "Outdoor Shakespeare under the stars."}
]

def fetch_url(url):
    """Fetches via proxy if the direct call fails."""
    # 1. Try Direct
    try:
        if HAS_HTTPX:
            with httpx.Client(http2=True) as client:
                resp = client.get(url, headers=BROWSER_HEADERS, timeout=10)
                if resp.status_code == 200: return resp.text
        else:
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
            if resp.status_code == 200: return resp.text
    except Exception as e:
        print(f"[DEBUG] Direct fetch failed: {e}")

    # 2. Try Proxy
    proxy_url = f"{PROXY_URL}{urllib.parse.quote(url)}"
    print(f"[REDIRECT] Proxying request: {proxy_url}")
    try:
        resp = requests.get(proxy_url, headers=BROWSER_HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        print(f"[PROXY FAULT] {e}")
    return None

def ingest_toronto_open_data():
    # Direct reliable download URL for the Festival dataset
    json_url = "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/festivals-and-events/resource/66e3cf05-6447-49d7-867c-2b2a8d38a8e1/download/festivals-and-events-json.json"
    raw = fetch_url(json_url)
    if not raw: return []
    try:
        data = json.loads(raw)
        return [{"title": e.get("event_name"), "venue": "Toronto", "zone": "downtown", "category": "Community", "source": "City of Toronto", "url": "https://open.toronto.ca", "start": e.get("start_date", ""), "end": e.get("end_date", ""), "free": True, "amount": 0, "desc": "Event from City Data"} for e in data[:10]]
    except: return []

def main():
    print("[PIPELINE] Starting...")
    data = ingest_toronto_open_data() + BASELINE_EVENTS
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"[SUCCESS] Saved {len(data)} events.")

if __name__ == "__main__":
    main()
