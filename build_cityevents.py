import os
import json
import urllib.parse
from datetime import datetime
import xml.etree.ElementTree as ET

# We use httpx with http2 support to bypass TLS fingerprinting gates
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    import requests

OUTPUT_FILE = "cityevents.json"

# Suppress noisy placeholder errors. Update this URL once you deploy your free Cloudflare Worker.
PROXY_URL = "https://your-worker-proxy.workers.dev/fetch?url="
PROXY_URL_PLACEHOLDER = "https://your-worker-proxy.workers.dev/fetch?url="

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}

# Baseline verified Toronto events for robust offline fallbacks
BASELINE_EVENTS = [
    {
        "title": "adidas Home of Soccer: World Cup Watch Parties",
        "venue": "STACKT market · 28 Bathurst St",
        "zone": "bentway",
        "category": "Sports & Fitness",
        "source": "STACKT Market",
        "url": "https://stacktmarket.com/event/adidas-home-of-soccer-toronto/",
        "start": "2026-06-12",
        "end": "2026-07-19",
        "free": True,
        "amount": 0,
        "desc": "adidas turns STACKT into a soccer hub with an outdoor viewing area for official FIFA World Cup watch parties, plus activations all summer."
    },
    {
        "title": "Dream in High Park: Twelfth Night",
        "venue": "High Park Amphitheatre · 1873 Bloor St W",
        "zone": "highpark",
        "category": "Art & Culture",
        "source": "Canadian Stage",
        "url": "https://www.canadianstage.com/shows-events/dream-in-high-park-26",
        "start": "2026-07-12",
        "end": "2026-09-06",
        "free": False,
        "amount": 35,
        "desc": "Canadian Stage's 43rd season of outdoor Shakespeare under the stars. Tue-Sat 8pm, Sun 7pm. Pack a picnic."
    },
    {
        "title": "FIFA Fan Festival (Fort York × The Bentway)",
        "venue": "The Bentway · Fort York",
        "zone": "bentway",
        "category": "Festivals",
        "source": "The Bentway",
        "url": "https://thebentway.ca/news/the-bentway-named-as-a-host-site-for-torontos-fifa-fan-festival-coming-summer-2026/",
        "start": "2026-06-11",
        "end": "2026-07-19",
        "free": True,
        "amount": 0,
        "desc": "Toronto's central fan destination with big-screen match broadcasts, cultural performances, art installations, and local food."
    },
    {
        "title": "Summer Music in the Garden (25th season)",
        "venue": "Toronto Music Garden",
        "zone": "harbourfront",
        "category": "Music",
        "source": "Harbourfront Centre",
        "url": "https://harbourfrontcentre.com/whats-on/",
        "start": "2026-06-21",
        "end": "2026-08-27",
        "free": True,
        "amount": 0,
        "desc": "Free concerts in the lakeside Toronto Music Garden, marking the series' 25th season."
    },
    {
        "title": "TD Toronto Jazz Festival: Free Outdoor Sets",
        "venue": "Bloor-Yorkville · OLG Village Stage",
        "zone": "yorkville",
        "category": "Music",
        "source": "Toronto Jazz Festival",
        "url": "https://torontojazz.com/",
        "start": "2026-06-19",
        "end": "2026-06-28",
        "free": True,
        "amount": 0,
        "desc": "Ten days of free open-air jazz across Bloor-Yorkville, plus Sidewalk Sessions at five locations."
    },
    {
        "title": "Give Me Liberty Street Party",
        "venue": "Lamport Stadium lot · 75 Fraser Ave",
        "zone": "liberty",
        "category": "Festivals",
        "source": "Liberty Village BIA",
        "url": "https://www.libertyvillagebia.com/events/give-me-liberty-street-party",
        "start": "2026-09-17",
        "end": "2026-09-17",
        "free": True,
        "amount": 0,
        "desc": "The Liberty Village BIA's signature block party: 40+ local vendors, live music, a beer garden, and activities for all ages."
    }
]

