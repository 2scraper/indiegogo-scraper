# Troubleshooting

## Exit codes

| code | meaning | what to look at |
|---|---|---|
| 0 | ok | — |
| 1 | crash | the traceback |
| 2 | bad usage | your flags; the message names the problem |
| 3 | blocked | the site refused the page before it could be parsed |
| 4 | zero rows | the page was served and parsed to nothing |
| 5 | remote API error | a 2Captcha product call failed, not the site |
| 6 | partial | some pages were gathered, then the run stopped early |

`<out>.meta.json` names `stop_reason` and **which** pages failed, by number.

## "Every page comes back as exit 3"

indiegogo.com answers an HTTP client with a Cloudflare challenge on every
route. If you are using `requests` or `curl`, that is expected and not
fixable from here — use one of the browser engines.

If a **browser** run is blocked, in order:

1. Run it again. The challenge self-cleared on the next navigation in 2 of 4
   measured runs.
2. Run `--headful`. Measure it rather than assuming; it is four runs.
3. Add `--proxy` or `--cdp-endpoint`.
4. Pass `--dump-html` and look at what actually arrived. A file containing
   `challenges.cloudflare.com` is a challenge; one containing
   `404: Page Not Found` is the site's 404, which for a campaign URL usually
   means the campaign is hosted on gamefound.com (check the `platform`
   column) and does not exist on this host.

## "0 rows, but the page clearly has campaigns" (exit 4)

The log distinguishes two causes:

* **`stop_reason: parser_found_nothing`** with a message about the inlined
  results payload — the site renamed or moved
  `App.Components.Search.SearchProjectsResults`. That is a site change; see
  `extract_inline_search_result()` in `product_parser.py` and the HTML dump.
* **`no_results`** — the site's own `search-count:0`. Your query genuinely
  matched nothing. This is a correct answer and is not retried.

## "I asked for 10 pages and got 1"

With `scraper_api_client.py`, that is expected and reported as `partial`
(exit 6): pages 2+ need a POST carrying a cleared browser session's cookies.
Use `playwright_scraper.py`.

With a browser engine, check `stop_reason`:

* `site_result_cap` — you reached the end of what the site will serve. The
  sidecar's `reachable_max` says how many rows that was.
* `no_new_products` — a page added no ids not already seen.
* `page_failed` — the search API refused a page mid-run; `pages_failed`
  names which.

## "The numbers changed between two runs but nothing happened on the site"

Check `price_source` on both sides. The API publishes `12576.05` where the
rendered card publishes `12,576`, so a run that used the API and one that
fell back to the DOM differ on every row. `diff_runs.py` reports that as
`source_changed`, not `changed`.

Also check `sort`: two runs under different sorts are different **samples**
of a capped result set, and `diff_runs.py` refuses to compare them.

## "`--cdp-endpoint` does nothing / 401 / profile_locked"

* Scraping Browser profile credentials expire after about a day. Fetch a
  fresh endpoint.
* A profile allows **one live connection at a time**. `profile_locked` means
  another run still holds it; wait, or use a different `pid`.
* `selenium_scraper.py` cannot use one at all — chromedriver's
  `debuggerAddress` has nowhere to put a password. It is refused if you pass
  the flag, and ignored with a warning if the variable merely sits in `.env`.

## "`python3 env_config.py` says my key is not set"

The loader treats an empty value, and any value still containing `{braces}`,
as unset — that is what stops a copied `.env.example` being sent to an API
as if it were a credential. Replace the whole placeholder, braces included.
