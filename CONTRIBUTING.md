# Contributing

Bug reports, site-change reports and pull requests are all welcome. This file
covers the few things specific to a scraper, which are not the usual ones.

## Before you open anything

Run the offline suite. It needs no network, no browser and no API key, and
takes about a second:

```bash
pip install -r requirements.txt
python3 smoke_test.py
```

It prints its own check count, and lists any group it had to skip because an
engine library is absent.

**The suite must pass with no engine installed at all.** CI installs only
`beautifulsoup4` and `requests`, so any import of `playwright_scraper`,
`puppeteer_scraper` or `selenium_scraper` in a test has to sit inside
`try/except ImportError` with the skip recorded. This is easy to get wrong
locally, where you almost certainly have an engine installed and an unguarded
import passes.

If the suite fails on a clean clone, that is itself the bug — say so.

## Never commit a credential

`.env` is in `.gitignore`. Keep it there.

The scrapers mask `user:pass@` in their own log lines, but two things are
**not** masked: raw HTML dumps and your shell history. Before pasting any
output into an issue or a PR, replace keys, proxy passwords and full
`ws://user:pass@host:9222` endpoints with `***`.

CI fails the build if something that looks like a credential is committed.
That check is a backstop, not a review — a leaked key has to be rotated
whether or not the check caught it.

## Reporting a site change

Indiegogo changing its markup or its own API is the normal way this stops
working, and it has its own issue template. Worth knowing before filing:
this site publishes **no JSON-LD anywhere**. Every mode reads the site's OWN
data first and the rendered DOM only as a fallback:

1. **`--mode search`** — page 1 reads the results payload inlined in the
   search page (the `App.Components.Search.SearchProjectsResults` component
   props); pages 2+ repeat the site's own
   `POST /api/projectSearch/searchProjectsForCards` call with the next
   `pageIndex`. If the inlined payload disappears, the run falls back to the
   rendered cards (`data-qa` hooks) for page 1 and says so by name.
2. **`--mode campaign`** — `window.__INITIAL_STATE__`, inlined in the
   campaign page.
3. **`--mode rewards`** — the reward boxes on the campaign page, anchored on
   the site's `data-qa` attributes (`price-type:Old`,
   `price-type:ThirtyDaysLowest`, ...). The least durable of the three, and
   where a markup change is most likely to land.

If you are reporting a change, saying which of these broke (and for the DOM
paths, which `data-qa` value) narrows the fix a lot. Note that some search
results are campaigns hosted on gamefound.com: their campaign URLs answer
404 on indiegogo.com, and that is the site's behaviour, not a bug.

`--dump-html PATH` writes the exact bytes the parser was given, on success as
well as failure, and a run that finds nothing writes a dump and a screenshot
next to the output on its own.

## Pull requests

**Add a test for the behaviour you are changing.** `smoke_test.py` is a
single file of plain functions with inline HTML fixtures — no pytest, no
conftest, no fixtures directory. Copy the nearest existing check and edit it.

Several properties in this repo exist because they were once absent, cost
real time to find, and are now pinned by a test so a PR that breaks one fails
rather than silently regressing:

- **`?page=N` on a search URL is ignored by the site.** It answers HTTP 200
  with the identical first 24 cards, so a `page_url()`-style construction
  would refetch page 1 forever and report a complete run. Pages are
  addressed by the POST body's 0-based `pageIndex` instead, planned from
  the site's own page count (`page_flow.plan_page_indices`), and
  `product_parser.page_url()` raises rather than returning anything.
- **`price` on a Campaign row is funds RAISED**, and `price_source` records
  whether it came from the API (an exact float) or a rendered card (rounded
  for display). `diff_runs.py` reports a price difference that comes with a
  `price_source` difference as `source_changed`, not `changed`.
- **Zero is not always a value.** `funded_in_seconds` is `0` in the API for
  campaigns where it does not apply, and is written as null.
- **A run that finds nothing writes nothing.** It must not replace a good
  output file with `[]`. `--allow-empty` is the opt-out.
- **Exit codes are a contract**, not decoration: `0` ok, `1` crash, `2` bad
  usage, `3` blocked, `4` the site answered and nothing matched, `5` the
  content was never obtained (or a served page parsed to nothing), `6`
  partial. A pipeline branches on these.
- **An EMPTY page is never retried and never counted as blocked.** A
  search the site itself answers with `search-count:0` is a correct answer
  to the question that was asked, not a failure, and exits 4. `page_flow.STATE_POLICY` holds that for all three engines so
  they cannot disagree about it.
- **A challenge marker is not necessarily a real challenge on THIS site.**
  `product_parser.BOT_CHALLENGE_MARKERS` decides whether a page is a
  Cloudflare challenge. Count any marker you add on a page you know is GOOD
  first: a marker that also appears on served pages turns every good page
  into a "blocked" one.
- **A `sku` already written by an earlier page of the same run is dropped,
  not duplicated.** See `dedupe_by_key` in `output_writer.py`. Note that
  `sku` means something different per mode — the campaign's `projectID`
  for search/campaign, the reward's own `productID` for `rewards` (one
  campaign has many rewards) — so a change here should be checked against
  all three modes, not just one.

There is also a naming check: certain phrases are banned repo-wide and the
suite fails naming them. If it trips, read the message — the phrase is wrong
for a reason, not merely unfashionable.

### Style

- **Match the file you are editing.** No formatter is enforced.
- **Comments explain *why*.** What the code does is visible; why it does it
  that way, especially where the obvious version is wrong, is not.
- **A timeout on every remote call.** Every browser library used here has
  needed an explicit timeout its own API does not provide, and each has
  needed its own route out of the runtime — reporting a timeout is not the
  same as exiting on one. If you add a call to a remote browser or API,
  bound it.
- **Fail loudly.** A function that returns an empty list on error, or logs
  success without checking that the thing it wanted actually happened, is a
  common bug class. A selector that matches the *wrong* element is worse
  than one that matches nothing, because the second one tells you.
- **Never present a guess as a fact.** A figure that could not be read is
  `None`, not `0` or a copied neighbour's value; a currency the site states
  only as an ambiguous symbol (`kr`) is `None`, never a defaulted `"USD"`.

### If your change needs a live run

Most do not — the suite covers the parsers, the writers, the captcha
classifier and the CLI contract against inline fixtures cut from real
captures. If yours genuinely needs indiegogo.com, say in the PR what you
ran (`--mode`, `--url`, `--pages`, the engine), whether you used a proxy, and
what you got — including the exit code and `.meta.json`'s `stop_reason`.
Every page here is behind a Cloudflare managed challenge that a real browser
usually clears by itself, so say whether a challenge was met.

Do not add anything that requires logging in, or that submits a form on the
site. This project only reads pages an anonymous visitor is served.

## Scope

This repo scrapes **public pages** on Indiegogo: the project search
listing, individual campaign pages and their reward tiers, exactly as an
anonymous visitor is served them. Out of scope:
anything behind a login, anything that submits a form, and anything that
defeats a protection rather than passing it the way an ordinary browser
does.

## Licence

MIT. By opening a pull request you agree your contribution ships under it.
