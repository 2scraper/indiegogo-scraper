"""
scraper_api_client.py — indiegogo.com through the 2Captcha Scraping Browser API
================================================================================

A plain HTTP client for 2Captcha's Scraper API (`scraper.2captcha.com`): you
hand it a URL, it runs a real browser on 2Captcha's side and hands back the
HTML. No local browser, no Chromium download, no driver.

**Why that is worth having on THIS site.** Every HTML route on
indiegogo.com answers a datacentre address with an HTTP 403 Cloudflare
managed challenge — measured 2026-09-17 on eight routes out of eight,
`robots.txt` and the site's own JSON API included. A plain `requests` call
gets the challenge, every time. This endpoint does not: measured the same
day, one call returned HTTP 200 and 137,504 bytes carrying the search page's
inlined results payload, with zero challenge markers on it.

What this client can and cannot do
-----------------------------------
It reads ONE page per call, and that is a property of the site rather than
of the API:

    --mode search     page 1 of a project search -> 24 rows. The raw
                      document inlines them, in the same shape the site's
                      own XHR returns, so parse_search_api reads both.
    --mode campaign   one campaign page -> one row.
    --mode rewards    the perks on one campaign -> one row each.

`--pages N > 1` is REFUSED for --mode search, with the reason, rather than
quietly returning page 1 under a complete-looking status. indiegogo.com's
listing has no per-page URL at all — `?page=2` is accepted, answers HTTP
200, and serves the identical first 24 cards — and the real pagination is a
0-based `pageIndex` inside a POST body to an endpoint sitting behind the
same Cloudflare challenge. Reaching it needs the cookies a cleared browser
session holds, which a one-shot HTML fetch does not have.

So: use this client for a single page with no local browser, and
playwright_scraper.py when you want more than one.

Usage
-----
    python3 scraper_api_client.py --key "$TWOCAPTCHA_KEY"

    python3 scraper_api_client.py --key "$TWOCAPTCHA_KEY" \\
        --url 'https://www.indiegogo.com/en/projects/search?term=solar'

    python3 scraper_api_client.py --key "$TWOCAPTCHA_KEY" --mode campaign \\
        --url https://www.indiegogo.com/en/projects/gpdhk/gpd-win-max-3-handheld-gaming-laptop

The key is read from TWOCAPTCHA_KEY in .env when --key is not given; never
pass a secret on a command line.

Requires: pip install -r requirements.txt   (no engine library at all)
"""

import argparse
import json
import logging
import os
import sys
import time
from typing import List, Optional
from urllib.parse import urljoin

import re

import requests
from bs4 import BeautifulSoup

from product_parser import (parse_search_api, parse_search_html,
                            parse_campaign, parse_rewards,
                            extract_inline_search_result, detect_page_state,
                            challenge_marker, sort_from_url, category_from_url,
                            is_project_url, CANONICAL_HOST, SORT_TYPES)
from output_writer import finish_run, dedupe_by_key, EXIT_REMOTE_API_ERROR
import page_flow
import env_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scraper_api_client")

API_BASE = "https://scraper.2captcha.com"
SYNC_ENDPOINT = f"{API_BASE}/tasks/sync"

# The API caps `timeout` at 120s and rejects bodies over 10,000 bytes.
MAX_API_TIMEOUT = 120

# EMPTY on purpose, and this is the honest answer rather than a limitation
# nobody wrote down. indiegogo.com's search listing has no per-page URL at
# all: pages 2+ are requested with a 0-based `pageIndex` inside a POST body,
# and that endpoint is behind the same Cloudflare challenge as every HTML
# route -- so it is only reachable with the cookies a cleared BROWSER SESSION
# holds. A one-shot HTML fetch has no session to carry, so this client reads
# page 1 and says so. Page 1 is still a complete, parseable page of 24: the
# raw document inlines its results.
PAGINATED_MODES = ()

DEFAULT_SEARCH_URL = f"https://{CANONICAL_HOST}/en/projects/search"


# Global on purpose, not a single find/partition -- see CLAUDE.md §8: "A
# masker that handles the first occurrence prints the password the other
# four times and looks like it is working." Same pattern the three browser
# engines use in their own _mask_credentials.
_CREDENTIALS_IN_URL_RE = re.compile(r"([a-z][a-z0-9+.\-]*://)[^\s/@]+:[^\s/@]+@",
                                    re.IGNORECASE)


