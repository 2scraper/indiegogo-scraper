#!/usr/bin/env python3
"""
indiegogo-scraper — Selenium edition (parity engine)
======================================================

The same scraper as playwright_scraper.py, driven through Selenium. It must
agree with its twins on exit codes, run status, and whether a run crashes or
spends money; the shared decisions live in output_writer.finish_run() and
page_flow.py so they cannot drift apart, and smoke_test.py asserts the three
CLIs are flag-for-flag identical in both directions.

Two limits are Selenium's, not this repo's, and both are stated here rather
than left to be discovered:

  * **Selenium cannot use an AUTHENTICATED remote CDP endpoint.**
    Playwright's `connect_over_cdp` and Puppeteer's `browserWSEndpoint` take
    a full `ws://user:pass@host:port` and authenticate on the WebSocket
    upgrade; chromedriver's `debuggerAddress` takes a bare `host:port` with
    nowhere to put a password. So `--cdp-endpoint` against the 2Captcha
    Scraping Browser is REFUSED here rather than half-working. That matters
    more on this site than on most in the family: the Scraping Browser is
    the documented easy way past indiegogo.com's Cloudflare challenge, so
    this engine is the one that most needs a proxy or a key.
  * **Selenium's `--proxy-server` cannot authenticate at all.** Credentials
    are stripped and a warning is printed; a `user:pass` URL is not silently
    believed to be doing something.

Read playwright_scraper.py's module docstring for what is different about
indiegogo.com. The mechanical difference HERE is that `execute_script` takes
a function BODY with an explicit `return`, where Playwright and pyppeteer
take `() => expr` — which is exactly why page_flow.py names OPERATIONS
rather than passing JavaScript across its boundary.

Requires: pip install -r requirements.txt -r requirements-selenium.txt
"""

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# Imported at MODULE level on purpose — see puppeteer_scraper.py's note.
from selenium import webdriver
from selenium.common.exceptions import (TimeoutException, WebDriverException,
                                        JavascriptException)
from selenium.webdriver.chrome.options import Options as ChromeOptions

from captcha_solver import (detect_recaptcha_v3, reconcile_detections,
                            solve_recaptcha, detect_turnstile,
                            TURNSTILE_INTERCEPT_JS, TURNSTILE_INJECT_JS,
                            INJECT_TOKEN_JS)
from product_parser import (parse_search_api, parse_search_html,
                            parse_campaign, parse_rewards,
                            extract_inline_search_result, extract_search_params,
                            body_from_site_params, build_search_body,
                            sort_from_url, category_from_url, normalise_url,
                            challenge_marker, is_project_url,
                            SEARCH_API_PATH, SORT_TYPES, PAGE_SIZE,
                            CANONICAL_HOST, DEFAULT_SORT)
from output_writer import (dedupe_by_key, finish_run, RemoteAPIError,
                           EXIT_REMOTE_API_ERROR, Campaign)
import page_flow
from proxy_pool import (from_args as proxy_pool_from_args, mask, ROTATE_MODES,
                        ProxyError, ProxyPool, split_credentials)
import env_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("selenium_scraper")

DEFAULT_SEARCH_URL = f"https://{CANONICAL_HOST}/en/projects/search"
PAGINATED_MODES = ("search",)
CONCURRENCY_CAPABLE_MODES = ("search",)
NAV_TIMEOUT_S = 60

_CREDENTIALS_IN_URL_RE = re.compile(r"([a-z][a-z0-9+.\-]*://)[^\s/@]+:[^\s/@]+@",
                                    re.IGNORECASE)
_KEY_IN_QUERY_RE = re.compile(r"((?:client)?key|token|api[_-]?key)=[^&\s\"']+",
                              re.IGNORECASE)


def _mask_credentials(text: str) -> str:
    masked = _CREDENTIALS_IN_URL_RE.sub(r"\1***:***@", text or "")
    return _KEY_IN_QUERY_RE.sub(r"\1=***", masked)


