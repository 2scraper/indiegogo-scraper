# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/) as
closely as a CLI toolkit can. A **patch** release means fixes — it does not
promise that every flag and default is frozen, so a behaviour-changing
default can land in one. When that happens it is called out at the top of
the release notes rather than left to be discovered from a bill or a diff.

## [0.1.0] — 2026-09-17

First release. Three browser engines, an HTTP-only client, and three modes.

### Added

- **`--mode search`** — project search listings, 24 rows per page, read from
  the site's own `searchProjectsForCards` payload. Page 1 comes inlined in
  the document and pages 2+ from the XHR the site's own "Load more" button
  makes; both are the same object shape, so one parser reads both and they
  cannot drift.
- **`--mode campaign`** — one campaign page to one row, including the
  campaign's ISO 4217 currency, which the listing does not publish.
- **`--mode rewards`** — one row per reward tier, with prices, stock and
  estimated delivery.
- **`playwright_scraper.py`** (primary), **`puppeteer_scraper.py`**,
  **`selenium_scraper.py`** — flag-for-flag identical CLIs, asserted in both
  directions by the offline suite.
- **`scraper_api_client.py`** — the 2Captcha Scraper API, no local browser
  required.
- **`diff_runs.py`** — diff two runs by `sku`, refusing pairs that are not
  comparable.
- **Cloudflare Turnstile support** (`TurnstileTaskProxyless`), including the
  `turnstile.render` interception a Challenge page requires, installed by all
  three engines in each driver's own dialect.
- **Docker image**, built and run in CI.

### Measured on 2026-09-17

From a datacentre address (Hetzner, Helsinki, AS24940):

- `curl`/`requests` got **HTTP 403 on 8 of 8 routes**, `robots.txt` and the
  site's own JSON API included.
- A **local headless Chromium returned a full page of results on 4 of 4
  runs** with no key, no proxy and no CDP endpoint. Two of four met the
  Cloudflare challenge first and cleared it on the next navigation
  themselves: **0 solves attempted, 2Captcha balance unchanged to five
  decimal places**.
- The Scraping Browser API and the Scraper API both worked on every attempt.
- The canary's first dispatch, from a bare GitHub Actions runner with no
  secret configured, returned **72 rows across 3/3 pages** with the first
  response already unchallenged — a second, independent network position
  agreeing with the first.
- **Zero JSON-LD blocks** on four page kinds.
- The search result set is **capped**: the site reports 10,000 matches
  across 417 pages, but pageIndex 416 returns nothing while 415 returns a
  full 24 — a real ceiling of **9,984 rows**, binary-searched live.
- **110 of 432** sampled search rows were campaigns hosted on gamefound.com
  rather than indiegogo.com.

### Notes for anyone porting this elsewhere

- `?page=2` on the search listing is accepted, answers HTTP 200 and returns
  **page 1 again**. `product_parser.page_url()` raises rather than returning
  a URL, so the mistake cannot be made quietly.
- `pageIndex` is **0-based**: pageIndex 0 is page 1.
- `cf-turnstile` is **not** used as a challenge marker. Counted across nine
  captures it appeared on all six served pages and was absent from a
  challenge fetched without a browser extension injecting it.
  `challenges.cloudflare.com` separates cleanly and is what this repo uses.
- `campaignOutcome` is **not** a success flag — 20 of 60 sampled rows with
  `outcome=0` had already passed their goal. The raw integer is carried with
  no invented label.
- `fundedInSeconds` is never null; it reports `0` for "not applicable", which
  is normalised away.

[0.1.0]: https://github.com/2scraper/indiegogo-scraper/releases/tag/v0.1.0