def _mask_credentials(text: str) -> str:
    """Never print a username:password embedded in a ws://... or http://... URL."""
    return _CREDENTIALS_IN_URL_RE.sub(r"\1***:***@", text or "")


def _build_wait_for(args) -> Optional[str]:
    """`waitFor` must be a JSON STRING (double-encoded), per the API docs.
    Passing a nested object is silently wrong.

    Default (no flag): wait for the DOM. Not needed for a page that arrives
    as complete HTML with no hydration (this site, per the module
    docstring) -- --wait-text/--wait-element exist for the day that stops
    being true, or for a --cdp-url run through a challenge page."""
    if args.wait_text:
        return json.dumps({"text": args.wait_text})
    if args.wait_element:
        return json.dumps({"element": args.wait_element, "checkVisible": True})
    if args.wait_state:
        return json.dumps({"state": args.wait_state})
    return None


def fetch_html(args, url: str, timeout: int):
    payload = {
        "task_type": "scrape",
        "url": url,
        "data_format": "raw",   # we want HTML; product_parser does the rest
        "format": "json",       # so we get {"status", "headers", "body"}
        "timeout": min(timeout, MAX_API_TIMEOUT),
    }

    wait_for = _build_wait_for(args)
    if wait_for:
        payload["waitFor"] = wait_for
        logger.info("waitFor: %s", wait_for)

    if args.cdp_url:
        payload["cdpurl"] = args.cdp_url
        logger.info("Routing through an existing browser session: %s",
                    _mask_credentials(args.cdp_url))

    logger.info("POST %s (url=%s)", SYNC_ENDPOINT, url)
    resp = requests.post(
        SYNC_ENDPOINT,
        headers={"Authorization": f"Bearer {args.key}", "Content-Type": "application/json"},
        json=payload,
        # Give the HTTP call more headroom than the API-side task timeout,
        # otherwise a task that legitimately runs the full 120s looks like
        # a client-side network failure.
        timeout=min(timeout, MAX_API_TIMEOUT) + 30,
    )

    # The API returns its own per-task metadata (price, timings, status)
    # in an x-debug header -- worth logging, it's the only place the real
    # cost of the call shows up.
    debug = resp.headers.get("x-debug")
    if debug:
        logger.info("x-debug: %s", debug)

    if resp.status_code != 200:
        # 422 = task ran but errored (this is what a bad/unreachable
        # cdpurl produces); 402 = out of balance; 408 = sync wait exceeded.
        # This is the Scraper API's OWN call failing, not the target site --
        # see output_writer.RemoteAPIError / EXIT_REMOTE_API_ERROR, which
        # this file maps to below rather than treating it as a crash or as
        # indiegogo.com blocking a page.
        raise RuntimeError(
            f"Scraper API returned HTTP {resp.status_code}: {resp.text[:500]}"
        )

    body = resp.json()
    html = body.get("body") or ""
    upstream_status = body.get("status")
    upstream_headers = body.get("headers") or {}
    logger.info("Upstream page status %s, %d bytes of HTML.", upstream_status, len(html))
    # The STATUS and the HEADERS both travel WITH the HTML, neither thrown
    # away. The Scraper API hands back `{"status", "headers", "body"}` and
    # until v0.4.1 this function kept the first two thirds of that and
    # dropped the headers on the floor -- which meant the single most
    # decisive signal this site emits, `x-amzn-waf-action: captcha`, arrived
    # in the response and was discarded one line before the classifier that
    # needed it. See product_parser.detect_page_state, which consults that
    # header before anything else.
    waf_action = upstream_headers.get("x-amzn-waf-action") or \
        upstream_headers.get("X-Amzn-Waf-Action")
    if waf_action:
        logger.warning("AWS WAF answered this request with action=%r "
                       "(server=%r). The page body is the challenge, not the "
                       "listing.", waf_action,
                       upstream_headers.get("server")
                       or upstream_headers.get("Server"))
    return html, upstream_status, upstream_headers


