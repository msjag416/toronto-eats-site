"""
build_review_pages.py

Builds Google-friendly pages from the review list in archive.html.

  * One page per review ............ /reviews/<name-city>.html
  * City and area pages ............ /toronto/, /grand-cayman/, /mississauga/, /canada/, /world/
  * Toronto topic pages ............ /toronto/restaurants/, /toronto/nightlife/, /toronto/services/
  * sitemap.xml .................... every page above, plus the hand-made pages already listed

archive.html stays the single source of truth. Nothing here edits archive.html
or index.html. Run it with:  python build_review_pages.py

GitHub runs it on its own after every change to archive.html
(see .github/workflows/build-review-pages.yml).
"""

import html
import json
import os
import re
import shutil
import unicodedata
from collections import defaultdict
from datetime import datetime
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET

SITE = "https://torontoeatsandreviews.com"
ROOT = os.path.dirname(os.path.abspath(__file__))
GOOGLE_PROFILE = "https://maps.app.goo.gl/w3JyVs56oL8oc73r7"
PHOTO_MAP_FILE = os.path.join(ROOT, "review-photos.json")

# Folders this program owns. They are wiped and rebuilt on every run.
GENERATED_DIRS = ["reviews", "toronto", "grand-cayman", "mississauga", "canada", "world"]

SCORE_LABELS = {5: "Editor's Pick", 4: "Recommended", 3: "Mixed", 2: "Not Recommended", 1: "Avoid"}
CATEGORY_WORDS = {"Dining": "Restaurants", "Nightlife": "Nightlife", "Service": "Services", "Travel": "Travel"}

PROVINCES = {
    "ON": "Ontario", "BC": "British Columbia", "AB": "Alberta", "QC": "Quebec", "NS": "Nova Scotia",
    "NB": "New Brunswick", "PE": "Prince Edward Island", "NL": "Newfoundland and Labrador",
    "MB": "Manitoba", "SK": "Saskatchewan",
}
PROVINCE_NAMES = {v.lower(): k for k, v in PROVINCES.items()}
# Former boroughs that are part of the City of Toronto
TORONTO_AREAS = {"toronto", "north york", "etobicoke", "east york", "york", "scarborough"}


# ─────────────────────────── Toronto neighbourhoods ───────────────────────────
# Postal-code prefixes (the first three characters) grouped into the names people search for.

NEIGHBOURHOODS = [
    ("King West & Entertainment District", ["M5V"], "King Street West, the Entertainment District, CityPlace and the Fashion District."),
    ("Liberty Village & Parkdale", ["M6K"], "Liberty Village, Parkdale and the west end of King and Queen."),
    ("Queen West & Trinity Bellwoods", ["M6J"], "Queen West, Ossington and the streets around Trinity Bellwoods Park."),
    ("Harbourfront & Union Station", ["M5J"], "The waterfront, Union Station, Scotiabank Arena and the South Core."),
    ("Financial District", ["M5H", "M5K", "M5L", "M5X", "M5C"], "Bay Street, the PATH, King and Adelaide, and the Old Town edge of downtown."),
    ("Downtown Yonge & Dundas", ["M5G", "M5B"], "Yonge-Dundas Square, the Eaton Centre, TMU and the hospital row on University."),
    ("Yorkville & The Annex", ["M5R", "M5S"], "Yorkville, Bloor Street, the Annex and the U of T campus."),
    ("Kensington Market & Chinatown", ["M5T"], "Kensington Market, Chinatown and the Spadina strip."),
    ("Distillery District & Corktown", ["M5A"], "The Distillery District, Corktown, Regent Park and the Canary District."),
    ("St. Lawrence Market", ["M5E"], "St. Lawrence Market, Front Street East and the Esplanade."),
    ("Rosedale, Cabbagetown & Summerhill", ["M4W", "M4X", "M4T", "M4V"], "Rosedale, Cabbagetown, Summerhill and Moore Park."),
    ("Church-Wellesley Village", ["M4Y"], "The Village, Church Street and Yonge north of Wellesley."),
    ("Midtown: Yonge & Eglinton", ["M4P", "M4S", "M4R", "M5N", "M5P"], "Yonge and Eglinton, Davisville and Forest Hill."),
    ("Little Italy & Dovercourt", ["M6G", "M6H"], "Little Italy, Little Portugal, Christie Pits and Dovercourt."),
    ("Leslieville, Riverdale & The Beaches", ["M4M", "M4L", "M4K", "M4E", "M4J", "M4C"], "Leslieville, Riverdale, the Danforth and the Beaches."),
    ("Leaside", ["M4G", "M4H"], "Leaside and Thorncliffe Park."),
    ("Don Mills", ["M3C", "M3B", "M3A", "M4A"], "Don Mills, the Shops at Don Mills and Victoria Village."),
    ("The Junction, High Park & Roncesvalles", ["M6N", "M6P", "M6S", "M6R"], "The Junction, High Park, Bloor West Village and Roncesvalles."),
    ("Lawrence Park & Yorkdale", ["M6A", "M6B", "M5M", "M4N"], "Lawrence Park, Bedford Park and the Yorkdale area."),
    ("St. Clair West", ["M6C", "M6E"], "St. Clair West, Oakwood Village and Humewood."),
    ("Etobicoke", ["M8", "M9"], "Etobicoke, from the Lakeshore to Rexdale."),
    ("Scarborough", ["M1"], "Scarborough."),
    ("North York", ["M2", "M3H", "M3J", "M3K", "M3L", "M3M", "M3N"], "North York, Willowdale, Bayview Village and Downsview."),
]
MIN_PER_PAGE = 3  # a neighbourhood or cuisine page needs at least this many reviews


# Downtown streets we can place without a postal code (number, street) -> postal prefix
STREET_RULES = [
    (r"\bqueen st(reet)? w", lambda n: "M5V" if n < 500 else ("M6J" if n < 1000 else "M6K")),
    (r"\bking st(reet)? w", lambda n: "M5V" if n < 800 else "M6K"),
    (r"\b(front|wellington|adelaide|richmond) st(reet)? w", lambda n: "M5V" if n < 700 else "M6K"),
    (r"\b(blue jays way|john st|peter st|duncan st|mercer st|widmer st)\b", lambda n: "M5V"),
    (r"\bqueen st(reet)? e", lambda n: "M5A" if n < 700 else "M4M"),
    (r"\bking st(reet)? e", lambda n: "M5C" if n < 200 else "M5A"),
]


def street_fsa(address):
    low = address.lower()
    m = re.match(r"\s*(\d+)", low)
    number = int(m.group(1)) if m else 0
    for pattern, rule in STREET_RULES:
        if re.search(pattern, low):
            return rule(number)
    return None


def neighbourhood_for(r):
    """Returns (name, slug, blurb) for a Toronto review, or None."""
    if r["place"]["hub"] != "toronto":
        return None
    address = r.get("address", "")
    m = re.search(r"\b(M\d[A-Z])\s?\d[A-Z]\d\b", address, re.I)
    fsa = m.group(1).upper() if m else street_fsa(address)
    if not fsa:
        return None
    for name, prefixes, blurb in NEIGHBOURHOODS:
        if any(fsa.startswith(pfx) for pfx in prefixes):
            return (name, slugify(name), blurb)
    return None


# ─────────────────────────── Toronto cuisines & features ───────────────────────────
# Found from the review text. A match in the business name counts for a lot; words in the
# review count less, so one passing mention of "coffee" in a dinner review doesn't tag it.

