# How torontoeatsandreviews.com works

A plain-language map of the site. Hosted free on GitHub Pages (repo `msjag416/toronto-eats-site`, branch `main`), with the domain at Porkbun. Anything saved to `main` goes live a few minutes later.

## The pages you edit by hand

| File | What it is |
|---|---|
| `index.html` | Homepage. Shows the 7 newest reviews as cards, plus matching Google data (JSON-LD) for those 7. |
| `archive.html` | "The Vault". Holds **every** review in one list called `const reviews = [ ... ]`. This list is the master copy of all reviews. |
| `sitemap.xml` | The list of pages Google should visit. The builder adds its own pages here; the dates for `index.html` and `archive.html` are still set by hand. |
| `robots.txt` | Tells Google it may visit every page and where the sitemap is. |
| `tvguide.html`, `cityevents.html`, `thanks.html` | Separate tools and pages, not part of the review system. |

## The pages made automatically

`build_review_pages.py` reads the review list in `archive.html` and makes:

| Address | What's on it |
|---|---|
| `/reviews/<name-city>.html` | One page per review: full text, stars, score label, address, directions button, photo if there is one, and 6 related reviews. |
| `/toronto/` | All Toronto reviews (includes North York, Etobicoke, East York, York, Scarborough). |
| `/toronto/restaurants/`, `/toronto/nightlife/`, `/toronto/services/` | Toronto reviews by topic. |
| `/grand-cayman/` | All Grand Cayman reviews. |
| `/mississauga/` | All Mississauga reviews. |
| `/canada/` | Every Canadian review, grouped by city. |
| `/world/` | Reviews outside Canada and Cayman, grouped by country. |

It also writes:

* `sitemap.xml` (keeps the hand-made entries, adds every page above)
* `review-links.json` (the archive uses this to turn each card title into a link to its own page)
* `review-photos.json` (remembers which photo goes with which review, so a photo stays on the review page after its card leaves the homepage)
* `review-pages.css` is the shared style for all of these pages. Same colours and fonts as the main site. Light theme only.

**Never edit the files inside `reviews/`, `toronto/`, `grand-cayman/`, `mississauga/`, `canada/` or `world/` by hand.** They are wiped and rebuilt every time.

## What happens when a new review is added

1. The review is added in the usual three spots (homepage card, homepage JSON-LD, archive list).
2. Those changes are saved to `main`.
3. GitHub runs `.github/workflows/build-review-pages.yml`, which runs the builder and saves the new pages. This takes a minute or two.
4. GitHub Pages publishes everything a few minutes later.

You can also run the builder yourself with `python build_review_pages.py`.

## How the builder decides where a review goes

* Address contains "Cayman" → Grand Cayman.
* Address has a Canadian province code (ON, BC, AB, QC…) → the city before it. Toronto boroughs go to Toronto, Mississauga gets its own page, everything else goes on the Canada page.
* Anything else → World, grouped by the last part of the address (the country).
* No address (online businesses) → still gets its own review page, just not listed on a city page.

## Google data on each page

* Review pages carry a `Review` block (business type, address, rating out of 5, date, author MsJAG) and a `BreadcrumbList`.
* City pages carry a `CollectionPage` with an `ItemList` of the reviews on that page.
* The archive builds its own review data in the browser and now writes dates as `YYYY-MM-DD`.

## Other automation

* `.github/workflows/update-cityevents.yml` runs `build_cityevents.py` daily for the events page. It is separate from the review system.

## Rules

* Light designs only, never dark themes.
* Every page has the footer with the JavaScript copyright year.
