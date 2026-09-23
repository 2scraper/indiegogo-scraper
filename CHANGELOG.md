# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/) as
closely as a CLI toolkit can. A **patch** release means fixes — it does not
promise that every flag and default is frozen, so a behaviour-changing
default can land in one. When that happens it is called out at the top of
the release notes rather than left to be discovered from a bill or a diff.

## [Unreleased]

> **Correction to v0.1.0's release notes.** They said the 2Captcha balance
> was "unchanged to five decimal places". That was measured across the four
> bare-browser runs and is true of them, but it is not true of the session as
> a whole: the balance moved **$0.0024** over the day, which is Scraping
> Browser session usage from `--cdp-endpoint` runs. The claim that matters is
> narrower and now stated that way — 2Captcha's own statistics endpoint
> reports **0 captcha solves and $0.00000** for the day. A run without
> `--cdp-endpoint` still costs nothing at all.

### The site has a SECOND captcha, configured but never rendered

Asked plainly which captcha this site uses, and answered by measuring. There
are two, at different layers:

1. **Cloudflare Turnstile**, in a managed challenge at the edge. This is the
   one v0.1.0 documents: met, self-cleared, never solved.
2. **reCAPTCHA Enterprise**, wired into the application and enabled on every
   page the site serves — site key `6LeRruUr…`, the enterprise loader on
   **recaptcha.net** (not google.com), and an empty `<captcha-widgets>` mount
   point. It is never shown to an anonymous reader.

"No challenge rendered" is not "no captcha configured", so the useful
question was whether this repo would RECOGNISE it. Constructing the shapes
this site's own captcha would take found two the static detector missed.

### Fixed

- **Scraper API: `waitFor` is now sent as a JSON object.** Measured
  2026-09-23 against `scraper.2captcha.com/tasks/sync`: the JSON-encoded
  string this client sent (the form older docs described) is refused with
  HTTP 422 "params.waitFor must be an object" -- and the task is still
  billed ($0.0005) -- so every run with `--wait-text` or its sibling wait flags
  failed with exit 5. The object form answers HTTP 200.
- **Scraper API: the target page's status is read from `http_code`.** The
  response's `status` field is the API's own verdict string (`"success"`),
  not the target site's HTTP code, so a target 403/503 was never seen by
  this client. `http_code` (an int) is read now, with `status` kept as a
  fallback only when it is an int.
- **A rendered enterprise widget went undetected.** The iframe rung matched
  `recaptcha/api2/anchor`; this site serves `recaptcha/enterprise/anchor`,
  where the sitekey can be only in the frame's `k=` parameter.
- **A sitekey inside the site's own `<captcha-*>` mount went undetected.**
- **`category` is localised and differed BETWEEN MODES.** The same German
  campaign read `Produktivität` from `--mode search` (the API's localised
  name) and `Productivity` from `--mode campaign` (a numeric code resolved
  through an English table).
- **`pyproject` extras had drifted from `requirements-*.txt`**
  (`playwright>=1.44` against `>=1.40.0`, `selenium>=4.20` against
  `>=4.15.0`). CI installs the requirements files, so the drift was invisible.
- **The offline suite's credential check scanned gitignored files** and
  failed on a `--dump-html` capture containing a site nonce — a file that
  cannot be committed. `.github/ci_checks.py` had already learned this;
  the suite now matches it.
- **`.gitignore` did not cover this repo's own default output prefixes.**

### Added

- **`category_code`** on `Campaign` — the site's numeric, locale-independent
  catalogue id. Join on it; `category` is display text.
- Checks pinning all of the above, each verified to fail when its fix is
  reverted.

### The proxy path, now exercised live

v0.1.0 shipped with `--proxy` untested against a real gateway -- there was
no credential to test with. There is now, and the path works, but testing it
found a defect first.

- **Fixed: a pasted proxy-LIST line exited 1 (crash) instead of 2 (bad
  usage).** Proxy lists are `scheme://host:port:login:password`; this
  expects `http://login:password@host:port`, and the extra colons land in
  the port. `proxy_pool` already validated it and raised with a message
  naming the fix -- but no engine caught `ProxyError` at its entry point, so
  it reached the interpreter as a traceback. `proxy_pool`'s own comment said
  this should be exit 2; now it is, in all three engines, with the password
  absent from the message.

Measured 2026-09-17 through a 2Captcha residential gateway:

- **The site served content on the FIRST response, with no challenge** --
  better than the datacentre path, which met one on 2 of 4 runs.
- **`-region-XX` is honoured**: `de` → Munich (M-net), `us` → Reston (AT&T),
  `gb` → Glossop (Plusnet), `tr` → Istanbul (Türk Telekom), all residential
  ISPs. The Scraping Browser's `country-` segment was NOT honoured on the
  accounts tested (a `country-us` login exited from Turkey), which is worth
  knowing before trusting either.
- **One credential mints many exits**: 5 `-session-` logins → 5 distinct
  addresses (RU, US, US, IN, LK).
- **`--concurrency 3` gave each worker its own session** and returned 144
  rows over 6 pages, `(page, position)` unique, status complete.
- **A dead exit rotates rather than retrying**: a pool with an unreachable
  entry first reported `ERR_PROXY_CONNECTION_FAILED`, rotated to the next
  exit and completed with 24 rows -- the "a proxy failure is not a timeout"
  invariant, exercised rather than asserted.
- **Selenium's documented limitation confirmed**: it cannot authenticate a
  proxy, says so, and then legitimately returns 39 bytes and exit 3 where
  the other two engines returned 24 rows through the same exit.
- **No credential reached any log** across every run above.

### Verified, not assumed

- `--fingerprint` applies a real user agent, locale and timezone
  (`de-DE`/`Europe/Berlin` for a German fingerprint — not the `en-DE` bug
  this family shipped four times).
- `--concurrency 3` over 6 pages returns 144 rows with unique
  `(page, position)` and no duplicate skus.
- `/de/` and `/zh/` each return 48/48 fully populated rows.

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