CUISINES = [
    ("Pizza", "pizza", r"\bpizz"),
    ("Burgers", "burgers", r"\bburger"),
    ("Sushi & Japanese", "sushi", r"\bsushi\b|\bjapanese\b|\bramen\b|\bizakaya\b|\bsashimi\b"),
    ("Thai", "thai", r"\bthai\b|\bpad thai\b"),
    ("Italian", "italian", r"\bitalian\b|\bpasta\b|\btrattoria\b|\bosteria\b"),
    ("Indian", "indian", r"\bindian\b|\bcurry\b|\btandoor|\bbiryani\b|\bnaan\b"),
    ("Chinese", "chinese", r"\bchinese\b|\bdim sum\b|\bcantonese\b|\bszechuan\b|\bdumpling"),
    ("Caribbean", "caribbean", r"\bcaribbean\b|\bjamaican\b|\bjerk\b|\broti\b|\btrini"),
    ("Mexican", "mexican", r"\bmexican\b|\btaco|\bburrito"),
    ("Steakhouses", "steakhouses", r"\bsteak"),
    ("Seafood", "seafood", r"\bseafood\b|\boyster|\blobster\b|\bcrab\b|\bshrimp\b|\bcalamari\b|\bmussels\b|\bceviche\b"),
    ("Brunch & Breakfast", "brunch", r"\bbrunch\b|\bbreakfast\b|\beggs benedict\b|\bpancake"),
    ("Coffee Shops & Cafés", "coffee-shops", r"\bcoffee\b|\bcaf[eé]\b|\bespresso\b|\blatte\b|\bcappuccino\b"),
    ("Desserts & Bakeries", "desserts", r"\bdessert|\bbakery\b|\bice cream\b|\bgelato\b|\bpastr|\bcroissant|\bdonut|\bdoughnut|\bcheesecake\b"),
    ("Pubs & Breweries", "pubs", r"\bpub\b|\bgastropub\b|\bbrewery\b|\bcraft beer\b|\btaproom\b"),
    ("Patios & Rooftops", "patios", r"\bpatio\b|\brooftop\b"),
    ("Wings", "wings", r"\bwings\b"),
]


def cuisine_tags(r):
    if r["place"]["hub"] != "toronto" or r["category"] not in ("Dining", "Nightlife"):
        return []
    name = r["name"].lower()
    text = r["comment"].lower()
    tags = []
    for label, slug, pattern in CUISINES:
        # Tag only when the cuisine is in the business name, or comes up at least three times in the review
        score = 3 * len(re.findall(pattern, name)) + len(re.findall(pattern, text))
        if score >= 3:
            if slug == "seafood" and re.search(r"\bvegan\b|\bplant-based\b", name + " " + text):
                continue
            tags.append((label, slug))
    return tags


# ─────────────────────────── small, fast photo copies ───────────────────────────

def make_thumb(photo_url):
    """Makes a 640px-wide WebP copy of a photo for cards. Returns (url, width, height) or None."""
    name = photo_url.rsplit("/", 1)[-1]
    src = os.path.join(ROOT, name)
    if not os.path.exists(src):
        return None
    out_name = os.path.splitext(name)[0] + ".webp"
    out = os.path.join(ROOT, "thumbs", out_name)
    try:
        from PIL import Image
        if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(src):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            im = Image.open(src).convert("RGB")
            im.thumbnail((640, 640))
            im.save(out, "WEBP", quality=78, method=6)
        w, h = Image.open(out).size
        return (f"/thumbs/{out_name}", w, h)
    except Exception:
        return None


# ─────────────────────────── reading the archive ───────────────────────────

def load_reviews():
    src = open(os.path.join(ROOT, "archive.html"), encoding="utf-8").read()
    start = src.index("const reviews = [") + len("const reviews = ")
    end = src.index("];", start) + 1
    return json.loads(src[start:end])


def load_photo_map():
    """Photos come from the homepage cards. Once a photo is linked to a review,
    it is kept in review-photos.json so it stays after the card leaves the homepage."""
    photos = {}
    if os.path.exists(PHOTO_MAP_FILE):
        photos = json.load(open(PHOTO_MAP_FILE, encoding="utf-8"))
    src = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', src, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if data.get("@type") != "ItemList":
            continue
        for item in data.get("itemListElement", []):
            name = (item.get("itemReviewed") or {}).get("name")
            image = item.get("image") or (item.get("itemReviewed") or {}).get("image")
            if isinstance(image, list):
                image = image[0] if image else None
            if name and image:
                photos[name] = image
    return photos


def parse_date(text):
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%b %d %Y"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except (ValueError, AttributeError):
            pass
    return None


def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ").replace("'", "")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return re.sub(r"-{2,}", "-", text)


