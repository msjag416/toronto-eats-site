# How torontoeatsandreviews.com works

A plain-language map of the site. Hosted free on GitHub Pages (repo `msjag416/toronto-eats-site`, branch `main`), with the domain at Porkbun. Anything saved to `main` goes live a few minutes later.

## The pages you edit by hand

| File | What it is |
|---|---|
| `index.html` | Homepage. Shows the 7 newest reviews as cards, plus matching Google data (JSON-LD) for those 7. |
| `archive.html` | "More Reviews". Holds **every** review in one list called `const reviews = [ ... ]`. This list is the master copy of all reviews. |
| `sitemap.xml` | The list of pages Google should visit. The builder adds its own pages here; the dates for `index.html` and `archive.html` are still set by hand. |
| `robots.txt` | Tells Google it may visit every page and where the sitemap is. |
| `tvguide.html`, `cityevents.html` | Separate tools, not part of the review system. |
| `thanks.html` | Thank-you page (kept for FormSubmit's standard send). |

## The pages made automatically

`build_review_pages.py` reads the review list in `archive.html` and makes:

| Address | What's on it |
|---|---|
| `/reviews/<name-city>.html` | One page per review: full text, stars, score label, address, directions button, photo if there is one, and 6 related reviews. |
| `/toronto/` | All Toronto reviews (includes North York, Etobicoke, East York, York, Scarborough). |
| `/toronto/restaurants/`, `/toronto/nightlife/`, `/toronto/services/` | Toronto reviews by topic. |
| `/toronto/neighbourhoods/` and `/toronto/neighbourhoods/<name>/` | Toronto reviews by neighbourhood (worked out from the postal code, or the street for a few downtown streets). A neighbourhood needs 3+ reviews to get a page. These are the pages for "restaurant near me" searches. |
| `/toronto/<cuisine>/` (pizza, burgers, caribbean, sushi…) | Toronto reviews by cuisine or feature, found from the business name and review text. Needs 3+ reviews. |
| `/toronto/best-restaurants/` | Every 5-star Toronto restaurant, newest first. |
| `/thumbs/*.webp` | Small, fast copies of the review photos for cards. Made automatically from the full photos. |
| `/grand-cayman/` | All Grand Cayman reviews. |
| `/mississauga/` | All Mississauga reviews. |
| `/canada/` | Every Canadian review, grouped by city. |
| `/world/` | Reviews outside Canada and Cayman, grouped by country. |

It also writes:

* `sitemap.xml` (keeps the hand-made entries, adds every page above)
* The neighbourhood and cuisine links on the homepage, between the `<!-- NEIGHBOURHOODS:START -->` and `END` comments. Don't edit between those comments by hand.
* `review-links.json` (the archive uses this to turn each card title into a link to its own page)
* `review-photos.json` (remembers which photo goes with which review, so a photo stays on the review page after its card leaves the homepage)
* `site.css` is the one shared style file for every page (homepage, More Reviews, review pages, city pages). Magazine look: white pages, big photos, Fraunces serif headlines, Inter body text. Light theme only.

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

## Logo and icons

* `toronto-eats-logo.png` is the site logo Google reads (Organization logo).
* `favicon.ico`, `icon-32.png`, `icon-48.png`, `icon-192.png`, `icon-512.png` are the browser-tab icons (round, see-through corners).
* `icon-apple-180.png` is the phone home-screen icon (solid cream, because iPhones turn see-through corners black).
* `share-card.png` (1200×630) is the picture shown when a page is shared, unless the page has its own food photo.
* `tvguide.html` and `cityevents.html` keep their own icons.

## Contact form delivery

The form on the homepage and More Reviews sends to **Web3Forms** first (access key in the page code; Web3Forms keys are public by design). If Web3Forms fails or takes longer than 12 seconds, it tries **FormSubmit** as a backup. Both deliver to the same Gmail. If both fail, the visitor sees a "couldn't be sent, please try again" note and their typed message stays in the form. A Cloudflare Turnstile check (hidden unless needed) and a hidden honeypot box block bots first.

## Editorial policy wording (updated Nov 11, 2026)

The site says "Independent" and "No paid reviews". It does not say every visit is self-funded, because
JoAnne accepts invitations to previews and openings. The wording lives in `index.html` (policy quote,
About section, top bar, footer), `archive.html`, `thanks.html`, and the templates in `build_review_pages.py`.

To mark an invited visit, add `"hosted": true` to that review's entry in the `const reviews` list in
`archive.html`. Its review page then shows "Hosted visit: the venue invited me. The score is my own."
Every other review page shows "Independent review. No payment accepted."