NEIGHBORHOOD_MAP = {
    "stackt": "bentway",
    "bentway": "bentway",
    "fort york": "bentway",
    "bathurst": "bentway",
    "harbourfront": "harbourfront",
    "queens quay": "harbourfront",
    "music garden": "harbourfront",
    "bloor-yorkville": "yorkville",
    "yorkville": "yorkville",
    "danforth": "danforth",
    "greektown": "danforth",
    "church": "downtown",
    "wellesley": "downtown",
    "yonge": "downtown",
    "luminato": "downtown",
    "high park": "highpark",
    "lamport": "liberty",
    "liberty village": "liberty",
    "fraser": "liberty",
    "kensington": "kensington",
    "st. lawrence": "stlawrence",
    "distillery": "distillery",
    "leslieville": "leslieville",
    "queen west": "queenwest",
    "ossington": "queenwest",
    "bellwoods": "queenwest",
    "annex": "annex",
    "comedy bar": "queenwest"
}

def clean_html(raw_html):
    if not raw_html:
        return ""
    import html
    import re
    clean_text = re.sub('<[^<]+?>', '', raw_html)
    return html.unescape(clean_text).strip()

def determine_zone(title, venue, description):
    search_text = f"{title} {venue} {description}".lower()
    for keyword, zone_key in NEIGHBORHOOD_MAP.items():
        if keyword in search_text:
            return zone_key
    return "downtown"

def fetch_url(url):
    """Fetches a URL using HTTPX with HTTP2, falling back cleanly to Requests if needed."""
    if HAS_HTTPX:
        try:
            with httpx.Client(http2=True) as client:
                response = client.get(url, headers=BROWSER_HEADERS, timeout=12.0)
                if response.status_code == 403:
                    print(f"[WAF BLOCK] Standard access to {url} was rejected (403).")
                    return redirect_to_proxy(url)
                response.raise_for_status()
                return response.text
        except Exception as e:
            print(f"[NETWORK ERROR] HTTPX connection failed for {url}: {e}")
            return redirect_to_proxy(url)
    else:
        try:
            response = requests.get(url, headers=BROWSER_HEADERS, timeout=12.0)
            if response.status_code == 403:
                print(f"[WAF BLOCK] Standard requests call to {url} was rejected (403).")
                return redirect_to_proxy(url)
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"[NETWORK ERROR] Requests connection failed for {url}: {e}")
            return redirect_to_proxy(url)

def redirect_to_proxy(target_url):
    """Routes blocked requests through a custom proxy only if it has been configured."""
    if PROXY_URL == PROXY_URL_PLACEHOLDER:
        print("[INFO] Proxy redirect skipped: Proxy URL is still configured with placeholder.")
        return None
        
    encoded_url = urllib.parse.quote(target_url)
    proxy_request_url = f"{PROXY_URL}{encoded_url}"
    print(f"[REDIRECT] Routing request through edge proxy: {proxy_request_url}")
    try:
        if HAS_HTTPX:
            with httpx.Client(http2=True) as client:
                resp = client.get(proxy_request_url, timeout=12.0)
                if resp.status_code == 200:
                    return resp.text
        else:
            resp = requests.get(proxy_request_url, timeout=12.0)
            if resp.status_code == 200:
                return resp.text
    except Exception as e:
        print(f"[PROXY FAULT] Serverless gateway was unreachable: {e}")
    return None

def fetch_toronto_json_url():
    """Queries Toronto's CKAN API to dynamically fetch the active JSON feed path."""
    print("[INGEST] Resolving active resource path from City of Toronto CKAN API...")
    ckan_package_api = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id=festivals-and-events"
    metadata_raw = fetch_url(ckan_package_api)
    if not metadata_raw:
        return None
    try:
        metadata = json.loads(metadata_raw)
        if metadata.get("success"):
            resources = metadata.get("result", {}).get("resources", [])
            for res in resources:
                # Find the resource matching the JSON output format type
                if res.get("format", "").lower() == "json":
                    print(f"[INGEST] Dynamic event JSON resource resolved: {res.get('url')}")
                    return res.get("url")
    except Exception as e:
        print(f"[ERROR] Failed to parse CKAN metadata payload: {e}")
    return None