def parse_place(address):
    """Works out city, region, country and which hub page a review belongs to."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    place = {"street": parts[0] if parts else "", "city": "", "region": "", "postal": "",
             "country": "", "country_name": "", "hub": "world", "area": ""}
    if not parts:
        place["street"] = ""
        place["hub"] = None  # online businesses with no address
        return place

    if "cayman" in address.lower():
        place.update(country="KY", country_name="Cayman Islands", region="Grand Cayman", hub="grand-cayman")
        exact = [i for i, p in enumerate(parts) if p.lower() == "grand cayman"]
        idx = exact[-1] if exact else len(parts) - 1
        place["city"] = re.sub(r"\s*KY\d-\d{4}", "", parts[idx - 1]).strip() if idx >= 1 else "Grand Cayman"
        place["area"] = place["city"]
        if re.match(r"^[0-9A-Z]{4}\+", place["street"]) or "cayman" in place["street"].lower():  # Google plus codes are not street addresses
            place["street"] = ""
        if place["city"] == place["street"]:
            place["street"] = ""
        return place

    for i, p in enumerate(parts):
        m = re.match(r"^(ON|BC|AB|QC|NS|NB|PE|NL|MB|SK)\b\s*(.*)$", p)
        if not m and p.lower().startswith("quebec"):
            m = re.match(r"^(Quebec)\b\s*(.*)$", p)
        if m and i >= 1:
            code = m.group(1) if m.group(1) in PROVINCES else PROVINCE_NAMES.get(m.group(1).lower(), "")
            place.update(city=parts[i - 1], region=code, postal=m.group(2).strip(),
                         country="CA", country_name="Canada")
            if i - 1 == 0:
                place["street"] = ""
            low = place["city"].lower()
            if low in TORONTO_AREAS:
                place["hub"] = "toronto"
                place["area"] = place["city"]
            elif low == "mississauga":
                place["hub"] = "mississauga"
            else:
                place["hub"] = "canada"
            return place

    # Everywhere else: last part is the country, the part before it holds the city
    place["country_name"] = parts[-1]
    if len(parts) >= 3:
        cityish = parts[-2]
        us = re.match(r"^(.*?)\s*,?\s*([A-Z]{2})\s+\d{5}$", cityish)
        if us:
            place["city"] = parts[-3]
            place["region"] = us.group(2)
            place["postal"] = cityish.split()[-1]
        else:
            place["city"] = clean_city(parts[-2]) or clean_city(parts[-3])
    airport = next((p for p in parts if "airport" in p.lower()), None)
    if airport:
        place["city"] = airport
        place["street"] = ""
    elif len(parts) == 2:
        place["city"] = re.sub(r"\d+", "", parts[0]).strip()
        place["street"] = ""
    if place["country_name"] == "United States":
        place["country"] = "US"
    return place


def clean_city(text):
    """Drops postal codes and short codes, e.g. '1012 TP Amsterdam' -> 'Amsterdam', 'Q.R.' -> ''."""
    words = [w for w in text.split() if not re.search(r"\d", w)]
    while words and re.fullmatch(r"[A-Z]{1,3}", words[0]):
        words.pop(0)
    out = " ".join(words)
    return "" if re.fullmatch(r"([A-Z]\.)+", out) else out


def schema_type(r):
    name = r["name"].lower()
    if r["category"] == "Dining":
        if re.search(r"\b(bar|pub|lounge|tavern|brewery|taproom)\b", name):
            return "BarOrPub"
        if re.search(r"\b(caf[eé]|coffee|espresso|bakery)\b", name):
            return "CafeOrCoffeeShop"
        return "Restaurant"
    if re.search(r"\b(hotel|resort|inn|suites?|motel|marriott|hilton|hyatt|sandals|secrets|mgm grand|ibis|shore club)\b", name) \
            and "buffet" not in name:
        return "Hotel"
    if r["category"] == "Nightlife":
        return "NightClub" if re.search(r"night ?club|club\b", name) else "BarOrPub"
    if re.search(r"\b(bar|pub|lounge|tavern|brewery|taproom)\b", name) and r["category"] != "Service":
        return "BarOrPub"
    if re.search(r"\b(caf[eé]|coffee|espresso|tea house|bakery)\b", name):
        return "CafeOrCoffeeShop"
    if r["category"] == "Dining" or re.search(r"\b(restaurant|kitchen|grill|pizza|pizzeria|burger|sushi|diner|bistro|buffet)\b", name):
        return "Restaurant"
    if re.search(r"\b(beach|point|playa)\b", name):
        return "Beach"
    return "LocalBusiness"


# ─────────────────────────── building the data ───────────────────────────

def prepare(reviews, photos):
    used = set()
    out = []
    for r in reviews:
        place = parse_place(r.get("address", ""))
        base = slugify(r["name"])
        city_slug = slugify(place["city"] or place["region"] or "")
        if city_slug and not base.endswith(city_slug):
            base = f"{base}-{city_slug}"
        if place["country"] == "KY" and not base.endswith("grand-cayman"):
            base = f"{base}-grand-cayman"
        slug, n = base, 2
        while slug in used:
            slug = f"{base}-{n}"
            n += 1
        used.add(slug)
        when = parse_date(r.get("date", ""))
        rating = int(r.get("rating") or 0)
        out.append({
            **r,
            "rating": rating,
            "slug": slug,
            "url": f"/reviews/{slug}.html",
            "place": place,
            "when": when,
            "iso": when.strftime("%Y-%m-%d") if when else "",
            "label": SCORE_LABELS.get(rating, "Unrated"),
            "type": schema_type(r),
            "photo": photos.get(r["name"]),
        })
    # Same place reviewed more than once: add the year so each page has its own title
    groups = defaultdict(list)
    for r in out:
        groups[(r["name"], short_place(r["place"]))].append(r)
    for group in groups.values():
        streets = {r["place"]["street"] for r in group}
        years = [r["when"].year for r in group if r["when"]]
        for r in group:
            if len(group) == 1:
                r["year_tag"] = ""
            elif len(set(years)) == len(group):
                r["year_tag"] = f" ({r['when'].year})"
            elif len(streets) == len(group) and r["place"]["street"]:
                r["year_tag"] = f" ({r['place']['street']})"
            else:
                r["year_tag"] = f" ({r['place']['street'] or ''} {r['when'].year if r['when'] else ''})".replace("( ", "(").replace(" )", ")")
    for r in out:
        r["hood"] = neighbourhood_for(r)
        r["tags"] = cuisine_tags(r)
        r["thumb"] = make_thumb(r["photo"]) if r["photo"] else None
    return out


def location_line(p):
    bits = [p["street"], p["city"]]
    if p["country"] == "CA":
        bits.append(p["region"])
    elif p["country"] == "KY":
        bits.append("Grand Cayman")
    elif p["region"]:
        bits.append(p["region"])
    if p["country_name"] and p["country"] != "CA":
        bits.append(p["country_name"])
    out = []
    for b in bits:
        if b and (not out or out[-1] != b):
            out.append(b)
    return ", ".join(out)


def short_place(p):
    if p["country"] == "KY":
        return f"{p['city']}, Grand Cayman" if p["city"] and p["city"] != "Grand Cayman" else "Grand Cayman"
    if p["country"] == "CA":
        return f"{p['city']}, {p['region']}"
    return ", ".join(b for b in [p["city"], p["country_name"]] if b) or "Online"


def snippet(text, limit=155):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:")
    return cut + "…"


def sort_best(items):
    return sorted(items, key=lambda x: (-x["rating"], -(x["when"].timestamp() if x["when"] else 0)))


def sort_newest(items):
    return sorted(items, key=lambda x: -(x["when"].timestamp() if x["when"] else 0))


# ─────────────────────────── page parts ───────────────────────────

E = html.escape

HUB_NAV = [
    ("/toronto/restaurants/", "Restaurants"),
    ("/toronto/nightlife/", "Bars & Nightlife"),
    ("/toronto/", "Toronto"),
    ("/grand-cayman/", "Grand Cayman"),
    ("/canada/", "Canada"),
    ("/world/", "Travel"),
]


def page(title, description, canonical, body, schemas, og_image=None, geo=("CA-ON", "Toronto")):
    og_image = og_image or f"{SITE}/share-card.png"
    geo_region, geo_place = geo
    schema_tags = "\n".join(
        '<script type="application/ld+json">\n' + json.dumps(s, ensure_ascii=False, indent=1)
        .replace("</", "<\\/") + "\n</script>" for s in schemas)
    nav = "\n".join(f'        <a href="{u}">{E(t)}</a>' for u, t in HUB_NAV)
    return f"""<!DOCTYPE html>
<html lang="en-CA">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" href="/favicon.ico" sizes="48x48">
    <link rel="icon" type="image/png" sizes="32x32" href="/icon-32.png">
    <link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
    <link rel="apple-touch-icon" href="/icon-apple-180.png">
    <meta name="theme-color" content="#faf7f2">
    <title>{E(title)}</title>
    <meta name="description" content="{E(description)}">
    <link rel="canonical" href="{SITE}{canonical}">
    <meta property="og:title" content="{E(title)}">
    <meta property="og:description" content="{E(description)}">
    <meta property="og:url" content="{SITE}{canonical}">
    <meta property="og:image" content="{E(og_image)}">
    <meta property="og:type" content="article">
    <meta property="og:locale" content="en_CA">
    <meta property="og:site_name" content="MsJAG Reviews">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{E(title)}">
    <meta name="twitter:description" content="{E(description)}">
    <meta name="twitter:image" content="{E(og_image)}">
    <meta name="geo.region" content="{E(geo_region)}">
    <meta name="geo.placename" content="{E(geo_place)}">
    <meta name="robots" content="index, follow, max-image-preview:large">
{schema_tags}
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,700;0,9..144,800;1,9..144,500;1,9..144,700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="/site.css">
</head>
<body>
    <div class="topbar"><span>Level 7 Google Local Guide</span><span>Independent · Self-funded</span><span>No sponsored reviews</span></div>
    <header class="site-header">
        <a href="/" class="logo" aria-label="Toronto Eats and Reviews home"><img class="logo-mark" src="/icon-512.png" width="112" height="112" alt="Toronto Eats and Reviews logo"><span class="logo-text">Toronto Eats and Reviews<span class="dot">.</span></span></a>
        <div class="tagline">Restaurant, bar &amp; venue reviews by MsJAG · Toronto · Grand Cayman · Canada</div>
    </header>
    <nav class="site-nav" aria-label="Primary">
        <a href="/">Home</a>
{nav}
        <a href="/archive.html">More Reviews</a>
    </nav>
    <main>
{body}
    </main>
    <footer class="colophon">
        <div class="col-inner">
            <div class="brand"><img class="logo-mark" src="/icon-192.png" width="56" height="56" alt="" loading="lazy"><span>Toronto Eats and Reviews<span class="dot">.</span></span></div>
            <div class="col-meta">
                &copy; <span id="copyrightYear"></span> Toronto Eats and Reviews · by MsJAG · Independent · Unsponsored<br>
                <a href="/toronto/">Toronto</a> · <a href="/grand-cayman/">Grand Cayman</a> · <a href="/canada/">Canada</a> · <a href="/archive.html">All reviews</a><br>
                <a href="{GOOGLE_PROFILE}" target="_blank" rel="noopener">Google Maps</a> ·
                <a href="https://instagram.com/chowhoundgta" target="_blank" rel="noopener">Instagram</a> ·
                <a href="/#contact">Contact</a>
            </div>
        </div>
    </footer>
    <script>document.getElementById('copyrightYear').textContent = new Date().getFullYear();</script>