@dataclass
class PageOutcome:
    page_num: int
    url: str
    final_url: Optional[str] = None
    rows: List = field(default_factory=list)
    blocked_by: Optional[str] = None
    load_failed: bool = False
    state: Optional[str] = None
    total_results: Optional[int] = None
    total_pages: Optional[int] = None
    capped: Optional[bool] = None

    @property
    def ok(self) -> bool:
        return not self.load_failed and self.blocked_by is None


_PROXY_ERROR_MARKERS = (
    "ERR_PROXY_CONNECTION_FAILED", "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_AUTH_UNSUPPORTED", "ERR_PROXY_AUTH_REQUESTED",
    "ERR_UNEXPECTED_PROXY_AUTH", "ERR_PROXY_CERTIFICATE_INVALID",
)


def _proxy_failure(exc) -> str:
    text = str(exc)
    for marker in _PROXY_ERROR_MARKERS:
        if marker in text:
            return marker
    return ""


class UsageError(RuntimeError):
    """A flag combination this engine cannot honour. Maps to exit 2."""


class SearchAPIError(RuntimeError):
    """The site's own search endpoint answered with something unusable."""


# Selenium's `execute_async_script` hands the browser a function BODY and
# appends a callback as the last argument — a different dialect from the
# `() => expr` the other two engines use. It is still not an `eval` of a
# string the page composes; the driver wraps this in a real function, so it
# behaves the same way under a CSP without `unsafe-eval`.
_SEARCH_FETCH_JS = """
var path = arguments[0], body = arguments[1], done = arguments[2];
fetch(path, {
  method: 'POST',
  headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
  body: JSON.stringify(body),
  credentials: 'include'
}).then(function (r) {
  return r.text().then(function (t) { done({status: r.status, text: t}); });
}).catch(function (e) { done({status: 0, text: String(e)}); });
"""


def _count(driver, selector: str) -> int:
    try:
        return int(driver.execute_script(
            "return document.querySelectorAll(arguments[0]).length;", selector))
    except (WebDriverException, JavascriptException) as e:
        logger.debug("count(%s) failed: %s", selector, e)
        return 0


def _wait_ready(driver, mode: str) -> str:
    selector = page_flow.ready_selector(mode)
    page_flow.wait_for_count(lambda s: _count(driver, s),
                             lambda ms: time.sleep(ms / 1000.0),
                             selector, page_flow.ready_count(mode),
                             page_flow.content_timeout_ms(mode))
    try:
        return driver.page_source or ""
    except WebDriverException as e:
        logger.debug("page_source failed: %s", e)
        return ""


def _install_turnstile_hook(driver) -> None:
    """Hook `turnstile.render` before any page script runs.

    Selenium has no `add_init_script`; the same thing is done through CDP.
    Without it a Cloudflare Challenge page cannot be solved at all, because
    its sitekey exists only inside the one render() call.
    """
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",
                               {"source": TURNSTILE_INTERCEPT_JS})
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not install the turnstile.render hook (%s). A "
                       "Cloudflare Challenge page will not be solvable in "
                       "this run.", e)


CDP_UNSUPPORTED = (
    "chromedriver's debuggerAddress takes a bare host:port with nowhere to "
    "put a password, so an authenticated Scraping Browser endpoint cannot be "
    "used from the Selenium engine. Use playwright_scraper.py or "
    "puppeteer_scraper.py for --cdp-endpoint, or give this engine --proxy.")