def ingest_toronto_open_data():
    """Ingests data from the resolved Toronto Open Data JSON URL."""
    json_url = fetch_toronto_json_url()
    if not json_url:
        print("[WARNING] Could not resolve Toronto Open Data URL. Pipeline falling back.")
        return []
        
    raw_data = fetch_url(json_url)
    if not raw_data:
        return []

    try:
        events_json = json.loads(raw_data)
        parsed_events = []
        for item in events_json:
            cal_event = item.get("calEvent", {})
            title = cal_event.get("eventName", "")
            description = clean_html(cal_event.get("description", ""))
            
            locations = cal_event.get("locations", [])
            venue = "Toronto, ON"
            if locations:
                venue = locations[0].get("locationName", "Toronto, ON")
            
            zone = determine_zone(title, venue, description)
            is_free = cal_event.get("freeEvent", "No") == "Yes"
            source_url = cal_event.get("eventURL", "https://open.toronto.ca")
            
            start_date = cal_event.get("startDate", "").split("T")[0]
            end_date = cal_event.get("endDate", "").split("T")[0]
            
            if not title or not start_date:
                continue

            parsed_events.append({
                "title": title,
                "venue": venue,
                "zone": zone,
                "category": "Community",
                "source": "City of Toronto",
                "url": source_url,
                "start": start_date,
                "end": end_date,
                "free": is_free,
                "amount": 0,
                "desc": description[:220] + "..." if len(description) > 220 else description
            })
        print(f"[INGEST] Processed {len(parsed_events)} live events from Toronto Open Data.")
        return parsed_events
    except Exception as e:
        print(f"[ERROR] Failed to parse Toronto Open Data payload: {e}")
        return []

def ingest_blogto_rss():
    """Ingests data from the blogTO events RSS feed."""
    print("[INGEST] Querying blogTO RSS Feed...")
    rss_url = "https://www.blogto.com/feeds/events/"
    raw_xml = fetch_url(rss_url)
    if not raw_xml:
        return []
    try:
        root = ET.fromstring(raw_xml)
        parsed_events = []
        for item in root.findall(".//item"):
            title = item.find("title").text
            description = clean_html(item.find("description").text)
            link = item.find("link").text
            venue = "Toronto Venue"
            zone = determine_zone(title, venue, description)
            today_str = datetime.today().strftime("%Y-%m-%d")
            
            parsed_events.append({
                "title": title,
                "venue": venue,
                "zone": zone,
                "category": "Art & Culture",
                "source": "blogTO",
                "url": link,
                "start": today_str,
                "end": today_str,
                "free": True,
                "amount": 0,
                "desc": description[:220] + "..." if len(description) > 220 else description
            })
        print(f"[INGEST] Processed {len(parsed_events)} live events from blogTO RSS.")
        return parsed_events
    except Exception as e:
        print(f"[ERROR] Failed to parse blogTO RSS: {e}")
        return []

def jaccard_similarity(str1, str2):
    words1 = set(str1.lower().split())
    words2 = set(str2.lower().split())
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    return len(intersection) / len(union) if union else 0

def deduplicate_and_merge(incoming_events):
    """Deduplicates incoming scrape results against themselves and baseline items."""
    unique_list = []
    blocked_count = 0
    
    # Pre-populate with our verified baseline events
    unique_list.extend(BASELINE_EVENTS)
    
    for item in incoming_events:
        is_duplicate = False
        for existing in unique_list:
            if item["start"] == existing["start"]:
                similarity = jaccard_similarity(item["title"], existing["title"])
                if similarity >= 0.72:
                    is_duplicate = True
                    blocked_count += 1
                    break
        if not is_duplicate:
            unique_list.append(item)
            
    print(f"[DEDUPE] Deduping complete. Blocked {blocked_count} duplicate events.")
    return unique_list

def main():
    print("[PIPELINE] Starting automated event ingestion script...")
    
    open_data_events = ingest_toronto_open_data()
    blogto_events = ingest_blogto_rss()
    
    all_incoming = open_data_events + blogto_events
    
    # Merge, deduplicate, and preserve baseline events
    final_dataset = deduplicate_and_merge(all_incoming)
    
    # Sort events chronologically by start date
    final_dataset.sort(key=lambda x: x["start"])
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_dataset, f, indent=2, ensure_ascii=False)
        
    print(f"[SUCCESS] Ingestion completed. Saved {len(final_dataset)} events to {OUTPUT_FILE}.")

if __name__ == "__main__":
    main()