def _parse_for_mode(html: str, url: str, args, page_num: int) -> List:
    """Rows for this mode, from the SAME parser the browser engines use.

    For --mode search that means the results the raw document inlines, read
    with parse_search_api -- the identical function that reads an XHR
    response, so this client and the browser engines cannot drift about what
    a campaign row means.
    """
    sort = sort_from_url(url, getattr(args, "sort", None))
    if args.mode == "search":
        result = extract_inline_search_result(html)
        if result is None:
            # Served but the payload is gone: a SITE CHANGE, not a block.
            # Named rather than reported as "0 products", which sends the
            # reader to check the URL instead of the parser.
            logger.error("The search page was served but its inlined results "
                         "payload is missing -- that is a site change. "
                         "Falling back to the rendered cards, which this "
                         "client will usually find EMPTY, because the raw "
                         "document holds zero of them until the page "
                         "hydrates in a browser.")
            return parse_search_html(html, page=page_num, sort=sort, url=url,
                                     category=category_from_url(url))
        return parse_search_api(result, page_index=page_num - 1, sort=sort,
                                url=url,
                                category=category_from_url(url)).rows
    if args.mode == "campaign":
        row = parse_campaign(html, url=url, sort=sort)
        return [row] if row is not None else []
    if args.mode == "rewards":
        return parse_rewards(html, url=url)
    raise ValueError(f"unknown mode {args.mode!r}")


