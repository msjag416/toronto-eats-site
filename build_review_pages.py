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
    ("/toronto/", "Toronto"),
    ("/grand-cayman/", "Grand Cayman"),
    ("/mississauga/", "Mississauga"),
    ("/canada/", "Canada"),
    ("/world/", "World"),
]


def page(title, description, canonical, body, schemas, og_image=None):
    og_image = og_image or f"{SITE}/msjag-logo.PNG"
    schema_tags = "\n".join(
        '<script type="application/ld+json">\n' + json.dumps(s, ensure_ascii=False, indent=1)
        .replace("</", "<\\/") + "\n</script>" for s in schemas)
    nav = "\n".join(f'            <a href="{u}">{E(t)}</a>' for u, t in HUB_NAV)
    return f"""<!DOCTYPE html>
<html lang="en-CA">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>⭐</text></svg>">
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
{schema_tags}
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62.5..125,500..900&family=Archivo+Narrow:wght@500;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="/review-pages.css">
</head>
<body>
    <header class="masthead">
        <a href="/" class="wordmark" aria-label="MsJAG Reviews home">MsJAG<span class="dot">.</span></a>
        <nav class="subnav" aria-label="Primary">
            <a href="/">Home</a>
            <a href="/archive.html">The Vault</a>
{nav}
        </nav>
    </header>
    <main>
{body}
    </main>
    <footer class="colophon">
        <div class="col-inner">
            <div class="brand">MsJAG<span class="dot">.</span></div>
            <div class="col-meta">
                &copy; <span id="copyrightYear"></span> MsJAG Reviews · Independent · Unsponsored<br>
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
    return f"""            <article class="rcard s-{score_class(r['rating'])}">
                <div class="rmeta"><span class="pill">{E(CATEGORY_WORDS.get(r['category'], r['category']))}</span><span>{when}</span><span>{E(short_place(r['place']))}</span></div>
                <h3><a href="{r['url']}">{E(r['name'])}</a></h3>
                <div class="score"><span class="stars" aria-label="{r['rating']} out of 5 stars">{stars(r['rating'])}</span> <b>{E(r['label'])}</b></div>
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

    # Related reviews: same place first, same category first, best first
    same = [x for x in all_reviews if x is not r and (hub_for(x) == hub if hub else True)]
    same = sort_best([x for x in same if x["category"] == r["category"]])[:4] + \
        sort_best([x for x in same if x["category"] != r["category"]])[:2]
    related = "\n".join(review_card(x) for x in same[:6])

    photo_html = ""
    if r["photo"]:
        photo_html = f"""        <figure class="hero-photo"><img src="{E(r['photo'])}" alt="{E(r['name'])}, {E(where_short)}, photo by MsJAG" width="1200" height="800" loading="eager"></figure>\n"""

    when_text = r["when"].strftime("%B %d, %Y").replace(" 0", " ") if r["when"] else E(r.get("date", ""))
    address_line = location_line(p)
    body = f"""{crumbs_html(trail)}
        <article class="review">
            <div class="rmeta"><span class="pill">{E(CATEGORY_WORDS.get(r['category'], r['category']))}</span><span>Reviewed {when_text}</span></div>
            <h1>{E(r['name'])} <span class="h1-sub">Review</span></h1>
            <p class="where">{E(address_line) if address_line else 'Online business'}</p>
            <div class="verdict s-{score_class(r['rating'])}">
                <span class="stars" aria-hidden="true">{stars(r['rating'])}</span>
                <span class="num">{r['rating']}/5</span>
                <span class="label">{E(r['label'])}</span>
            </div>
{photo_html}            <div class="text">
{body_text}
            </div>
            <p class="byline">Reviewed by <a href="/">MsJAG</a>, Level 7 Google Local Guide. Self-funded visit, no sponsorship.</p>
            <div class="actions">
                {f'<a class="btn" href="{maps_url}" target="_blank" rel="noopener">Get directions</a>' if address_line else ''}
                <a class="btn ghost" href="/archive.html">All {len(all_reviews)} reviews</a>
            </div>
        </article>
        <section class="related">
            <h2>More reviews {('in ' + HUB_INFO[hub][0]) if hub else 'from MsJAG'}</h2>
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
    }
    if r["iso"]:
        review_schema["datePublished"] = r["iso"]
    html_out = page(title, desc, r["url"], body,
                    [review_schema, crumbs_schema(trail, r["url"])], og_image=r["photo"])
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


def build_hub(slug, title, h1, intro, items, trail, extra="", link_more=None, picks_heading=None):
    url = f"/{slug}/"
    items_sorted = sort_best(items)
    body = f"""{crumbs_html(trail)}
        <section class="hub-head">
            <h1>{h1}</h1>
            <p class="intro">{intro}</p>
{stats_line(items)}
        </section>
{extra}
{picks_section(items, picks_heading) if picks_heading else ''}
{sections_by_category(items, link_more)}"""
    desc = re.sub(r"<[^>]+>", "", intro)
    desc = snippet(desc, 158)
    # Share image: newest photo from a 4 or 5 star review, otherwise any photo
    share_photo = next((x["photo"] for x in sort_newest(items) if x["photo"] and x["rating"] >= 4), None) \
        or next((x["photo"] for x in sort_newest(items) if x["photo"]), None)
    return url, page(title, desc, url, body, [item_list_schema(title, url, items_sorted), crumbs_schema(trail, url)],
                     og_image=share_photo)


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
    topic_links = {c: f"/toronto/{s}/" for c, (_, s) in TORONTO_TOPICS.items()}
    topic_strip = "        <div class=\"chips\">" + "".join(
        f'<a href="/toronto/{s}/">{E(n)}</a>' for _, (n, s) in TORONTO_TOPICS.items()) + "</div>"
    url, out = build_hub(
        "toronto", "Toronto Restaurant, Bar & Venue Reviews | MsJAG",
        "Toronto <em>reviews.</em>",
        f"Honest, unsponsored reviews of {len(tor)} Toronto restaurants, bars, nightclubs, hotels, clinics and shops, "
        "from downtown and King West to North York, Etobicoke and East York. Every visit is self-funded and "
        "written by MsJAG, a Level 7 Google Local Guide.",
        tor, [("Home", "/"), ("Canada", "/canada/"), ("Toronto", None)],
        extra=topic_strip, link_more=topic_links, picks_heading="Latest Toronto Editor's Picks")
    write(url, out)
    sitemap.append((SITE + url, newest_iso(tor), "weekly", "0.9"))

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
