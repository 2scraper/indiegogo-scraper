# indiegogo-scraper

[![release](https://img.shields.io/github/v/release/2scraper/indiegogo-scraper?sort=semver)](https://github.com/2scraper/indiegogo-scraper/releases)
[![tests](https://github.com/2scraper/indiegogo-scraper/actions/workflows/tests.yml/badge.svg)](https://github.com/2scraper/indiegogo-scraper/actions/workflows/tests.yml)
[![canary](https://github.com/2scraper/indiegogo-scraper/actions/workflows/canary.yml/badge.svg)](https://github.com/2scraper/indiegogo-scraper/actions/workflows/canary.yml)
[![python](https://img.shields.io/badge/python-3.9%20%7C%203.12-blue)](pyproject.toml)
[![licence](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![engines](https://img.shields.io/badge/engines-Playwright%20%7C%20Selenium%20%7C%20Puppeteer-informational)](#engines)
[![runs without an account](https://img.shields.io/badge/runs%20without%20an%20account-yes-brightgreen)](#do-you-need-a-2captcha-account)

Scrapes **indiegogo.com** project listings, campaign pages and reward tiers
to JSON or CSV. Three interchangeable browser engines, plus an HTTP-only
client that needs no local browser at all.

```bash
pip install -r requirements.txt -r requirements-playwright.txt
playwright install chromium
python3 playwright_scraper.py --pages 3
```

That command needs no account, no key and no proxy. See below for what it
returned when measured, and for the one thing on this site that does need a
paid product.

---

## Do you need a 2Captcha account?

**For most of this, no.** Everything below was measured on **2026-09-17**
from a datacentre address (Hetzner, Helsinki, AS24940) — the kind of address
most sites treat worst.

| How you fetch it | Result |
|---|---|
| `curl` / `requests` | **HTTP 403 on 8 of 8 routes.** The home page, the search listing, a campaign page, `robots.txt`, `sitemap.xml` and the site's own JSON API alike. Cloudflare managed challenge, `cf-mitigated: challenge`. |
| **Local headless Chromium** (Playwright, no key, no proxy) | **4 of 4 runs returned 48 rows across 2 pages, exit 0.** |
| Local headless Chromium (Selenium, no key, no proxy) | 4 of 4 runs returned 24 rows. **2 of the 4 met the challenge first and cleared it on the next navigation by themselves** — 0 solves attempted, and the 2Captcha balance was unchanged to five decimal places either side. |
| 2Captcha Scraping Browser API (`--cdp-endpoint`) | Worked on every attempt. |
| 2Captcha Scraper API (`scraper_api_client.py`) | HTTP 200, 137,504 bytes, inlined results payload present, zero challenge markers. |

So the honest summary is: **an HTTP client cannot read this site at all, and
a real browser can — including from a datacentre IP, and including through
the challenge.** Cloudflare's managed challenge is designed to be cleared by
a browser that runs its JavaScript, and that is what happens here.

What the paid products actually buy:

* **Volume from many addresses.** One address clearing one challenge is very
  different from one address clearing a hundred. `--proxy-file` plus
  `--concurrency` is the path, and each worker clears the challenge
  separately.
* **A specific country.** The Scraping Browser's `country-` segment picks
  the exit.
* **No browser infrastructure.** `scraper_api_client.py` needs no Chromium
  on your machine at all — only `requests`.
* **A solver, if the challenge ever stops self-clearing.** The Turnstile
  path is implemented and wired in (`TurnstileTaskProxyless`); it simply was
  not needed in any run measured here. See [Captchas](#captchas).

---

## What it collects

### `--mode search` (default)

One row per campaign in a project search, 24 per page.

```bash
python3 playwright_scraper.py --pages 5 --sort most-funded --format both
python3 playwright_scraper.py --pages 3 \
  --url 'https://www.indiegogo.com/en/projects/search?projectCatalogCategories=BoardAndCardGames'
```

`sku` `title` `url` `image_url` `price` (funds raised) `currency` `category`
`platform` `sort` `page` `position` `creator` `creator_url` `goal`
`pct_funded` `backers_count` `followers_count` `campaign_start`
`campaign_end` `days_left` `phase_label` `phase` `outcome_code`
`funded_in_seconds` `short_description` `tags`

### `--mode campaign`

One campaign page → one row, with everything above plus the campaign's
**ISO 4217 currency** (the listing publishes only a symbol), `campaign_day`,
`stretch_goal_count`, `reward_count` and `published_date`.

```bash
python3 playwright_scraper.py --mode campaign \
  --url https://www.indiegogo.com/en/projects/gpdhk/gpd-win-max-3-handheld-gaming-laptop
```

### `--mode rewards`

One row per reward tier on a campaign: `price`, `original_price`,
`lowest_price_30d`, `discount_pct`, `remaining_stock`, `purchased_count`,
`estimated_delivery`.

```bash
python3 playwright_scraper.py --mode rewards --url <campaign url>
```

`sample_output.json` and `sample_output.csv` are page 1 of a real
`--mode search` run — 24 rows, not curated, not hand-written.

---

## Traps that look like bugs

Every one of these is the site's behaviour, not a defect here. They are
listed because hitting one unwarned reads as a broken tool.

### Most of the search results are not Indiegogo campaigns

indiegogo.com now runs on the **Gamefound** platform, and its search results
mix in campaigns hosted on `gamefound.com`. Measured across 432 sampled
rows: **110 were Gamefound-hosted, 322 Indiegogo.** They are real results
that indiegogo.com serves — they are simply not on this site.

Every row carries a **`platform`** column, and `--platform indiegogo`
filters to the rest. `--mode campaign` refuses a Gamefound row rather than
fetching it, because `/en/projects/<creator>/<slug>` answers **404** for
those on this host.

The mix depends heavily on the sort. Measured over 5 pages (120 rows) each:

| `--sort` | Gamefound | Indiegogo |
|---|---:|---:|
| `default` | 49 | 71 |
| `newest` | 43 | 77 |
| `oldest` | **0** | 120 |
| `most-popular` | 44 | 76 |
| `most-funded` | 43 | 77 |
| `a-z` | **0** | 120 |
| `z-a` | **0** | 120 |
| `ending-soon` | 36 | 84 |
| `most-discussed` | 2 | 118 |

There is no platform filter in the site's own API — `source`,
`userCommunitySearchTypes` and `retailerOfferTypes` were each measured to
leave the mix unchanged.

### `?page=2` silently returns page 1

The listing has **no per-page URL**. `?page=2`, `?pageNumber=2` and `?p=2`
on `/en/projects/search` are all accepted, all answer HTTP 200, and all
serve the identical first 24 cards. Real pagination is a **0-based
`pageIndex` inside a POST body** to `/api/projectSearch/searchProjectsForCards`,
which this scraper calls from inside the cleared browser session.

`product_parser.page_url()` raises on purpose, so the mistake cannot be made
quietly: a `?page=N` convention here would refetch page 1 forever, find no
new ids, decide the listing was exhausted, and report a **complete** run
holding 24 rows.

### "Complete" and "exhaustive" are different words

Every search is capped. The site reports `totalItemCount: 10000` and
`totalPageCount: 417` for an unfiltered query, and says so itself with
`hasCappedResults: true` — but **pageIndex 416 returns zero items** while
415 returns a full 24, so the real ceiling is **416 pages × 24 = 9,984
rows**, binary-searched live.

A run that fetches every page really is `status: complete` — it got
everything the site will serve. It is also a **sample**. The sidecar records
`total_results`, `pages_available`, `capped_by_site` and `reachable_max` so
a consumer can tell, and `diff_runs.py` says so before diffing.

A narrower query is not capped: `?term=solar` reported 801 matches with
`hasCappedResults: false`.

### The sort is part of the data, not its order

Because the result set is capped and served 24 at a time, the sort decides
**which** campaigns end up in your file. Two runs under different sorts are
different samples of the same query, so `sort` rides on every row and in the
sidecar, and **`diff_runs.py` refuses to compare two runs that disagree
about it** — every line of that diff would be an artefact of the sampling.

### `campaignGoal` is often absent, and `fundedInSeconds` lies with a zero

**79 of 432** sampled campaigns publish no goal at all — an "Express
crowdfunding" campaign has none by design. `goal` and `pct_funded` are both
`null` on those rows, never `0`.

`fundedInSeconds` is **never null** in the API: a campaign that has not
reached a goal and one with no goal at all both report `0`. Written through,
that reads as "funded in zero seconds". It is normalised to `null`.

### `outcome_code` is not "did it succeed"

The API's `campaignOutcome` is tempting to read as success. It is not:
**20 of 60 sampled rows with `outcome=0` had already passed their goal**
(one had raised 640,492 against a 50,000 goal). It tracks whether the
funding phase has *ended*. The raw integer is carried through with no
invented label; **`phase_label` is the site's own human-readable status**
and is what you want.

### The rendered card is lossier than the API

Where both exist, this scraper reads the site's own JSON. The card rounds
and abbreviates:

| | API | rendered card |
|---|---|---|
| funds raised | `12576.05` | `€12,576` |
| backers | `3409` | `3.4k` |
| followers | `1065` | `1.1k` |
| time left | `2026-10-17T02:00:00Z` | `29 days left` / `29 Tage verbleiben` |

`price_source` records which was used (`api`, `dom`, `detail`), and
`diff_runs.py` reports a price difference that comes with a `price_source`
difference as `source_changed` rather than `changed`.

### `kr` is two currencies, so it stays null

The site's own currency table uses **`kr` for both Norwegian krone and
Swedish krona**. It cannot tell them apart from the symbol and neither can
this parser, so such a row gets a price with a `null` currency rather than a
coin flip. `kr.` (Danish) *is* distinguishable and resolves to DKK.
Currency is never defaulted to `"USD"`.

### The 30-day low sits next to the old price

Reward boxes carry the EU Omnibus "lowest price in the last 30 days"
disclosure in nearly identical markup to the struck-through price, told
apart only by a `data-qa` value. On the measured capture the 30-day low
**equals the current price** on every tier while the real discount is
12–26%, so reading it as a was-price would report a 0% discount on a
genuinely discounted reward. It gets its own column, `lowest_price_30d`,
and `discount_pct` is computed only from `original_price`.

### There is no JSON-LD

Zero `<script type="application/ld+json">` blocks on a search listing, a
campaign page, a rewards tab and a German-locale listing. Extraction reads
the site's own JSON instead — the same object for the inlined first page and
for the XHR that fetches later ones, so the two routes cannot drift.

### Locales

Nine, as path prefixes on one host: `en it fr de pl es cs pt zh`. Taken from
the site's own `hreflang` set. Number formatting is identical on `/en/` and
`/de/`; the countdown text is translated, which is one more reason the
absolute `campaign_end` is what this scraper records. Phase labels were
observed **untranslated** on `/de/`.

---

## Engines

All four entry points share the same flags and the same exit codes.
`smoke_test.py` asserts the three browser CLIs are flag-for-flag identical
in both directions.

| | needs a local browser | `--cdp-endpoint` | `--pages > 1` | notes |
|---|---|---|---|---|
| `playwright_scraper.py` | yes | yes | yes | **primary** |
| `puppeteer_scraper.py` | yes | yes | yes | pyppeteer is effectively unmaintained; its own README points at Playwright |
| `selenium_scraper.py` | yes | **no** | yes | see below |
| `scraper_api_client.py` | **no** | n/a | **no** | HTTP only; page 1 of a search, or one campaign |

**Selenium cannot use an authenticated remote CDP endpoint.** Playwright's
`connect_over_cdp` and Puppeteer's `browserWSEndpoint` take a full
`ws://user:pass@host:port` and authenticate on the WebSocket upgrade;
chromedriver's `debuggerAddress` takes a bare `host:port` with nowhere to
put a password. `--cdp-endpoint` is refused there rather than half-working.
An `INDIEGOGO_CDP_ENDPOINT` merely sitting in your `.env` is ignored with a
warning, so that engine stays runnable.

**Selenium's `--proxy-server` cannot authenticate at all.** Credentials are
stripped and a warning printed.

**`scraper_api_client.py` reads one page**, and that is the site's doing
rather than the API's: pages 2+ need a POST carrying the cleared session's
cookies, which a one-shot HTML fetch does not have. Asking for more reports
`partial` (exit 6) rather than a complete-looking page 1.

**Install exactly one engine.** Playwright and pyppeteer declare mutually
unsatisfiable pins (`pyee` <12 vs ≥13), and pyppeteer and selenium collide
on `urllib3`. Use a virtualenv per engine if you need more than one.

---

## Captchas

The only challenge this site serves is a **Cloudflare managed challenge**
carrying a **Turnstile**. 2Captcha solves those with
`TurnstileTaskProxyless`, and this repo implements that path.

It was not needed in any run measured here — the browser cleared the
challenge itself, 4 times out of 4, and **$0.00 was spent**. That is a
measurement, not a guarantee, so the path is wired and tested rather than
omitted.

One thing worth knowing if you ever debug it: **a Cloudflare Challenge page
publishes no sitekey anywhere in its markup.** Cloudflare calls
`turnstile.render(container, params)` once and keeps nothing, and
`TurnstileTaskProxyless` needs `sitekey`, `action`, `cData` and
`chlPageData` — all four of which exist only inside that call. So the
parameters are captured by an init script installed *before any page script
runs*, hooking `turnstile.render`. Each engine spells that its own way
(`add_init_script`, `evaluateOnNewDocument`, CDP
`Page.addScriptToEvaluateOnNewDocument`). A static read of the HTML — any
static read — cannot produce a solvable task.

`--solve-captcha when-blocked` is the default and counts results before
paying. A missing key is a warning, not a crash.

### A marker that fires on good pages is worse than none

`cf-turnstile` is the obvious marker for a Turnstile and is **measured
useless here**. Counted across nine captures on 2026-09-17:

| | `cf-turnstile` | `challenges.cloudflare.com` |
|---|---:|---:|
| 6 served pages | **1 each** | 0 each |
| the site's own 404 | 1 | 0 |
| a real challenge, fetched via browser | 1 | 6 |
| a real challenge, fetched via curl | **0** | 5 |

It fires on every page fetched through the Scraping Browser — whose
auto-solve extension injects a Turnstile hunter into everything it loads —
and is *absent* from the one challenge fetched without that extension.
Carrying it would report exit 3 on a 630 KB page holding the full result
set. This repo uses `challenges.cloudflare.com`.

---

## Output contract

Exit codes: `0` ok · `1` crash · `2` bad usage · `3` blocked · `4` zero rows
· `5` remote API error · `6` partial.

A run that finds nothing **writes nothing** — `--allow-empty` is the opt-out
— so an empty result never replaces last night's good output. Every run
writes `<out>.meta.json` beside its data, except a failed one, which leaves
the previous good output and its sidecar alone.

```bash
python3 diff_runs.py --old projects.2026-09-16.json --new projects.2026-09-17.json
```

---

## Configuration

Credentials live in `.env` next to the scripts, never on a command line — a
secret in `argv` is readable by anything that can run `ps`.

```bash
cp .env.example .env
python3 env_config.py        # prints what was picked up, without secrets
```

Precedence, highest first: **explicit flag → exported environment variable →
`.env` → default.** A value still carrying `{braces}` counts as unset, so a
copied example is never sent to an API as if it were a key.

Variables: `TWOCAPTCHA_KEY`, `INDIEGOGO_CDP_ENDPOINT`, `INDIEGOGO_PROXY`,
`INDIEGOGO_URL`.

Scraping Browser profile credentials expire after about a day, so fetch a
fresh endpoint rather than keeping one.

---

## Tests

```bash
python3 smoke_test.py -v     # the offline suite; no engine library needed
pytest                        # the same checks, wrapped as one test
```

`.github/workflows/tests.yml` runs it on Python 3.9 and 3.12, installs each
engine in its own virtualenv and fails if the installed one reports skipped,
and builds and runs the Docker image. `canary.yml` does a real 3-page run
daily and asserts row counts, field coverage, page/position uniqueness and
the sidecar's cap arithmetic.

---

## Licence

MIT. Scrape responsibly: respect the site's terms, keep request rates
sane, and do not republish personal data.