def _fetch_one_page(args, page_num: int, url: str):
    """(rows, blocked_by, upstream_status, html) for one page. Never raises
    for an EXPECTED failure (a bad status, a challenge page); those come
    back as blocked_by set. A Scraper API call failure IS allowed to raise
    -- caught once in main() and mapped to EXIT_REMOTE_API_ERROR. `html` is
    returned (even on a blocked/vendor outcome, where it's the challenge
    page) so the caller can read page 1's own next-link for
    page_flow.pagination_is_addressable, the same way the browser engines
    read it off the live DOM."""
    attempts = max(1, args.retries + 1)
    html, upstream_status, upstream_headers = "", None, {}
    for attempt in range(1, attempts + 1):
        html, upstream_status, upstream_headers = fetch_html(args, url, args.timeout)

        if args.dump_html:
            dump_path = (args.dump_html if page_num == 1
                        else f"{args.dump_html}.page{page_num}")
            with open(dump_path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info("Saved the snapshot the parser sees to %s (%d bytes).",
                        dump_path, len(html))

        state = detect_page_state(html, status=upstream_status, url=url,
                                  headers=upstream_headers)
        if state == "blocked":
            # Retried like a vendor challenge, NOT returned immediately: a
            # transient upstream error (a non-2xx status, or an empty body)
            # deserves the same --retries budget every other engine in this
            # repo gives it -- playwright_scraper.py's own _fetch_one_page
            # retries a "blocked" classification (see page_flow.should_retry),
            # and this client used to be the one place in the family where
            # --retries silently did nothing for that outcome: it returned
            # on the FIRST attempt regardless of how many were requested.
            if attempt < attempts:
                logger.info("state=blocked (upstream HTTP %s) on attempt "
                            "%d/%d -- retrying in %ds.", upstream_status,
                            attempt, attempts, args.retry_delay)
                time.sleep(args.retry_delay)
                continue
            debug_html = f"{args.out}_page{page_num}_debug.html"
            with open(debug_html, "w", encoding="utf-8") as f:
                f.write(html)
            logger.error("Refused or unreachable (state=blocked, upstream "
                        "HTTP %s, %d bytes) -- saved to %s. This is exit 3, "
                        "distinct from a genuinely empty result (exit 4).",
                        upstream_status, len(html), debug_html)
            return [], "blocked", upstream_status, html

        vendor = challenge_marker(html, upstream_headers) if state != "content" else None
        if vendor:
            if attempt < attempts:
                logger.info("%s challenge page on attempt %d/%d -- retrying "
                            "in %ds.", vendor, attempt, attempts, args.retry_delay)
                time.sleep(args.retry_delay)
                continue
            debug_html = f"{args.out}_page{page_num}_debug.html"
            with open(debug_html, "w", encoding="utf-8") as f:
                f.write(html)
            logger.error("Blocked by %s before parsing (%d bytes) -- saved "
                        "to %s.", vendor, len(html), debug_html)
            return [], vendor, upstream_status, html
        break

    rows = _parse_for_mode(html, url, args, page_num)
    logger.info("Parsed %d row(s) from page %d.", len(rows), page_num)

    if rows and args.mode == "search":
        priced = sum(1 for r in rows if r.price is not None)
        cur = sum(1 for r in rows if r.currency is not None)
        logger.info("Page %d: %d/%d rows carry a funding total, %d/%d a "
                    "currency.", page_num, priced, len(rows), cur, len(rows))
        platforms = {}
        for r in rows:
            platforms[r.platform] = platforms.get(r.platform, 0) + 1
        if platforms:
            # Not a warning: indiegogo.com's own search results legitimately
            # include gamefound.com-hosted campaigns. Logged so a run that is
            # mostly off-platform is visible rather than surprising.
            logger.info("Page %d by platform: %s", page_num, platforms)

    if not rows:
        debug_html = f"{args.out}_page{page_num}_debug.html"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(html)
        logger.warning("0 rows parsed -- saved what the API returned to %s.", debug_html)

    return rows, None, upstream_status, html


def scrape(args) -> int:
    all_rows: List = []
    seen_keys = set()
    blocked = False
    stop_reason = "single_page_mode" if args.mode not in PAGINATED_MODES else "completed"
    pages_completed = 0
    pages_failed: List[int] = []
    final_url = args.url

    rows, blocked_by, _status, page1_html = _fetch_one_page(args, 1, args.url)
    if blocked_by is not None:
        stop_reason = f"blocked_{blocked_by}"
        blocked = True
        pages_failed.append(1)
    else:
        pages_completed = 1
        fresh = dedupe_by_key(rows, seen_keys, key="sku")
        all_rows.extend(fresh)

        # No pagination block here, deliberately. PAGINATED_MODES is empty
        # on this site (see its comment), so any loop over pages 2..N would
        # be code no control flow can reach -- and the version inherited
        # from a sibling repo called product_parser.page_url(), which on
        # this site RAISES by design because ?page=N silently returns page
        # 1. Keeping it would have looked like working pagination.
        if args.pages > 1:
            stop_reason = "single_page_only"

    return finish_run(all_rows, args.out, args.format, args.allow_empty,
                      blocked=blocked, stop_reason=stop_reason,
                      pages_requested=args.pages, pages_completed=pages_completed,
                      pages_failed=pages_failed, mode=args.mode,
                      sort=sort_from_url(args.url, getattr(args, "sort", None)),
                      start_url=args.url, final_url=final_url)


def main() -> int:
    args = parse_args()
    try:
        return scrape(args)
    except requests.RequestException as e:
        logger.error("Network error talking to the Scraper API: %s", e)
        return EXIT_REMOTE_API_ERROR
    except RuntimeError as e:
        # HTTP 4xx/5xx from the Scraper API itself, including the 422 a
        # busy or unreachable --cdp-url produces -- the API's own call
        # failing, not indiegogo.com blocking a page. See
        # output_writer.RemoteAPIError (this file raises plain RuntimeError
        # from fetch_html rather than that class, since this client has no
        # dependency on a browser engine to share it with -- but the exit
        # code is the same family contract value either way).
        logger.error("%s", e)
        return EXIT_REMOTE_API_ERROR


def parse_args():
    p = argparse.ArgumentParser(
        description="indiegogo.com scraper -- 2Captcha Scraper API edition "
                    "(no local browser). See this file's docstring: this "
                    "site was measured to need neither a proxy nor "
                    "--cdp-url for the plain HTTP path to work.")
    p.add_argument("--mode", choices=["search", "campaign", "rewards"],
                   default="search",
                   help="Same meaning as the browser engines -- see "
                        "playwright_scraper.py's --mode help.")
    # NOT required: prefer the TWOCAPTCHA_KEY env var. A key passed on the
    # command line is visible to anyone who can run `ps`, and it lands in
    # shell history and in any log that echoes the command line.
    p.add_argument("--key", default=None,
                   help="2captcha.com API key (sent as a Bearer token). "
                        "Falls back to $TWOCAPTCHA_KEY via .env/env_config, "
                        "which is the safer way to pass it.")
    p.add_argument("--url", default=None,
                   help="Override the URL this mode would otherwise build. "
                        "Falls back to $INDIEGOGO_URL, then to the "
                        "mode's own canonical URL.")
    p.add_argument("--pages", type=int, default=1,
                   help="Listing pages to crawl. Checked per run against "
                        "page 1's own next-link (page_flow.pagination_is_"
                        "addressable). Refused above 1 for --mode search on "
                        "this site; see the module docstring.")
    p.add_argument("--delay", type=float, default=1.0, help="Delay between pages, seconds")
    p.add_argument("--sort", choices=sorted(SORT_TYPES), default=None,
                   help="Result ordering. Recorded on every row and in the "
                        "sidecar, because it decides WHICH campaigns the "
                        "capped result set contains.")
    p.add_argument("--format", choices=["json", "csv", "both"], default="both")
    p.add_argument("--out", default="indiegogo_projects_scraperapi", help="Output file prefix")
    p.add_argument("--timeout", type=int, default=60,
                   help=f"API-side task timeout in seconds (1-{MAX_API_TIMEOUT}, default 60)")
    p.add_argument("--cdp-url", default=None,
                   help="Route the fetch through an existing browser session over CDP "
                        "(sent as the API's `cdpurl` param), e.g. ws://user:pass@host:port "
                        "-- NOT needed for this site as measured; see the module docstring.")
    wait = p.add_mutually_exclusive_group()
    wait.add_argument("--wait-text", default=None,
                      help="Wait until this string appears on the page. Only useful with "
                           "--cdp-url -- a plain fetch already returns complete HTML here.")
    wait.add_argument("--wait-element", default=None,
                      help="Wait until this CSS selector is visible, e.g. 'table.items'")
    wait.add_argument("--wait-state", choices=["load", "domcontentloaded"], default=None,
                      help="Wait for a page load state instead of specific content")
    p.add_argument("--allow-empty", action="store_true",
                   help="Write output files even when 0 rows were found.")
    p.add_argument("--retries", type=int, default=1,
                   help="Extra attempts if a bot-challenge page comes back. Each attempt "
                        "is a separate billable task, so this defaults to 1.")
    p.add_argument("--retry-delay", type=int, default=10, help="Seconds between retries")
    p.add_argument("--dump-html", default=None,
                   help="Save the exact HTML the parser is given (always, even on success)")
    args = p.parse_args()
    # This client uses --key rather than --twocaptcha-key, so the env
    # mapping is spelled out instead of defaulted -- same pattern this
    # family's mediamarkt-scraper uses for its own scraper_api_client.py.
    env_config.apply(args, keys={
        "TWOCAPTCHA_KEY": "key",
        "INDIEGOGO_CDP_ENDPOINT": "cdp_url",
        "INDIEGOGO_URL": "url",
    })

    if not args.key:
        p.error("no --key given, and TWOCAPTCHA_KEY is not set in the environment or in .env.")

    if not args.url:
        if args.mode != "search":
            p.error(f"--mode {args.mode} needs --url (a campaign page).")
        args.url = DEFAULT_SEARCH_URL

    if args.mode in ("campaign", "rewards") and not is_project_url(args.url):
        p.error(f"--mode {args.mode} needs a campaign URL "
                f"(/projects/<creator>/<slug>); got {args.url!r}")

    if args.mode not in PAGINATED_MODES and args.pages != 1:
        # Said out loud rather than silently clamped. For --mode search this
        # is the site's doing, not the API's: see the module docstring.
        if args.mode == "search":
            # NOT clamped to 1. The request is left intact so that
            # finish_run sees pages_requested=N against pages_completed=1
            # and reports `partial` (exit 6) rather than `complete` (exit
            # 0) -- a consumer that asked for three pages and got one has
            # NOT been given a complete view, and the warning alone does
            # not reach a pipeline reading the sidecar.
            logger.warning(
                "--pages %d requested, but this client can only reach page 1 "
                "of a search. indiegogo.com has no per-page URL (?page=2 "
                "returns page 1 again) and its real pagination is a POST "
                "behind the same Cloudflare challenge, which needs a browser "
                "session's cookies. This run will report `partial`. Use "
                "playwright_scraper.py for more than one page.", args.pages)
        else:
            logger.warning("--pages %d is ignored in --mode %s (one page).",
                           args.pages, args.mode)
            args.pages = 1
    return args


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