def _open_driver(args, pool):
    if args.cdp_endpoint:
        # Refused rather than half-working -- but only when the user
        # actually typed it. A value that merely sits in .env was not aimed
        # at this engine, and failing the run over it would make this engine
        # unusable for anyone who keeps a shared endpoint there.
        if getattr(args, "cdp_endpoint_explicit", True):
            raise UsageError("--cdp-endpoint is not supported here. " +
                             CDP_UNSUPPORTED)
        logger.warning("Ignoring INDIEGOGO_CDP_ENDPOINT from the "
                       "environment: %s", CDP_UNSUPPORTED)
        args.cdp_endpoint = None

    opts = ChromeOptions()
    if args.headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument(f"--lang={args.locale}")

    if pool:
        scrubbed, credentials = split_credentials(pool.current)
        if credentials:
            logger.warning(
                "Selenium cannot authenticate a proxy: --proxy-server takes "
                "no credentials, so %s will be used WITHOUT them and will "
                "fail if the exit requires auth. Use the Playwright or "
                "pyppeteer engine for an authenticated proxy.",
                mask(pool.current))
        opts.add_argument(f"--proxy-server={scrubbed}")
        logger.info("Using proxy exit %s", mask(pool.current))

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(NAV_TIMEOUT_S)
    try:
        ua = driver.execute_script("return navigator.userAgent;")
        if ua and "Headless" in ua:
            # The UA comes from the browser, never a literal — but the
            # headless marker in it is a giveaway worth removing.
            driver.execute_cdp_cmd("Network.setUserAgentOverride",
                                   {"userAgent": ua.replace("HeadlessChrome",
                                                            "Chrome")})
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not normalise the user agent: %s", e)

    if args.fingerprint:
        from fingerprint_client import (get_fingerprint, playwright_init_script,
                                        fingerprint_user_agent)
        fp = get_fingerprint(args.twocaptcha_key, tags=args.fp_tags,
                             country=args.fp_country)
        ua = fingerprint_user_agent(fp)
        if ua:
            driver.execute_cdp_cmd("Network.setUserAgentOverride",
                                   {"userAgent": ua})
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",
                               {"source": playwright_init_script(fp)})
        logger.info("Using 2captcha fingerprint %s (%s)", fp.get("id"),
                    fp.get("country"))

    _install_turnstile_hook(driver)
    return driver