</body>
</html>
"""


def crumbs_html(trail):
    links = []
    for name, url in trail:
        links.append(f'<a href="{url}">{E(name)}</a>' if url else f"<span>{E(name)}</span>")
    return '        <nav class="crumbs" aria-label="Breadcrumb">' + " / ".join(links) + "</nav>"


def crumbs_schema(trail, last_url):
    items = []
    for i, (name, url) in enumerate(trail, 1):
        items.append({"@type": "ListItem", "position": i, "name": name, "item": SITE + (url or last_url)})
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}


def stars(n):
    return "★" * n + "☆" * (5 - n)


def score_class(n):
    return "good" if n >= 4 else ("mixed" if n == 3 else "low")


def review_card(r):
    when = r["when"].strftime("%b %d, %Y") if r["when"] else E(r.get("date", ""))
    photo = ""
    if r["thumb"]:
        u, w, h = r["thumb"]
        photo = (f'<a class="rph" href="{r["url"]}" tabindex="-1" aria-hidden="true">'
                 f'<img src="{E(u)}" width="{w}" height="{h}" alt="" loading="lazy" decoding="async"></a>')
    elif r["photo"]:
        photo = (f'<a class="rph" href="{r["url"]}" tabindex="-1" aria-hidden="true">'
                 f'<img src="{E(r["photo"])}" alt="" loading="lazy"></a>')
    return f"""            <article class="rcard s-{score_class(r['rating'])}">
                {photo}
                <div class="rmeta"><span class="pill">{E(CATEGORY_WORDS.get(r['category'], r['category']))}</span><span>{when}</span><span>{E(short_place(r['place']))}</span></div>
                <h3><a href="{r['url']}">{E(r['name'])}</a></h3>
                <span class="badge {'' if r['rating'] >= 4 else ('mixed' if r['rating'] == 3 else 'low')}" aria-label="{r['rating']} out of 5 stars">{r['rating']}/5 · {E(r['label'])}</span>
                <p>{E(snippet(r['comment'], 180))}</p>
                <a class="more" href="{r['url']}">Read the full review →</a>
            </article>"""


def hub_for(r):
    return r["place"]["hub"]


HUB_INFO = {
    "toronto": ("Toronto", "/toronto/"),
    "grand-cayman": ("Grand Cayman", "/grand-cayman/"),
    "mississauga": ("Mississauga", "/mississauga/"),
    "canada": ("Canada", "/canada/"),
    "world": ("World", "/world/"),
}


# ─────────────────────────── review pages ───────────────────────────

def build_review_page(r, all_reviews):
    p = r["place"]
    where_short = short_place(p)
    title = f"{r['name']} Review, {where_short}{r['year_tag']} | MsJAG"
    if len(title) > 70:
        title = f"{r['name']} Review{r['year_tag']} | MsJAG"
    desc = f"{r['label']} ({r['rating']}/5): {snippet(r['comment'], 130)}"
    paragraphs = [para.strip() for para in r["comment"].split("\n\n") if para.strip()]
    body_text = "\n".join(f"            <p>{E(para).replace(chr(10), '<br>')}</p>" for para in paragraphs)
    maps_url = "https://www.google.com/maps/search/?api=1&query=" + quote_plus(f"{r['name']} {r.get('address', '')}".strip())

    hub = hub_for(r)
    trail = [("Home", "/")]
    if hub in ("toronto", "mississauga", "canada"):
        trail.append(("Canada", "/canada/"))
    if hub and hub != "canada":
        trail.append(HUB_INFO[hub])
    if hub == "toronto" and r["category"] in TORONTO_TOPICS:
        trail.append((TORONTO_TOPICS[r["category"]][0], f"/toronto/{TORONTO_TOPICS[r['category']][1]}/"))
    trail.append((r["name"], None))

    # Related reviews: same neighbourhood first, then same city and category, best first
    hood = r["hood"]
    pool = [x for x in all_reviews if x is not r and (hub_for(x) == hub if hub else True)]
    nearby = sort_best([x for x in pool if hood and x["hood"] and x["hood"][1] == hood[1]])
    rest = [x for x in pool if x not in nearby]
    same = nearby[:4] + sort_best([x for x in rest if x["category"] == r["category"]])[:4] + \
        sort_best([x for x in rest if x["category"] != r["category"]])[:2]
    related = "\n".join(review_card(x) for x in same[:6])
    related_heading = f"More near {hood[0]}" if nearby else (f"More reviews in {HUB_INFO[hub][0]}" if hub else "More reviews from MsJAG")
    hood_line = ""
    if hood:
        hood_count = sum(1 for x in all_reviews if x["hood"] and x["hood"][1] == hood[1])
        if hood_count >= MIN_PER_PAGE:
            hood_line = f' · <a href="/toronto/neighbourhoods/{hood[1]}/">{E(hood[0])}</a>'
        else:
            hood_line = f" · {E(hood[0])}"
    tag_line = ""
    live_tags = [(l, sl) for l, sl in r["tags"] if sl in LIVE_CUISINE_SLUGS]
    if live_tags:
        tag_line = '            <div class="chips small">' + "".join(
            f'<a href="/toronto/{slug}/">{E(label)}</a>' for label, slug in live_tags) + "</div>\n"

    photo_html = ""
    if r["photo"]:
        photo_html = f"""        <figure class="hero-photo"><img src="{E(r['photo'])}" alt="{E(r['name'])}, {E(where_short)}, photo by MsJAG" width="1200" height="800" loading="eager"></figure>\n"""

    when_text = r["when"].strftime("%B %d, %Y").replace(" 0", " ") if r["when"] else E(r.get("date", ""))
    address_line = location_line(p)
    body = f"""{crumbs_html(trail)}
        <article class="review">
            <div class="rmeta"><span class="pill">{E(CATEGORY_WORDS.get(r['category'], r['category']))}</span><span>Reviewed {when_text}</span></div>
            <h1>{E(r['name'])} <span class="h1-sub">Review</span></h1>
            <p class="where">{E(address_line) if address_line else 'Online business'}{hood_line}</p>
            <div class="verdict s-{score_class(r['rating'])}">
                <span class="stars" aria-hidden="true">{stars(r['rating'])}</span>
                <span class="num">{r['rating']}/5</span>
                <span class="label">{E(r['label'])}</span>
            </div>
{photo_html}            <div class="text">
{body_text}
            </div>
{tag_line}            <p class="byline">Reviewed by <a href="/">MsJAG</a>, Level 7 Google Local Guide. Self-funded visit, no sponsorship.</p>
            <div class="actions">
                {f'<a class="btn" href="{maps_url}" target="_blank" rel="noopener">Get directions</a>' if address_line else ''}
                <a class="btn ghost" href="/archive.html">All {len(all_reviews)} reviews</a>
            </div>
        </article>
        <section class="related">
            <h2>{E(related_heading)}</h2>
            <div class="grid">
{related}
            </div>
        </section>"""

    item = {"@type": r["type"], "name": r["name"]}
    if address_line:
        addr = {"@type": "PostalAddress"}
        if p["street"]:
            addr["streetAddress"] = p["street"]
        if p["city"]:
            addr["addressLocality"] = p["city"]
        if p["region"]:
            addr["addressRegion"] = p["region"]
        if p["postal"]:
            addr["postalCode"] = p["postal"]
        if p["country"]:
            addr["addressCountry"] = p["country"]
        item["address"] = addr
    if r["photo"]:
        item["image"] = r["photo"]
    if address_line:
        item["hasMap"] = maps_url
    review_schema = {
        "@context": "https://schema.org",
        "@type": "Review",
        "@id": f"{SITE}{r['url']}#review",
        "url": SITE + r["url"],
        "name": f"{r['name']} review",
        "itemReviewed": item,
        "reviewRating": {"@type": "Rating", "ratingValue": r["rating"], "bestRating": 5, "worstRating": 1},
        "author": {"@type": "Person", "@id": f"{SITE}/#person", "name": "MsJAG", "url": f"{SITE}/"},
        "publisher": {"@type": "Organization", "@id": f"{SITE}/#organization", "name": "MsJAG Reviews", "url": f"{SITE}/"},
        "reviewBody": r["comment"],
        "inLanguage": "en-CA",
        "mainEntityOfPage": {"@type": "WebPage", "@id": SITE + r["url"]},
        "isPartOf": {"@id": f"{SITE}/#website"},
    }
    if r["iso"]:
        review_schema["datePublished"] = r["iso"]
    if hub in ("toronto", "mississauga"):
        geo = ("CA-ON", "Toronto")
    elif hub == "grand-cayman":
        geo = ("KY", "Grand Cayman")
    elif p["country"] == "CA":
        geo = ("CA", p["city"] or "Canada")
    else:
        geo = (p["country"] or "", p["city"] or p["country_name"] or "")
    html_out = page(title, desc, r["url"], body,
                    [review_schema, crumbs_schema(trail, r["url"])], og_image=r["photo"], geo=geo)
    return html_out


# ─────────────────────────── hub pages ───────────────────────────

TORONTO_TOPICS = {
    "Dining": ("Restaurants", "restaurants"),
    "Nightlife": ("Bars & Nightlife", "nightlife"),
    "Service": ("Services & Shops", "services"),
}

CATEGORY_ORDER = ["Dining", "Nightlife", "Travel", "Service"]
SECTION_TITLES = {"Dining": "Restaurants & food", "Nightlife": "Bars & nightlife",
                  "Travel": "Hotels, beaches & things to do", "Service": "Services & shops"}


def item_list_schema(name, url, items):
    return {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": name,
        "url": SITE + url,
        "isPartOf": {"@id": f"{SITE}/#website"},
        "author": {"@type": "Person", "@id": f"{SITE}/#person", "name": "MsJAG"},
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": len(items),
            "itemListElement": [
                {"@type": "ListItem", "position": i, "url": SITE + x["url"], "name": x["name"]}
                for i, x in enumerate(items, 1)
            ],
        },
    }


def stats_line(items):
    picks = sum(1 for x in items if x["rating"] == 5)
    rec = sum(1 for x in items if x["rating"] == 4)
    avoid = sum(1 for x in items if x["rating"] <= 2)
    return (f'        <div class="stats"><span><b>{len(items)}</b> reviews</span>'
            f'<span><b>{picks}</b> Editor\'s Picks</span><span><b>{rec}</b> Recommended</span>'
            f'<span><b>{avoid}</b> to skip</span></div>')


def sections_by_category(items, link_more=None):
    out = []
    for cat in CATEGORY_ORDER:
        group = sort_best([x for x in items if x["category"] == cat])
        if not group:
            continue
        more = ""
        if link_more and cat in link_more:
            more = f'\n            <p class="seeall"><a href="{link_more[cat]}">See all {len(group)} {SECTION_TITLES[cat].lower()} reviews →</a></p>'
            group = group[:9]
        cards = "\n".join(review_card(x) for x in group)
        out.append(f"""        <section class="group">
            <h2>{SECTION_TITLES[cat]} <span class="count">{len([x for x in items if x['category'] == cat])}</span></h2>
            <div class="grid">
{cards}
            </div>{more}
        </section>""")
    return "\n".join(out)


def picks_section(items, heading):
    picks = sort_newest([x for x in items if x["rating"] == 5])[:6]
    if not picks:
        return ""
    cards = "\n".join(review_card(x) for x in picks)
    return f"""        <section class="group picks">
            <h2>{E(heading)}</h2>
            <div class="grid">
{cards}
            </div>
        </section>"""


LIVE_CUISINE_SLUGS = set()  # filled in main() once we know which cuisine pages exist


def faq_html_and_schema(faqs):
    """faqs: list of (question, answer_html, answer_text)."""
    if not faqs:
        return "", None
    items = "\n".join(f"""            <details class="faq">
                <summary>{E(q)}</summary>
                <div class="faq-a">{a_html}</div>
            </details>""" for q, a_html, _ in faqs)
    html_block = f"""        <section class="sec faqs" aria-labelledby="faq-h">
            <div class="sec-head"><div><h2 id="faq-h">Good to <em>know.</em></h2></div></div>
{items}
        </section>"""
    schema = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a_text}} for q, _, a_text in faqs]}
    return html_block, schema


def build_hub(slug, title, h1, intro, items, trail, extra="", link_more=None, picks_heading=None,
              extra_end="", faqs=None, description=None, ranked=False):
    url = f"/{slug}/"
    items_sorted = sort_best(items)
    faq_block, faq_schema = faq_html_and_schema(faqs or [])
    if ranked:
        cards = "\n".join(review_card(x) for x in items_sorted)
        listing = f"""        <section class="group">
            <div class="grid">
{cards}
            </div>
        </section>"""
    else:
        listing = sections_by_category(items, link_more)
    body = f"""{crumbs_html(trail)}
        <section class="hub-head">
            <h1>{h1}</h1>
            <p class="intro">{intro}</p>
{stats_line(items)}
        </section>
{extra}
{picks_section(items, picks_heading) if picks_heading else ''}
{listing}
{extra_end}
{faq_block}"""
    desc = description or snippet(re.sub(r"<[^>]+>", "", intro), 158)
    schemas = [item_list_schema(title, url, items_sorted), crumbs_schema(trail, url)]
    if faq_schema:
        schemas.append(faq_schema)
    # Share image: newest photo from a 4 or 5 star review, otherwise any photo
    share_photo = share_photo_for(items)
    return url, page(title, desc, url, body, schemas, og_image=share_photo)


def share_photo_for(items):
    return next((x["photo"] for x in sort_newest(items) if x["photo"] and x["rating"] >= 4), None) \
        or next((x["photo"] for x in sort_newest(items) if x["photo"]), None)


def write(path, text):
    full = os.path.join(ROOT, path.lstrip("/"))
    if full.endswith("/"):
        full += "index.html"
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)


def newest_iso(items):
    dates = [x["when"] for x in items if x["when"]]
    return max(dates).strftime("%Y-%m-%d") if dates else datetime.now().strftime("%Y-%m-%d")


# ─────────────────────────── sitemap ───────────────────────────

def build_sitemap(entries):
    """Keeps hand-made pages already in sitemap.xml (with their dates) and adds every generated page."""
    ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    keep = []
    path = os.path.join(ROOT, "sitemap.xml")
    generated_prefixes = tuple(f"{SITE}/{d}/" for d in GENERATED_DIRS)
    if os.path.exists(path):
        tree = ET.parse(path)
        for u in tree.getroot().findall(f"{{{ns}}}url"):
            loc = u.findtext(f"{{{ns}}}loc", "").strip()
            if loc.startswith(generated_prefixes):
                continue
            keep.append((loc, u.findtext(f"{{{ns}}}lastmod", "").strip(),
                         u.findtext(f"{{{ns}}}changefreq", "").strip(),
                         u.findtext(f"{{{ns}}}priority", "").strip()))
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<urlset xmlns="{ns}">']
    for loc, lastmod, freq, prio in keep + entries:
        lines.append("  <url>")
        lines.append(f"    <loc>{E(loc)}</loc>")
        if lastmod:
            lines.append(f"    <lastmod>{lastmod}</lastmod>")
        if freq:
            lines.append(f"    <changefreq>{freq}</changefreq>")
        if prio:
            lines.append(f"    <priority>{prio}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


# ─────────────────────────── main ───────────────────────────

def main():
    photos = load_photo_map()
    reviews = prepare(load_reviews(), photos)

    # Remember photo links for next time
    known = {r["name"]: r["photo"] for r in reviews if r["photo"]}
    json.dump(dict(sorted(known.items())), open(PHOTO_MAP_FILE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    for d in GENERATED_DIRS:
        shutil.rmtree(os.path.join(ROOT, d), ignore_errors=True)

    sitemap = []

    # Which Toronto cuisine pages will exist (needs enough reviews each)
    tor_all = [r for r in reviews if r["place"]["hub"] == "toronto"]
    cuisine_groups = {}
    for label, slug, _ in CUISINES:
        group = [r for r in tor_all if (label, slug) in r["tags"]]
        if len(group) >= MIN_PER_PAGE:
            cuisine_groups[slug] = (label, group)
    LIVE_CUISINE_SLUGS.clear()
    LIVE_CUISINE_SLUGS.update(cuisine_groups)

    # Link list the archive page uses to point each card at its own page
    links = {f"{r['name']}|{r.get('date', '')}|{r.get('address', '')}": r["url"] for r in reviews}
    json.dump(links, open(os.path.join(ROOT, "review-links.json"), "w", encoding="utf-8"), ensure_ascii=False)

    for r in reviews:
        write(r["url"], build_review_page(r, reviews))
        sitemap.append((SITE + r["url"], r["iso"], "yearly", "0.6"))

    by_hub = defaultdict(list)
    for r in reviews:
        if r["place"]["hub"]:
            by_hub[r["place"]["hub"]].append(r)
    canada_all = [r for r in reviews if r["place"]["country"] == "CA"]

    # Toronto
    tor = by_hub["toronto"]
    tor_trail = [("Home", "/"), ("Canada", "/canada/"), ("Toronto", "/toronto/")]

    # Neighbourhoods with enough reviews to deserve a page
    hoods = {}
    for r in tor:
        if r["hood"]:
            hoods.setdefault(r["hood"][1], {"name": r["hood"][0], "blurb": r["hood"][2], "items": []})["items"].append(r)
    hoods = {k: v for k, v in hoods.items() if len(v["items"]) >= MIN_PER_PAGE}
    hoods_sorted = sorted(hoods.items(), key=lambda kv: -len(kv[1]["items"]))

    def chips(pairs, heading=None, cls=""):
        inner = "".join(f'<a href="{u}">{E(t)}</a>' for u, t in pairs)
        head = f'        <h2 class="chips-h">{heading}</h2>\n' if heading else ""
        return f'{head}        <div class="chips {cls}">{inner}</div>'

    hood_pairs = [(f"/toronto/neighbourhoods/{k}/", f"{v['name']} ({len(v['items'])})") for k, v in hoods_sorted]
    cuisine_pairs = [(f"/toronto/{slug}/", f"{label} ({len(group)})")
                     for slug, (label, group) in sorted(cuisine_groups.items(), key=lambda kv: -len(kv[1][1]))]
    topic_pairs = [(f"/toronto/{s}/", n) for _, (n, s) in TORONTO_TOPICS.items()] + [("/toronto/best-restaurants/", "Best restaurants")]
    topic_links = {c: f"/toronto/{s}/" for c, (_, s) in TORONTO_TOPICS.items()}

    tor_extra = "\n".join([
        chips(topic_pairs),
        chips(hood_pairs, "Restaurants near you: browse Toronto by <em>neighbourhood.</em>"),
        chips(cuisine_pairs, "Browse by <em>cuisine.</em>"),
    ])

    picks5 = sort_newest([r for r in tor if r["rating"] == 5 and r["category"] == "Dining"])[:3]
    top_hood = hoods_sorted[0][1] if hoods_sorted else None
    tor_faqs = [
        ("How do I find a good restaurant near me in Toronto?",
         "Start with the neighbourhood list above. Each neighbourhood page shows every place reviewed there, with Editor's Picks first, a street address and a Get directions link to Google Maps. "
         + (f'The most-reviewed area is <a href="/toronto/neighbourhoods/{hoods_sorted[0][0]}/">{E(top_hood["name"])}</a> with {len(top_hood["items"])} reviews.' if top_hood else ""),
         "Start with the neighbourhood list on this page. Each neighbourhood page shows every place reviewed there, with Editor's Picks first, a street address and a Get directions link to Google Maps."
         + (f" The most-reviewed area is {top_hood['name']} with {len(top_hood['items'])} reviews." if top_hood else "")),
        ("What are the newest Editor's Pick restaurants in Toronto?",
         "The latest 5-star restaurant reviews are " + ", ".join(f'<a href="{r["url"]}">{E(r["name"])}</a>' for r in picks5) + '. See the full ranked list on the <a href="/toronto/best-restaurants/">best restaurants in Toronto</a> page.',
         "The latest 5-star restaurant reviews are " + ", ".join(r["name"] for r in picks5) + ". The full ranked list is on the best restaurants in Toronto page."),
        ("How are places rated?",
         "Every review gets a score out of 5: <b>5 is an Editor's Pick</b>, 4 is Recommended, 3 is Mixed, 2 is Not Recommended and 1 is Avoid. The score covers service, food or product quality, consistency and value.",
         "Every review gets a score out of 5: 5 is an Editor's Pick, 4 is Recommended, 3 is Mixed, 2 is Not Recommended and 1 is Avoid. The score covers service, food or product quality, consistency and value."),
        ("Are any of these Toronto reviews sponsored?",
         "No. Every visit is paid for by MsJAG. There are no comped meals, press trips or affiliate links, and businesses can't pay to change a score.",
         "No. Every visit is paid for by MsJAG. There are no comped meals, press trips or affiliate links, and businesses can't pay to change a score."),
        ("Who writes these reviews?",
         'MsJAG (JoAnne Gomez), a Level 7 Google Local Guide based in Toronto whose venue photos have more than 9 million views on Google Maps. <a href="/#about">Read more about the reviewer</a>.',
         "MsJAG (JoAnne Gomez), a Level 7 Google Local Guide based in Toronto whose venue photos have more than 9 million views on Google Maps."),
    ]
    url, out = build_hub(
        "toronto", "Toronto Restaurant & Bar Reviews by Neighbourhood | MsJAG",
        "Toronto <em>reviews.</em>",
        f"Honest, unsponsored reviews of {len(tor)} Toronto restaurants, bars, nightclubs, hotels, clinics and shops, "
        f"sorted by neighbourhood so you can find somewhere good near you, from King West and Queen West to "
        "North York and Etobicoke. Every visit is self-funded and written by MsJAG, a Level 7 Google Local Guide.",
        tor, [("Home", "/"), ("Canada", "/canada/"), ("Toronto", None)],
        extra=tor_extra, link_more=topic_links, picks_heading="Latest Toronto Editor's Picks", faqs=tor_faqs,
        description=f"{len(tor)} honest Toronto restaurant, bar and venue reviews sorted by neighbourhood, from King West to North York. Find somewhere good near you. By MsJAG, Level 7 Google Local Guide.")
    write(url, out)
    sitemap.append((SITE + url, newest_iso(tor), "weekly", "0.9"))

    # Neighbourhood index and one page per neighbourhood
    hood_cards = "\n".join(f"""            <a class="hood-card" href="/toronto/neighbourhoods/{k}/">
                <b>{E(v['name'])}</b><span>{len(v['items'])} reviews · {sum(1 for x in v['items'] if x['rating'] == 5)} Editor's Picks</span>
                <small>{E(v['blurb'])}</small>
            </a>""" for k, v in hoods_sorted)
    url = "/toronto/neighbourhoods/"
    body = f"""{crumbs_html(tor_trail + [("Neighbourhoods", None)])}
        <section class="hub-head">
            <h1>Toronto by <em>neighbourhood.</em></h1>
            <p class="intro">Looking for a restaurant, bar or shop near you? Pick your part of Toronto. {len(hoods)} neighbourhoods, {sum(len(v['items']) for v in hoods.values())} honest reviews, every one visited and paid for by MsJAG.</p>
        </section>
        <div class="hood-grid">
{hood_cards}
        </div>"""
    write(url, page("Toronto Restaurants & Bars Near You, by Neighbourhood | MsJAG",
                    f"Find reviewed restaurants, bars and shops near you in {len(hoods)} Toronto neighbourhoods, from King West and Liberty Village to Leslieville and North York.",
                    url, body, [crumbs_schema(tor_trail + [("Neighbourhoods", None)], url),
                                {"@context": "https://schema.org", "@type": "CollectionPage", "name": "Toronto neighbourhoods", "url": SITE + url,
                                 "isPartOf": {"@id": f"{SITE}/#website"},
                                 "hasPart": [{"@type": "WebPage", "name": v["name"], "url": f"{SITE}/toronto/neighbourhoods/{k}/"} for k, v in hoods_sorted]}],
                    og_image=share_photo_for(tor)))
    sitemap.append((SITE + url, newest_iso(tor), "weekly", "0.8"))

    for k, v in hoods_sorted:
        items = v["items"]
        dining = sum(1 for x in items if x["category"] == "Dining")
        night = sum(1 for x in items if x["category"] == "Nightlife")
        other_hoods = [(f"/toronto/neighbourhoods/{k2}/", v2["name"]) for k2, v2 in hoods_sorted if k2 != k][:8]
        url, out = build_hub(
            f"toronto/neighbourhoods/{k}",
            f"{v['name']} Restaurants & Bars: {len(items)} Honest Reviews | MsJAG"[:70] if len(f"{v['name']} Restaurants & Bars: {len(items)} Honest Reviews | MsJAG") <= 70 else f"{v['name']}: {len(items)} Honest Reviews | MsJAG",
            f"{E(v['name'])} <em>reviews.</em>",
            f"{len(items)} honest reviews of restaurants, bars and local businesses in {v['name']} ({v['blurb'].rstrip('.')}), "
            f"with {dining} restaurant and {night} nightlife reviews. Editor's Picks first, then everything else, each with an address and directions. "
            "Every visit is self-funded by MsJAG, a Level 7 Google Local Guide.",
            items, tor_trail + [("Neighbourhoods", "/toronto/neighbourhoods/"), (v["name"], None)],
            picks_heading=f"Editor's Picks in {v['name']}" if any(x["rating"] == 5 for x in items) else None,
            extra_end=chips(other_hoods, "Nearby <em>neighbourhoods.</em>"),
            description=snippet(f"Restaurants, bars and shops near you in {v['name']}, Toronto: {len(items)} honest reviews with addresses and directions, Editor's Picks first. By MsJAG, Level 7 Google Local Guide.", 158))
        write(url, out)
        sitemap.append((SITE + url, newest_iso(items), "weekly", "0.8"))

    # One page per cuisine or feature
    for slug, (label, group) in cuisine_groups.items():
        others = [(f"/toronto/{s2}/", l2) for s2, (l2, _) in cuisine_groups.items() if s2 != slug][:10]
        url, out = build_hub(
            f"toronto/{slug}",
            f"Best {label} in Toronto: {len(group)} Honest Reviews | MsJAG",
            f"Toronto <em>{E(label.lower())}.</em>",
            f"{len(group)} honest Toronto reviews for {label.lower()}, ranked with Editor's Picks first so the best {label.lower()} in Toronto is at the top. "
            "Every place was visited and paid for by MsJAG, a Level 7 Google Local Guide. Each review has the address and directions.",
            group, tor_trail + [(label, None)], ranked=True,
            extra_end=chips(others, "More Toronto <em>cuisines.</em>"),
            description=snippet(f"Best {label.lower()} in Toronto: {len(group)} honest reviews ranked with Editor's Picks first, with addresses and directions. Unsponsored, by MsJAG, Level 7 Google Local Guide.", 158))
        write(url, out)
        sitemap.append((SITE + url, newest_iso(group), "weekly", "0.7"))

    # Best restaurants in Toronto: every 5-star restaurant, newest first
    best = sort_newest([r for r in tor if r["category"] == "Dining" and r["rating"] == 5])
    best_hoods = sorted({r["hood"][0] for r in best if r["hood"]})
    url, out = build_hub(
        "toronto/best-restaurants",
        f"Best Restaurants in Toronto: {len(best)} Editor's Picks | MsJAG",
        "Best restaurants in <em>Toronto.</em>",
        f"Every Toronto restaurant that earned a 5 out of 5 Editor's Pick, newest first. {len(best)} places across "
        f"{len(best_hoods)} neighbourhoods, each visited and paid for by MsJAG, a Level 7 Google Local Guide, with no sponsored reviews.",
        best, tor_trail + [("Best restaurants", None)], ranked=True,
        extra_end=chips(hood_pairs, "Find the best restaurants near you by <em>neighbourhood.</em>"),
        faqs=[("What makes a restaurant an Editor's Pick?",
               "A 5 out of 5 score. The food, service, consistency and value all held up on a self-funded visit. A 4 is Recommended, a 3 is Mixed, and 2 or 1 means skip it.",
               "A 5 out of 5 score. The food, service, consistency and value all held up on a self-funded visit. A 4 is Recommended, a 3 is Mixed, and 2 or 1 means skip it."),
              ("How often is this list updated?",
               "Every time a new review is published. The newest Editor's Picks appear at the top.",
               "Every time a new review is published. The newest Editor's Picks appear at the top.")],
        description=f"The best restaurants in Toronto according to {len(best)} unsponsored 5-star reviews by MsJAG, Level 7 Google Local Guide. Newest picks first, with addresses and directions.")
    write(url, out)
    sitemap.append((SITE + url, newest_iso(best), "weekly", "0.9"))

    # Homepage: refresh the neighbourhood and cuisine links between the marker comments
    idx_path = os.path.join(ROOT, "index.html")
    idx = open(idx_path, encoding="utf-8").read()
    start_mark, end_mark = "<!-- NEIGHBOURHOODS:START -->", "<!-- NEIGHBOURHOODS:END -->"
    if start_mark in idx and end_mark in idx:
        block = ("\n            <h3 class=\"chips-h\">Restaurants near you: browse Toronto by neighbourhood</h3>\n"
                 "            <div class=\"chips\">" + "".join(f'<a href="{u}">{E(t)}</a>' for u, t in hood_pairs[:12])
                 + '<a href="/toronto/neighbourhoods/">All neighbourhoods →</a></div>\n'
                 "            <h3 class=\"chips-h\">Browse Toronto by cuisine</h3>\n"
                 "            <div class=\"chips\">" + "".join(f'<a href="{u}">{E(t)}</a>' for u, t in cuisine_pairs)
                 + '<a href="/toronto/best-restaurants/">Best restaurants in Toronto →</a></div>\n            ')
        a = idx.index(start_mark) + len(start_mark)
        b_ = idx.index(end_mark)
        new_idx = idx[:a] + block + idx[b_:]
        if new_idx != idx:
            open(idx_path, "w", encoding="utf-8").write(new_idx)

    for cat, (name, s) in TORONTO_TOPICS.items():
        group = [r for r in tor if r["category"] == cat]
        if not group:
            continue
        nice = {"Dining": "Toronto restaurant", "Nightlife": "Toronto bar and nightclub",
                "Service": "Toronto service, clinic and shop"}[cat]
        url, out = build_hub(
            f"toronto/{s}", f"Best {name} in Toronto: {len(group)} Honest Reviews | MsJAG",
            f"Toronto <em>{E(name.lower())}.</em>",
            f"{len(group)} {nice} reviews, ranked with Editor's Picks first. Every place was visited and paid "
            "for by MsJAG, with no sponsored posts and no free meals.",
            group, [("Home", "/"), ("Canada", "/canada/"), ("Toronto", "/toronto/"), (name, None)])
        write(url, out)
        sitemap.append((SITE + url, newest_iso(group), "weekly", "0.8"))

    # Grand Cayman
    gc = by_hub["grand-cayman"]
    url, out = build_hub(
        "grand-cayman", "Grand Cayman Restaurant, Bar & Beach Reviews | MsJAG",
        "Grand Cayman <em>reviews.</em>",
        f"{len(gc)} honest reviews of Grand Cayman restaurants, beach bars, beaches, nightlife and local services "
        "in George Town, Seven Mile Beach and Rum Point. Written by MsJAG, a Level 7 Google Local Guide who "
        "visits the island often.",
        gc, [("Home", "/"), ("Grand Cayman", None)], picks_heading="Grand Cayman Editor's Picks")
    write(url, out)
    sitemap.append((SITE + url, newest_iso(gc), "weekly", "0.9"))

    # Mississauga
    mi = by_hub["mississauga"]
    url, out = build_hub(
        "mississauga", "Mississauga Restaurant & Venue Reviews | MsJAG",
        "Mississauga <em>reviews.</em>",
        f"{len(mi)} honest reviews of Mississauga restaurants, bars and local businesses by MsJAG, "
        "a Level 7 Google Local Guide from Toronto.",
        mi, [("Home", "/"), ("Canada", "/canada/"), ("Mississauga", None)])
    write(url, out)
    sitemap.append((SITE + url, newest_iso(mi), "monthly", "0.7"))

    # Canada: city links, then every review outside Toronto and Mississauga grouped by city
    other = [r for r in canada_all if r["place"]["hub"] == "canada"]
    by_city = defaultdict(list)
    for r in other:
        by_city[f"{r['place']['city']}, {r['place']['region']}"].append(r)
    city_chips = (f'        <div class="chips"><a href="/toronto/">Toronto ({len(tor)})</a>'
                  f'<a href="/mississauga/">Mississauga ({len(mi)})</a>' +
                  "".join(f'<a href="#{slugify(c)}">{E(c)} ({len(v)})</a>'
                          for c, v in sorted(by_city.items(), key=lambda kv: -len(kv[1]))) + "</div>")
    groups = []
    groups.append(picks_section(canada_all, "Latest Editor's Picks across Canada"))
    for c, v in sorted(by_city.items(), key=lambda kv: -len(kv[1])):
        cards = "\n".join(review_card(x) for x in sort_best(v))
        groups.append(f"""        <section class="group" id="{slugify(c)}">
            <h2>{E(c)} <span class="count">{len(v)}</span></h2>
            <div class="grid">
{cards}
            </div>
        </section>""")
    trail = [("Home", "/"), ("Canada", None)]
    cities_named = ", ".join(c.split(",")[0] for c, _ in sorted(by_city.items(), key=lambda kv: -len(kv[1]))[:5])
    body = f"""{crumbs_html(trail)}
        <section class="hub-head">
            <h1>Canada <em>reviews.</em></h1>
            <p class="intro">{len(canada_all)} honest reviews of restaurants, bars, hotels and businesses across Canada, from Toronto and Mississauga to {E(cities_named)}. Every visit is self-funded and reviewed by MsJAG, a Level 7 Google Local Guide.</p>
{stats_line(canada_all)}
        </section>
{city_chips}
""" + "\n".join(groups)
    url = "/canada/"
    write(url, page("Canada Restaurant, Hotel & Business Reviews | MsJAG",
                    snippet(f"{len(canada_all)} honest reviews of restaurants, bars, hotels and businesses across Canada, "
                            f"from Toronto and Mississauga to {cities_named}, by Level 7 Google Local Guide MsJAG.", 158),
                    url, body, [item_list_schema("Canada reviews", url, sort_best(canada_all)), crumbs_schema(trail, url)], og_image=share_photo_for(canada_all)))
    sitemap.append((SITE + url, newest_iso(canada_all), "weekly", "0.8"))

    # World: everything outside Canada and Cayman, grouped by country
    wo = by_hub["world"]
    by_country = defaultdict(list)
    for r in wo:
        by_country[r["place"]["country_name"] or "Other"].append(r)
    groups = []
    for c, v in sorted(by_country.items(), key=lambda kv: -len(kv[1])):
        cards = "\n".join(review_card(x) for x in sort_best(v))
        groups.append(f"""        <section class="group" id="{slugify(c)}">
            <h2>{E(c)} <span class="count">{len(v)}</span></h2>
            <div class="grid">
{cards}
            </div>
        </section>""")
    chips = '        <div class="chips">' + "".join(
        f'<a href="#{slugify(c)}">{E(c)} ({len(v)})</a>'
        for c, v in sorted(by_country.items(), key=lambda kv: -len(kv[1]))) + "</div>"
    trail = [("Home", "/"), ("World", None)]
    countries = ", ".join(c for c, _ in sorted(by_country.items(), key=lambda kv: -len(kv[1]))[:5])
    body = f"""{crumbs_html(trail)}
        <section class="hub-head">
            <h1>Travel <em>reviews.</em></h1>
            <p class="intro">{len(wo)} honest hotel, resort, restaurant and nightlife reviews from trips to {E(countries)} and more, written by MsJAG, a Level 7 Google Local Guide from Toronto.</p>
{stats_line(wo)}
        </section>
{chips}
""" + "\n".join(groups)
    url = "/world/"
    write(url, page("Hotel, Resort & Restaurant Reviews Around the World | MsJAG",
                    snippet(f"{len(wo)} honest hotel, resort and restaurant reviews from {countries} and more, by Toronto's MsJAG.", 158),
                    url, body, [item_list_schema("Travel reviews", url, sort_best(wo)), crumbs_schema(trail, url)], og_image=share_photo_for(wo)))
    sitemap.append((SITE + url, newest_iso(wo), "monthly", "0.7"))

    build_sitemap(sitemap)
    print(f"Built {len(reviews)} review pages and {len(sitemap) - len(reviews)} hub pages.")


if __name__ == "__main__":
    main()