def fetch_search_page(driver, body: Dict[str, Any]) -> Dict[str, Any]:
    """POST one page of search results from inside the current session."""
    try:
        driver.set_script_timeout(45)
        res = driver.execute_async_script(_SEARCH_FETCH_JS, SEARCH_API_PATH, body)
    except (WebDriverException, JavascriptException) as e:
        raise SearchAPIError(f"search API call failed in-page: {e}") from None
    status = (res or {}).get("status")
    text = (res or {}).get("text") or ""
    if status != 200:
        marker = challenge_marker(text)
        raise SearchAPIError(
            f"search API answered HTTP {status}"
            + (f" with a challenge marker ({marker})" if marker else "")
            + f" for pageIndex={body.get('pageIndex')}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        marker = challenge_marker(text)
        raise SearchAPIError(
            "search API answered HTTP 200 but not JSON"
            + (f"; it looks like a challenge ({marker})" if marker else "")
        ) from None


def handle_captcha_if_present(driver, args) -> bool:
    """Detect and solve a challenge. True if something was solved."""
    try:
        html = driver.page_source or ""
    except WebDriverException:
        return False
    if not html:
        return False
    mode = getattr(args, "mode", "search")
    already = _count(driver, page_flow.ready_selector(mode))
    when_blocked = getattr(args, "solve_captcha", "when-blocked") == "when-blocked"
    current_url = driver.current_url

    challenge = detect_recaptcha_v3(html, current_url) or detect_turnstile(
        html, current_url)
    if challenge and getattr(challenge, "is_turnstile", False) and not challenge.sitekey:
        captured = None
        for _ in range(12):
            try:
                captured = driver.execute_script("return window.__tsParams;")
            except (WebDriverException, JavascriptException):
                captured = None
            if captured and captured.get("sitekey"):
                break
            time.sleep(0.5)
        if captured and captured.get("sitekey"):
            challenge.sitekey = captured["sitekey"]
            challenge.action = captured.get("action")
            challenge.cdata = captured.get("cData")
            challenge.chl_page_data = captured.get("chlPageData")
        else:
            logger.warning(
                "A Cloudflare Turnstile is on this page but no sitekey was "
                "captured, so it cannot be solved. Raising rather than "
                "building a task the API would reject.")
            return False
    if not challenge:
        return False

    if when_blocked and already > page_flow.min_matches(mode):
        logger.info("%s detected, but %d anchors are already on the page — "
                    "not solving it.", challenge.kind, already)
        return False
    if not args.twocaptcha_key:
        logger.warning("No 2captcha API key, so this challenge cannot be "
                       "solved — continuing with whatever the page holds.")
        return False
    try:
        token = solve_recaptcha(challenge, args.twocaptcha_key,
                                api_version=args.captcha_api,
                                min_score=args.min_score)
    except Exception as e:  # noqa: BLE001 — a solver failure is not a crash
        logger.error("Solving the challenge failed (%s) — continuing.",
                     _mask_credentials(str(e)))
        return False

    try:
        if getattr(challenge, "is_turnstile", False):
            driver.execute_script(f"return ({TURNSTILE_INJECT_JS})(arguments[0]);",
                                  token)
        else:
            driver.execute_script(f"return ({INJECT_TOKEN_JS})(arguments[0]);",
                                  token)
    except (WebDriverException, JavascriptException) as e:
        logger.warning("Injecting the token failed (%s).", e)
        return False
    time.sleep(1.5)
    try:
        driver.refresh()
    except WebDriverException as e:
        logger.warning("Reload after the solve failed (%s).", e)
    return True


def _dump(args, html: str, tag: str) -> None:
    if not args.dump_html or not html:
        return
    path = f"{args.out}_{tag}_debug.html"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Wrote %s (%d bytes)", path, len(html))
    except OSError as e:
        logger.warning("Could not write %s: %s", path, e)


def scrape(args) -> int:
    pool = proxy_pool_from_args(args)
    if pool and args.cdp_endpoint:
        logger.error("--proxy/--proxy-file cannot be combined with "
                     "--cdp-endpoint: the Scraping Browser already proxies.")
        return 2
    warning = page_flow.concurrency_warning(args.concurrency, pool is not None,
                                            args.cdp_endpoint)
    if warning:
        if args.cdp_endpoint:
            logger.error(warning)
            return 2
        logger.warning(warning)
    if args.concurrency > 1 and args.mode not in CONCURRENCY_CAPABLE_MODES:
        logger.error("--concurrency > 1 is not available for --mode %s.", args.mode)
        return 2

    sort = sort_from_url(args.url, args.sort)
    category = args.category or category_from_url(args.url)
    rows: List = []
    pages_failed: List[int] = []
    pages_completed = 0
    stop_reason = "completed"
    blocked = False
    total_results = total_pages = None
    capped = None
    final_url = args.url
    driver = None

    try:
        driver = _open_driver(args, pool)
        html = ""
        state = "blocked"
        for attempt in range(1, args.retries + 2):
            try:
                driver.get(args.url)
            except (TimeoutException, WebDriverException) as e:
                marker = _proxy_failure(e)
                logger.warning("Navigation failed (attempt %d): %s", attempt,
                               _mask_credentials(str(e)))
                if marker and pool:
                    try:
                        pool.advance(f"proxy failure: {marker}")
                    except ProxyError as pe:
                        raise RemoteAPIError(str(pe)) from None
                time.sleep(args.retry_delay)
                continue
            html = _wait_ready(driver, args.mode)
            # Selenium has no response object, so neither the status nor the
            # `cf-mitigated` header is available here — the body markers are
            # the only signal this engine gets. That asymmetry is why
            # product_parser's marker set has to stand on its own.
            state = page_flow.classify(html, url=driver.current_url,
                                       mode=args.mode)
            logger.info("Page 1 (%s): state=%s, %d bytes", args.mode, state,
                        len(html))
            if state == "content":
                break
            if page_flow.should_solve(state):
                if handle_captcha_if_present(driver, args):
                    html = _wait_ready(driver, args.mode)
                    state = page_flow.classify(html, url=driver.current_url,
                                               mode=args.mode)
                    if state == "content":
                        break
            if not page_flow.should_retry(state):
                break
            time.sleep(args.retry_delay)

        final_url = driver.current_url
        if state != "content":
            _dump(args, html, f"page1_{state}")
            blocked = page_flow.counts_as_blocked(state)
            stop_reason = {"empty": "no_results", "not_found": "not_found",
                           "captcha": "blocked_by_captcha", "blocked": "blocked",
                           "unpainted": "never_painted"}.get(state, state)
            return finish_run([], args.out, args.format, args.allow_empty,
                              blocked=blocked, stop_reason=stop_reason,
                              pages_requested=args.pages, pages_completed=0,
                              start_url=args.url, final_url=final_url,
                              pages_failed=[1], mode=args.mode, sort=sort)
        _dump(args, html, "page1")

        if args.mode == "campaign":
            row = parse_campaign(html, url=final_url, sort=sort)
            if row is None:
                logger.error("No campaign state on %s.", final_url)
                stop_reason = "parser_found_nothing"
            else:
                rows = [row]
                pages_completed = 1
                stop_reason = "single_page_mode"
        elif args.mode == "rewards":
            rows = parse_rewards(html, url=final_url)
            pages_completed = 1
            stop_reason = "single_page_mode"
        else:
            result = extract_inline_search_result(html)
            if result is None:
                logger.error("The search page was served but its inlined "
                             "results payload is missing — a SITE CHANGE, "
                             "not a block.")
                dom_rows = parse_search_html(html, page=1, sort=sort,
                                             url=args.url, category=category)
                if dom_rows:
                    rows = dom_rows
                    pages_completed = 1
                    stop_reason = "payload_missing_dom_fallback"
                else:
                    stop_reason = "parser_found_nothing"
            else:
                sp = parse_search_api(result, page_index=0, sort=sort,
                                      url=args.url, category=category)
                rows = list(sp.rows)
                total_results, total_pages, capped = (
                    sp.total_results, sp.total_pages, sp.capped)
                pages_completed = 1
                logger.info("Page 1: %d rows. Site reports %s results across "
                            "%s pages (capped=%s).", len(sp.rows),
                            total_results, total_pages, capped)
                site_params = extract_search_params(html)
                body_template = (body_from_site_params(site_params, 0, sort)
                                 if site_params
                                 else build_search_body(args.url, 0, sort))
                wanted = page_flow.plan_page_indices(args.pages, total_pages)[1:]
                if args.pages > 1 and not wanted:
                    stop_reason = "site_result_cap" if capped else "pagination_exhausted"
                seen_ids = {r.sku for r in rows if r.sku}
                for idx in wanted:
                    body = dict(body_template)
                    body["pageIndex"] = idx
                    try:
                        payload = fetch_search_page(driver, body)
                    except SearchAPIError as e:
                        logger.warning("pageIndex %d: %s", idx, e)
                        pages_failed.append(idx + 1)
                        stop_reason = "page_failed"
                        break
                    sp = parse_search_api(payload, page_index=idx, sort=sort,
                                          url=args.url, category=category)
                    if not sp.rows:
                        stop_reason = "site_result_cap" if capped else "pagination_exhausted"
                        break
                    fresh = [r for r in sp.rows if r.sku and r.sku not in seen_ids]
                    if not fresh:
                        stop_reason = "no_new_products"
                        break
                    seen_ids.update(r.sku for r in fresh if r.sku)
                    rows.extend(sp.rows)
                    pages_completed += 1
                    if args.delay:
                        time.sleep(args.delay)
                else:
                    if pages_completed >= args.pages:
                        stop_reason = "completed"
    except UsageError as e:
        logger.error("%s", e)
        return 2
    except RemoteAPIError as e:
        logger.error("%s", _mask_credentials(str(e)))
        return EXIT_REMOTE_API_ERROR
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception as e:  # noqa: BLE001
                logger.debug("Ignoring error during teardown: %s", e)

    rows = dedupe_by_key(rows, set(), key="sku")
    before = len(rows)
    if args.platform and args.platform != "all":
        rows = [r for r in rows if getattr(r, "platform", None) == args.platform]
        if len(rows) != before:
            logger.info("--platform %s kept %d of %d rows.", args.platform,
                        len(rows), before)
    for i, r in enumerate(rows, start=1):
        r.position = i

    return finish_run(rows, args.out, args.format, args.allow_empty,
                      blocked=blocked, stop_reason=stop_reason,
                      pages_requested=args.pages, pages_completed=pages_completed,
                      start_url=args.url, final_url=final_url,
                      pages_failed=pages_failed, mode=args.mode, sort=sort,
                      total_results=total_results, pages_available=total_pages,
                      capped_by_site=capped,
                      reachable_max=page_flow.reachable_max(total_pages))
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Scrape indiegogo.com project listings, campaigns and rewards.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage\n-----")[-1] if "Usage\n-----" in __doc__ else None)

    p.add_argument("--mode", choices=("search", "campaign", "rewards"),
                   default="search",
                   help="search: the project search listing (default). "
                        "campaign: one campaign page. "
                        "rewards: the perks on one campaign.")
    # default=None on purpose: env_config.apply() only fills a destination
    # that is still falsy, so an argparse default here would make
    # INDIEGOGO_URL silently inert. The fallback is applied below, after
    # apply() has had its turn.
    p.add_argument("--url", default=None,
                   help="For --mode search, any /projects/search URL with "
                        "the site's own filters on it "
                        "(?term=, ?projectCatalogCategories=, ?projectTags=). "
                        "For --mode campaign/rewards, a campaign URL. "
                        f"Default: {DEFAULT_SEARCH_URL}")
    p.add_argument("--pages", type=int, default=1,
                   help="How many pages of 24 to fetch (--mode search only). "
                        "The site caps every query, and reports its own "
                        "ceiling; asking for more than it will serve stops "
                        "cleanly rather than fetching empty pages.")
    p.add_argument("--sort", choices=sorted(SORT_TYPES), default=None,
                   help="Result ordering. Defaults to the site's own "
                        "('default'). NOT a cosmetic choice: the site serves "
                        "at most 24 rows a page and caps every query, so the "
                        "sort decides WHICH campaigns end up in the file. It "
                        "is recorded on every row and in the sidecar, and "
                        "diff_runs.py refuses to compare two runs that "
                        "disagree about it.")
    p.add_argument("--platform", choices=("all", "indiegogo", "gamefound"),
                   default="all",
                   help="indiegogo.com's search results include campaigns "
                        "hosted on gamefound.com (110 of 432 sampled rows). "
                        "They are labelled in the `platform` column; this "
                        "filters which are WRITTEN. The site has no platform "
                        "filter of its own, so this is applied after "
                        "fetching and the sidecar's page counts still "
                        "describe what was fetched.")
    p.add_argument("--category", default=None,
                   help="Override the category recorded on each row. By "
                        "default it is read from the URL's own "
                        "?projectCatalogCategories= parameter, or from each "
                        "campaign's own catalogue category.")
    p.add_argument("--locale", default="en-US",
                   help="Browser locale. The site also has a locale PATH "
                        "prefix (/en/, /de/, ... nine of them); that is part "
                        "of --url, not this.")

    p.add_argument("--format", choices=("json", "csv", "both"), default="json")
    p.add_argument("--out", default="indiegogo_projects",
                   help="Output prefix. Writes <prefix>.json/.csv and "
                        "<prefix>.meta.json.")
    p.add_argument("--allow-empty", action="store_true",
                   help="Write an empty result instead of refusing to "
                        "overwrite an earlier good one.")
    p.add_argument("--dump-html", action="store_true",
                   help="Write the page snapshot, on success as well as on "
                        "failure.")

    p.add_argument("--delay", type=float, default=0.0,
                   help="Seconds to wait between pages.")
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--retry-delay", type=float, default=3.0)
    p.add_argument("--concurrency", type=int, default=1,
                   help="Workers for --mode search. Each owns its own "
                        "browser AND has to clear Cloudflare separately, so "
                        "raising this without a proxy pool sends N times the "
                        "traffic from one address. Refused with "
                        "--cdp-endpoint (one live connection per profile).")

    p.add_argument("--proxy", default=None,
                   help="One proxy URL. Credentials go through the driver's "
                        "own fields, never a command line.")
    p.add_argument("--proxy-file", default=None,
                   help="A file of proxy URLs, one per line.")
    p.add_argument("--proxy-rotate", choices=list(ROTATE_MODES),
                   default="per-run")
    p.add_argument("--proxy-shuffle", action="store_true")
    p.add_argument("--proxy-block-retries", type=int, default=2)

    p.add_argument("--twocaptcha-key", default=None,
                   help="2Captcha API key. Read from TWOCAPTCHA_KEY in .env "
                        "if not given; never pass a secret on a command line.")
    p.add_argument("--captcha-api", choices=("v2", "v1"), default="v2")
    p.add_argument("--solve-captcha", choices=("when-blocked", "always", "never"),
                   default="when-blocked",
                   help="when-blocked (default) only pays when the page is "
                        "actually gated.")
    p.add_argument("--min-score", type=float, default=0.7)
    p.add_argument("--no-autosolve", action="store_true",
                   help="Do NOT enable the Scraping Browser's own "
                        "Captcha.setAutoSolve. A measurement mode: it is the "
                        "only way to observe how often a challenge clears by "
                        "itself, which is the control any claim about a "
                        "solve needs. Not a way to run a scrape.")

    p.add_argument("--cdp-endpoint", default=None,
                   help="Connect to an existing browser over CDP (the "
                        "2Captcha Scraping Browser API). Profile "
                        "credentials expire after about a day.")
    p.add_argument("--fingerprint", action="store_true",
                   help="Fetch a device fingerprint from the 2Captcha "
                        "Fingerprint API and apply it. Ignored with "
                        "--cdp-endpoint, which brings its own.")
    p.add_argument("--fp-country", default=None)
    p.add_argument("--fp-tags", default="Windows",
                   help="ONE OS-family tag. The API rejects a list, and "
                        "rejects 'Chrome' and 'Desktop' outright.")

    headless = p.add_mutually_exclusive_group()
    headless.add_argument("--headless", dest="headless", action="store_true",
                          default=True)
    headless.add_argument("--headful", dest="headless", action="store_false")

    args = p.parse_args(argv)
    # Recorded BEFORE the loader runs, so an engine can tell a flag the user
    # typed from a value that merely sits in their .env. The Selenium engine
    # cannot use a CDP endpoint at all, and a shared INDIEGOGO_CDP_ENDPOINT
    # would otherwise make that engine unrunnable for anyone who has one --
    # refusing a run over a variable the user did not point at this engine
    # is the wrong answer.
    explicit_cdp = args.cdp_endpoint is not None
    env_config.apply(args)
    args.cdp_endpoint_explicit = explicit_cdp
    args.cdp_endpoint_from_env = bool(args.cdp_endpoint) and not explicit_cdp
    if not args.url:
        args.url = DEFAULT_SEARCH_URL

    if args.mode in ("campaign", "rewards"):
        if not is_project_url(args.url):
            p.error(f"--mode {args.mode} needs a campaign URL "
                    f"(/projects/<creator>/<slug>); got {args.url!r}")
        if args.pages != 1:
            logger.info("--pages is ignored for --mode %s (one page).", args.mode)
            args.pages = 1
    if args.mode == "rewards" and "#" not in args.url:
        # The rewards live on the campaign's own rewards section; the site
        # routes it with a fragment, which it reads client-side.
        args.url = args.url.rstrip("/") + "#/section/rewards"
    if args.pages < 1:
        p.error("--pages must be at least 1")
    if args.concurrency < 1:
        p.error("--concurrency must be at least 1")
    if args.fingerprint and args.cdp_endpoint:
        logger.warning("--fingerprint is ignored with --cdp-endpoint: the "
                       "remote browser brings its own, and stacking a second "
                       "creates a contradiction rather than better cover.")
        args.fingerprint = False
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return scrape(args)
    except ProxyError as e:
        # A malformed --proxy/--proxy-file entry is BAD USAGE (exit 2), not a
        # crash (exit 1). proxy_pool validates the value and raises with a
        # message naming what is wrong -- but nothing caught it here, so the
        # most likely first-run mistake (pasting a proxy-LIST line, which is
        # host:port:login:password, where a URL belongs) reached the
        # interpreter as a traceback and exited 1. proxy_pool's own comment
        # says this should be exit 2; now it is.
        logger.error("%s", e)
        return 2
    except RemoteAPIError as e:
        logger.error("%s", _mask_credentials(str(e)))
        return EXIT_REMOTE_API_ERROR
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
