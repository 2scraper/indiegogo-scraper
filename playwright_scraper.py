#!/usr/bin/env python3
"""
indiegogo-scraper — Playwright edition (primary engine)
=========================================================

Scrapes indiegogo.com's project search, individual campaign pages, and the
rewards (perks) offered on a campaign.

    --mode search     the project search listing. The default. Paginated by
                      a 0-based `pageIndex` in a POST body, NOT by a URL —
                      see page_flow.py. Give any real
                      /projects/search URL with whatever filters you want.
    --mode campaign   one campaign page -> one row, with the campaign's ISO
                      currency, goal, dates and stretch-goal count.
                      Give --url.
    --mode rewards    the perks on one campaign -> one row each, with prices,
                      stock and estimated delivery. Give --url.

Three engines ship in this repo and they must agree on exit codes, run
status, and whether a run crashes or spends money; the shared decisions live
in output_writer.finish_run() and page_flow.py so they cannot drift apart.

What is different about indiegogo.com
--------------------------------------
* **Every HTML route is behind a Cloudflare managed challenge**, from a
  datacentre address, including `robots.txt` and the site's own JSON API
  (measured 2026-09-17 from a Hetzner exit: HTTP 403, `cf-mitigated:
  challenge`, on eight routes out of eight). Unlike bbb-scraper there is no
  ungated endpoint to fall back to, so a browser is not a convenience here —
  it is the only way in. What clears it, in order of what this repo
  recommends: the Scraping Browser's own auto-solve, then the solver API
  (`TurnstileTaskProxyless`), then a residential exit.
* **Zero JSON-LD anywhere.** Extraction reads the site's OWN JSON instead —
  see product_parser.py's docstring.
* **The search grid is NOT server-rendered.** The raw document holds zero
  cards; the 24 you see are hydrated client-side. But page 1's RESULTS are
  inlined in that same raw document as a Vue component prop, in the exact
  shape the API returns for pages 2+, so this engine reads data rather than
  waiting for a paint.
* **`?page=2` silently returns page 1.** Read page_flow.py's docstring
  before changing anything about pagination. `product_parser.page_url()`
  raises on purpose.
* **The search results are not all Indiegogo campaigns.** 110 of 432
  sampled rows were hosted on gamefound.com. They are real results that
  indiegogo.com serves; they are labelled in the `platform` column, and
  `--platform indiegogo` filters to the rest.

Usage
-----
    python playwright_scraper.py --pages 3

    python playwright_scraper.py --mode search --sort most-funded --pages 5 \\
        --url 'https://www.indiegogo.com/en/projects/search?projectCatalogCategories=BoardAndCardGames'

    python playwright_scraper.py --mode campaign \\
        --url https://www.indiegogo.com/en/projects/gpdhk/gpd-win-max-3-handheld-gaming-laptop

    python playwright_scraper.py --mode rewards --url <campaign url>

Requires: pip install -r requirements.txt -r requirements-playwright.txt
          then: playwright install chromium   (only if NOT using --cdp-endpoint)
"""

import argparse
import json
import logging
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from playwright.sync_api import (sync_playwright, Error as PWError,
                                 TimeoutError as PWTimeout)

from captcha_solver import (detect_recaptcha_v3, detect_recaptcha_in_page,
                            reconcile_detections, solve_recaptcha,
                            detect_turnstile, detect_turnstile_in_page,
                            wait_for_turnstile, TURNSTILE_INTERCEPT_JS,
                            TURNSTILE_INJECT_JS, INJECT_TOKEN_JS,
                            CaptchaUnsolvable)
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
from proxy_pool import (from_args as proxy_pool_from_args, to_playwright, mask,
                        ROTATE_MODES, ProxyError, ProxyPool)
import env_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("playwright_scraper")

DEFAULT_SEARCH_URL = f"https://{CANONICAL_HOST}/en/projects/search"

# Modes with more than one page. `campaign` and `rewards` read exactly one.
PAGINATED_MODES = ("search",)
# Modes whose pages can be requested independently — see
# page_flow.pages_are_independently_addressable.
CONCURRENCY_CAPABLE_MODES = ("search",)


def _chrome_ua(chromium_version: str) -> str:
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{chromium_version} Safari/537.36")


@dataclass
class PageOutcome:
    """What one page produced. Collected per page, merged afterwards in page
    order — merging afterwards rather than folding into shared state during
    the loop is what keeps output deterministic under concurrency."""
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


def _driver(page):
    def count(selector):
        # Swallowed deliberately: page_flow.wait_for_count polls this every
        # 250ms while the page may be navigating, and a transient error from
        # one poll means "nothing there yet", not a failed run.
        try:
            return len(page.query_selector_all(selector))
        except (PWError, PWTimeout) as e:
            logger.debug("count(%s) failed: %s", selector, e)
            return 0

    return {
        "count": count,
        "sleep": page.wait_for_timeout,
        "content": lambda: _content_when_settled(page),
        "current_url": lambda: page.url,
    }


def _classify(page, html: str, status=None, headers=None, mode="search") -> str:
    return page_flow.classify(html, status=status, url=page.url,
                              headers=headers, mode=mode)


# Chromium's own names for "the proxy is the problem, not the site". They
# want the opposite response to a timeout: a timeout deserves another try at
# the same exit, a dead proxy a different one.
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


_CREDENTIALS_IN_URL_RE = re.compile(r"([a-z][a-z0-9+.\-]*://)[^\s/@]+:[^\s/@]+@",
                                    re.IGNORECASE)
# A key passed as a query parameter leaks through the text of almost every
# library exception. Masked GLOBALLY, not once: a masker that handles the
# first occurrence prints the secret the other four times and looks like it
# is working.
_KEY_IN_QUERY_RE = re.compile(r"((?:client)?key|token|api[_-]?key)=[^&\s\"']+",
                              re.IGNORECASE)


def _mask_credentials(text: str) -> str:
    masked = _CREDENTIALS_IN_URL_RE.sub(r"\1***:***@", text or "")
    return _KEY_IN_QUERY_RE.sub(r"\1=***", masked)


def _content_when_settled(page, attempts: int = 4, pause_ms: int = 700):
    for attempt in range(1, attempts + 1):
        try:
            return page.content()
        except PWError as e:
            if "navigating" not in str(e).lower():
                raise
            if attempt == attempts:
                logger.warning("Page kept navigating through %d attempts — "
                               "continuing without a snapshot.", attempts)
                return None
            page.wait_for_timeout(pause_ms)
    return None


def _launch_local(pw, args, pool):
    launch_kwargs = {"headless": args.headless}
    proxy = to_playwright(pool.current) if pool else None
    if proxy:
        # Through Playwright's own proxy fields, never --proxy-server=: a
        # credential on a browser command line is readable by anything that
        # can run `ps`.
        launch_kwargs["proxy"] = proxy
        logger.info("Using proxy exit %s", mask(pool.current))

    browser = pw.chromium.launch(**launch_kwargs)
    ctx_kwargs = {"user_agent": _chrome_ua(browser.version), "locale": args.locale}
    init_script = None
    if args.fingerprint:
        from fingerprint_client import (get_fingerprint,
                                        playwright_context_kwargs,
                                        playwright_init_script)
        fp = get_fingerprint(args.twocaptcha_key,
                             tags=args.fp_tags, country=args.fp_country)
        ctx_kwargs.update(playwright_context_kwargs(fp))
        init_script = playwright_init_script(fp)
        logger.info("Using 2captcha fingerprint %s (%s)", fp.get("id"), fp.get("country"))

    context = browser.new_context(**ctx_kwargs)
    if init_script:
        context.add_init_script(init_script)
    _install_turnstile_hook(context)
    return browser, context, context.new_page()


def _install_turnstile_hook(context) -> None:
    """Hook `turnstile.render` before any page script runs.

    A Cloudflare Challenge page publishes no sitekey in its markup —
    `sitekey`, `action`, `cData` and `chlPageData` exist only inside the one
    `turnstile.render(container, params)` call Cloudflare makes and then
    forgets. `TurnstileTaskProxyless` needs all four, so without this a
    solve on this site is not merely harder, it is impossible to REQUEST.

    Installed on the CONTEXT rather than the page so it survives the
    navigations a challenge does on its way through.
    """
    try:
        context.add_init_script(TURNSTILE_INTERCEPT_JS)
    except (PWError, PWTimeout) as e:
        logger.warning("Could not install the turnstile.render hook (%s). A "
                       "Cloudflare Challenge page will not be solvable in "
                       "this run.", e)


class _BrowserSession:
    """One browser + context + page, relaunchable onto a different exit.

    A rotation means tearing the whole browser down: cookies a bot manager
    issued against exit A and replayed from exit B are a stronger signal
    than either address alone.
    """

    def __init__(self, pw, args, pool, remote: bool = False):
        self.pw, self.args, self.pool, self.remote = pw, args, pool, remote
        self.browser = self.context = self.page = None

    def open(self):
        if self.remote:
            self.browser, self.context, self.page = _connect_remote(self.pw, self.args)
        else:
            self.browser, self.context, self.page = _launch_local(
                self.pw, self.args, self.pool)
        return self

    def relaunch(self):
        if self.remote:
            return
        try:
            self.browser.close()
        except Exception as e:  # noqa: BLE001
            logger.debug("Ignoring error while closing browser for rotation: %s", e)
        self.open()

    def close(self):
        try:
            if self.remote:
                self.page.close()
            else:
                self.browser.close()
        except Exception as e:  # noqa: BLE001
            logger.debug("Ignoring error during browser teardown: %s", e)


def _connect_remote(pw, args):
    logger.info("Connecting to existing browser over CDP: %s",
                _mask_credentials(args.cdp_endpoint))
    try:
        browser = pw.chromium.connect_over_cdp(args.cdp_endpoint, timeout=30000)
    except (PWError, PWTimeout) as e:
        # A RemoteAPIError, not a re-raised PWError: this is the Scraping
        # Browser API's OWN connection refusing us, not the target site, and
        # it needs to be exit 5 rather than falling through to an uncaught
        # crash (1).
        raise RemoteAPIError(
            f"could not connect to --cdp-endpoint "
            f"{_mask_credentials(args.cdp_endpoint)}: "
            f"{_mask_credentials(str(e))}\n"
            f"A Scraping Browser profile allows ONE live connection at a "
            f"time, so a 500 here usually means another run still holds this "
            f"`pid`. Wait for it to finish, or use a different pid. "
            f"Profile credentials also expire after about a day."
        ) from None
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    _install_turnstile_hook(context)
    page = context.new_page()

    # Counters, not just log lines: the auto-solver is the PRIMARY path over
    # this endpoint and the solver API is the fallback, so the fallback needs
    # somewhere to read whether the primary has had its turn.
    autosolve = {"enabled": False, "detected": 0, "finished": 0, "failed": 0}
    page.autosolve = autosolve
    if getattr(args, "no_autosolve", False):
        # Deliberately NOT enabling it. This exists so a challenge can be met
        # and left unsolved, which is the only way to measure how often one
        # clears on its own — the control CLAUDE.md requires before a solve
        # may be credited with anything.
        logger.warning("--no-autosolve: Captcha.setAutoSolve NOT enabled. "
                       "Challenges will be left unsolved. This is a "
                       "measurement mode, not a way to run a scrape.")
        return browser, context, page
    try:
        cdp_session = context.new_cdp_session(page)
        cdp_session.send("Captcha.setAutoSolve",
                         {"autoSolve": True, "options": [{"type": "*"}]})

        def _on(event, level):
            def handler(*_):
                autosolve[event] += 1
                level("[Scraping Browser] CAPTCHA %s (%d).", event, autosolve[event])
            return handler

        cdp_session.on("Captcha.detected", _on("detected", logger.info))
        cdp_session.on("Captcha.solveFinished", _on("finished", logger.info))
        cdp_session.on("Captcha.solveFailed", _on("failed", logger.warning))
        autosolve["enabled"] = True
        logger.info("Scraping Browser API Captcha.setAutoSolve enabled.")
    except Exception as e:  # noqa: BLE001
        logger.info("Captcha.setAutoSolve not available on this --cdp-endpoint (%s).", e)
    return browser, context, page


# How long to let the Scraping Browser's own auto-solver work before the paid
# solver API is offered the challenge. Generous on purpose: the cost of
# waiting too long is latency, the cost of waiting too little is money plus a
# reload that moves the page out from under the primary path.
AUTOSOLVE_WAIT_MS = 180_000
AUTOSOLVE_POLL_MS = 500


def wait_for_autosolve(page, ready_selector: str) -> bool:
    """Give the Scraping Browser's auto-solver its turn. True if it cleared.

    Returns False when there is no auto-solver, when it reports failure, or
    when the budget runs out — in all three cases the caller falls back to
    the solver API, which is what the fallback is for.
    """
    state = getattr(page, "autosolve", None)
    if not state or not state.get("enabled"):
        return False
    waited = 0
    logger.info("Waiting up to %.0fs for the Scraping Browser's own auto-solve "
                "before offering this to the solver API.", AUTOSOLVE_WAIT_MS / 1000)
    while waited < AUTOSOLVE_WAIT_MS:
        if state["finished"]:
            logger.info("Auto-solve reported solveFinished after %.1fs — the "
                        "primary path cleared it, nothing was charged to the "
                        "solver API.", waited / 1000)
            return True
        if state["failed"]:
            logger.warning("Auto-solve reported solveFailed after %.1fs — "
                           "falling back to the solver API.", waited / 1000)
            return False
        try:
            page.wait_for_timeout(AUTOSOLVE_POLL_MS)
        except Exception:  # noqa: BLE001 -- a navigating page is not a failure
            time.sleep(AUTOSOLVE_POLL_MS / 1000)
        waited += AUTOSOLVE_POLL_MS
    logger.info("Auto-solve did not report solveFinished within %.0fs "
                "(detected=%d) — falling back to the solver API.",
                AUTOSOLVE_WAIT_MS / 1000, state["detected"])
    return False


def handle_captcha_if_present(page, args, ready_selector: str,
                              proxy=None) -> bool:
    """Detect and solve a challenge. True if something was solved.

    On this site the challenge that matters is a Cloudflare Turnstile, and
    the RUNTIME reading is the one that matters: a Cloudflare Challenge page
    publishes no sitekey in its markup, so only the interception installed
    at context creation can produce a solvable challenge. The static read is
    kept as the fallback for a standalone widget, whose sitekey IS in the
    markup, and the reCAPTCHA detectors stay wired because detection should
    not be narrowed to what has been seen so far.
    """
    html = _content_when_settled(page)
    if html is None:
        return False

    # Detected is not the same as blocking. A challenge on a page whose
    # results are already rendered guards nothing, and counting anchors is
    # instant — which is why this check sits here rather than after the
    # readiness wait. The other way round would cost 20 wasted seconds on a
    # page the captcha genuinely gates, where solving FIRST is what makes
    # the content appear.
    already_rendered = len(page.query_selector_all(ready_selector))
    when_blocked = getattr(args, "solve_captcha", "when-blocked") == "when-blocked"
    mode = getattr(args, "mode", "search")

    # PRIMARY path first. Over the Scraping Browser API the browser's own
    # extension is the documented default way to clear a challenge; the
    # solver API behind it is the fallback. Firing the paid call on
    # detection means the fallback always wins the race and the primary is
    # never exercised.
    if _looks_like_challenge(html, page) and wait_for_autosolve(page, ready_selector):
        d = _driver(page)
        seen = page_flow.wait_for_count(
            d["count"], d["sleep"], ready_selector,
            page_flow.ready_count(mode), page_flow.content_timeout_ms(mode))
        if seen:
            logger.info("Page painted %d match(es) after the auto-solve.", seen)
        else:
            # Do NOT reload unconditionally after a solve: the auto-solver
            # navigates the page itself once it has the token, and a reload
            # issued alongside that lands on `net::ERR_ABORTED; maybe frame
            # was detached?`, leaving the run to parse a detached frame.
            logger.info("Nothing painted after the auto-solve; reloading once.")
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)
            except (PWTimeout, PWError) as e:
                logger.warning("Reload after auto-solve failed (%s) — "
                               "continuing with whatever the page holds.", e)
        return True

    html_challenge = detect_recaptcha_v3(html, page.url)
    runtime_challenge = detect_recaptcha_in_page(
        lambda js: page.evaluate(js), page_url=page.url)
    challenge = reconcile_detections(html_challenge, runtime_challenge)
    if not challenge:
        challenge = (wait_for_turnstile(lambda js: page.evaluate(js),
                                        lambda s: page.wait_for_timeout(s * 1000),
                                        page_url=page.url)
                     or detect_turnstile(html, page.url))
        if challenge and not challenge.sitekey:
            # Raising a task from a sitekey-less detection would buy a
            # request the API rejects. Reporting the page unsolved is the
            # cheaper wrong answer.
            logger.warning(
                "A Cloudflare Turnstile is on this page but no sitekey was "
                "captured, so it cannot be solved. The page rendered its "
                "widget before this run's interception script was installed "
                "— which should not happen on a page this engine navigated "
                "to, and does happen if the browser was attached to "
                "mid-flight.")
            return False
    if not challenge:
        return False

    if when_blocked and already_rendered > page_flow.min_matches(mode):
        logger.info("%s detected via %s, but %d anchors are already on the "
                    "page — not solving it. Pass --solve-captcha always to "
                    "solve it anyway.", challenge.kind, challenge.source,
                    already_rendered)
        return False

    logger.warning("%s detected via %s (sitekey=%s, action=%s) — attempting to solve.",
                   challenge.kind, challenge.source, challenge.sitekey,
                   getattr(challenge, "action", None))
    if not args.twocaptcha_key:
        # A missing key is a WARNING, never a crash: the run continues and
        # reports exit 3 if it really was blocked.
        logger.warning("No 2captcha API key, so this challenge cannot be "
                       "solved — continuing with whatever the page already "
                       "holds.")
        return False
    try:
        token = solve_recaptcha(challenge, args.twocaptcha_key,
                                api_version=args.captcha_api,
                                min_score=args.min_score)
    except Exception as e:  # noqa: BLE001 — a solver failure is not a crash
        logger.error("Solving the challenge failed (%s) — continuing with "
                     "whatever the page holds.", _mask_credentials(str(e)))
        return False

    if getattr(challenge, "is_turnstile", False):
        # Turnstile hands its token back through `cf-turnstile-response` and
        # the page's own callback, not through `g-recaptcha-response`.
        called_back = page.evaluate(TURNSTILE_INJECT_JS, token)
        logger.info("Turnstile token injected%s.",
                    " and handed to the page's callback" if called_back
                    else " (no callback was captured — relying on the form field)")
        if getattr(challenge, "solved_user_agent", None):
            # Logged so a refused token can be RULED OUT as a UA mismatch,
            # not because a mismatch is known to matter: measured elsewhere
            # in this family, a token minted under a Windows UA was accepted
            # by a macOS browser.
            logger.info("2captcha solved it against user agent %r.",
                        challenge.solved_user_agent[:60] + "…")
    else:
        page.evaluate(INJECT_TOKEN_JS, token)
    logger.info("Token injected. Reloading page to continue.")
    page.wait_for_timeout(1500)
    try:
        page.reload(wait_until="domcontentloaded", timeout=60000)
    except (PWTimeout, PWError) as e:
        logger.warning("Reload after the solve failed (%s).", e)
    return True


def _looks_like_challenge(html: str, page) -> bool:
    return challenge_marker(html) is not None


# --------------------------------------------------------------------------
# The search API, called from inside the cleared browser session
# --------------------------------------------------------------------------

# Passed to page.evaluate as a FUNCTION EXPRESSION, never as a bare
# statement string. Playwright routes a function-shaped argument through
# `Runtime.callFunctionOn`, which is a protocol call rather than an eval —
# so it keeps working on a document whose CSP omits `unsafe-eval`, the
# failure that took a sibling repo's run down with an EvalError on the
# site's most obvious URL.
_SEARCH_FETCH_JS = """
async ({path, body}) => {
  const r = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
    body: JSON.stringify(body),
    credentials: 'include'
  });
  return {status: r.status, text: await r.text()};
}
"""


class SearchAPIError(RuntimeError):
    """The site's own search endpoint answered with something unusable."""


def fetch_search_page(page, body: Dict[str, Any],
                      timeout_ms: int = 45000) -> Dict[str, Any]:
    """POST one page of search results from inside the current session.

    Called from the PAGE rather than with an HTTP client on purpose: the
    endpoint is behind the same Cloudflare challenge as every HTML route
    (measured — a bare `curl` POST to it answered 403 with the challenge
    body), so it is only reachable with the cookies the cleared session
    holds. `credentials: 'include'` is what carries them.

    Raises SearchAPIError rather than returning {} on a bad answer: a
    function that returns an empty result on failure is indistinguishable
    from a genuinely empty page, and this repo's stop condition is "this
    page added no new ids".
    """
    try:
        res = page.evaluate(_SEARCH_FETCH_JS,
                            {"path": SEARCH_API_PATH, "body": body})
    except (PWError, PWTimeout) as e:
        raise SearchAPIError(f"search API call failed in-page: {e}") from None
    status = res.get("status")
    text = res.get("text") or ""
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


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

# Every remote call is bounded. A module constant rather than a flag, to
# match the rest of the family: an unshared flag here would fail the
# flag-parity check for no benefit a user has asked for.
NAV_TIMEOUT_MS = 60000


def _goto(page, url: str, args):
    """Navigate, returning the response or None. Bounded, always."""
    return page.goto(url, wait_until="domcontentloaded",
                     timeout=NAV_TIMEOUT_MS)


def _settle(page, args, mode: str) -> str:
    """Wait for the page to be usable, then hand back its HTML."""
    d = _driver(page)
    selector = page_flow.ready_selector(mode)
    page_flow.wait_for_count(d["count"], d["sleep"], selector,
                             page_flow.ready_count(mode),
                             page_flow.content_timeout_ms(mode))
    return _content_when_settled(page) or ""


def _dump(args, html: str, tag: str) -> None:
    """Write a snapshot. On SUCCESS too, not only on failure: a run can
    return the right count with a field silently unpopulated, and then the
    exact bytes are the only way to tell a parsing bug from a too-early
    snapshot."""
    if not args.dump_html or not html:
        return
    path = f"{args.out}_{tag}_debug.html"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Wrote %s (%d bytes)", path, len(html))
    except OSError as e:
        logger.warning("Could not write %s: %s", path, e)


def _open_first_page(session, args, pool):
    """Fetch page 1, clearing a challenge if one is in the way.

    Page 1 is always fetched ALONE, whatever --concurrency says, because its
    content is what decides whether the rest can be addressed and how many
    of them there are.
    """
    page = session.page
    url = args.url
    attempts = args.retries + 1
    last_state = None
    html = ""

    for attempt in range(1, attempts + 1):
        try:
            resp = _goto(page, url, args)
        except (PWTimeout, PWError) as e:
            marker = _proxy_failure(e)
            if marker and pool:
                # A dead proxy is not a timeout: it wants a DIFFERENT exit,
                # where a timeout wants another try at the same one.
                logger.warning("Proxy exit %s failed (%s) — rotating.",
                               mask(pool.current), marker)
                try:
                    pool.advance(f"proxy failure: {marker}")
                except ProxyError as pe:
                    raise RemoteAPIError(str(pe)) from None
                session.relaunch()
                page = session.page
                continue
            logger.warning("Navigation to %s failed (attempt %d/%d): %s",
                           url, attempt, attempts, _mask_credentials(str(e)))
            if attempt == attempts:
                return None, None, "load_failed"
            time.sleep(args.retry_delay)
            continue

        status = resp.status if resp else None
        headers = dict(resp.headers) if resp else None
        html = _settle(page, args, args.mode)
        state = _classify(page, html, status=status, headers=headers, mode=args.mode)
        last_state = state
        logger.info("Page 1 (%s): HTTP %s, state=%s, %d bytes",
                    args.mode, status, state, len(html or ""))

        if state == "content":
            _dump(args, html, "page1")
            return page, html, state

        if page_flow.should_solve(state):
            if handle_captcha_if_present(page, args,
                                         page_flow.ready_selector(args.mode),
                                         proxy=pool.current if pool else None):
                html = _settle(page, args, args.mode)
                state = _classify(page, html, mode=args.mode)
                logger.info("After solving: state=%s", state)
                if state == "content":
                    _dump(args, html, "page1")
                    return page, html, state

        if not page_flow.should_retry(state):
            _dump(args, html, f"page1_{state}")
            return page, html, state

        if attempt < attempts:
            if pool:
                try:
                    pool.advance(f"page 1 came back as {state}")
                    session.relaunch()
                    page = session.page
                except ProxyError as pe:
                    logger.warning("Could not rotate: %s", pe)
            time.sleep(args.retry_delay)

    _dump(args, html, f"page1_{last_state}")
    return page, None, last_state or "blocked"


def _rows_from_result(result: dict, args, page_index: int, sort: str,
                      category: Optional[str], position_offset: int):
    sp = parse_search_api(result, page_index=page_index, sort=sort,
                          url=args.url, category=category,
                          position_offset=position_offset)
    return sp


def _filter_platform(rows: List[Campaign], wanted: Optional[str]) -> List[Campaign]:
    """Keep only rows hosted on `wanted`, if a filter was asked for.

    Applied AFTER parsing rather than as a request parameter because the
    site has no platform filter of its own: `source`,
    `userCommunitySearchTypes` and `retailerOfferTypes` were each measured
    to leave the mix unchanged. So this narrows what is WRITTEN, and the
    sidecar's page counts still describe what was FETCHED — which is why
    the two can legitimately disagree.
    """
    if not wanted or wanted == "all":
        return rows
    return [r for r in rows if r.platform == wanted]


# --------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------

def _worker_pool(pool: Optional[ProxyPool], worker_index: int):
    """A pool object holding the same exits, rotated to a different offset.

    Each worker gets its OWN pool rather than sharing one, so no thread
    needs a lock: the concurrency is safe by construction rather than by
    discipline. A worker then owns one exit for its lifetime.
    """
    if not pool:
        return None
    proxies = pool.proxies
    offset = worker_index % len(proxies)
    return ProxyPool(proxies[offset:] + proxies[:offset], rotate="per-run")


def _fetch_pages_concurrently(args, pool, page_indices: List[int],
                              body_template: Dict[str, Any], sort: str,
                              category: Optional[str], concurrency: int):
    """Fetch pages 2..N with N workers, each owning its own browser.

    Every worker has to clear Cloudflare for itself before it can call the
    API at all — there is no shared cookie jar across browsers — which is
    why `page_flow.concurrency_warning` says what it says about raising this
    without a proxy pool.

    Returns {page_index: PageOutcome}. Restorable to page order by the
    caller; nothing is merged here.
    """
    work: "queue.Queue[int]" = queue.Queue()
    for idx in page_indices:
        work.put(idx)
    results: Dict[int, PageOutcome] = {}
    lock = threading.Lock()
    # Dispatch stops at the end of the listing, so asking for 50 pages of a
    # 5-page category costs at most `concurrency - 1` extra fetches.
    exhausted = threading.Event()

    def worker(widx: int):
        wpool = _worker_pool(pool, widx)
        with sync_playwright() as pw:
            session = _BrowserSession(pw, args, wpool,
                                      remote=bool(args.cdp_endpoint)).open()
            try:
                # Each worker needs a cleared session before the API answers.
                try:
                    _goto(session.page, args.url, args)
                    session.page.wait_for_timeout(1500)
                    html = _content_when_settled(session.page) or ""
                    if page_flow.should_solve(_classify(session.page, html,
                                                        mode=args.mode)):
                        handle_captcha_if_present(
                            session.page, args,
                            page_flow.ready_selector(args.mode),
                            proxy=wpool.current if wpool else None)
                except (PWError, PWTimeout) as e:
                    logger.warning("Worker %d could not open a session: %s",
                                   widx, _mask_credentials(str(e)))
                    return
                while not exhausted.is_set():
                    try:
                        idx = work.get_nowait()
                    except queue.Empty:
                        return
                    outcome = PageOutcome(page_num=idx + 1, url=args.url)
                    body = dict(body_template)
                    body["pageIndex"] = idx
                    try:
                        payload = fetch_search_page(session.page, body)
                    except SearchAPIError as e:
                        logger.warning("Worker %d, pageIndex %d: %s", widx, idx, e)
                        outcome.load_failed = True
                        with lock:
                            results[idx] = outcome
                        continue
                    sp = parse_search_api(payload, page_index=idx, sort=sort,
                                          url=args.url, category=category)
                    outcome.rows = sp.rows
                    outcome.total_results = sp.total_results
                    outcome.total_pages = sp.total_pages
                    outcome.capped = sp.capped
                    outcome.state = "content" if sp.rows else "empty"
                    if not sp.rows:
                        # The end of the listing. Stop dispatching rather
                        # than letting every remaining worker discover it.
                        logger.info("pageIndex %d returned no rows — end of "
                                    "the listing.", idx)
                        exhausted.set()
                    with lock:
                        results[idx] = outcome
                    if args.delay:
                        time.sleep(args.delay)
            finally:
                session.close()

    threads = [threading.Thread(target=worker, args=(i,), daemon=True)
               for i in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------

def scrape(args) -> int:
    pool = proxy_pool_from_args(args)
    if pool and args.cdp_endpoint:
        logger.error("--proxy/--proxy-file cannot be combined with "
                     "--cdp-endpoint: the Scraping Browser already proxies, "
                     "and stacking a second one creates a contradiction "
                     "rather than better cover.")
        return 2

    warning = page_flow.concurrency_warning(args.concurrency, pool is not None,
                                            args.cdp_endpoint)
    if warning:
        if args.cdp_endpoint:
            logger.error(warning)
            return 2
        logger.warning(warning)

    if args.concurrency > 1 and args.mode not in CONCURRENCY_CAPABLE_MODES:
        logger.error("--concurrency > 1 is not available for --mode %s: it "
                     "reads exactly one page.", args.mode)
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

    with sync_playwright() as pw:
        session = _BrowserSession(pw, args, pool,
                                  remote=bool(args.cdp_endpoint)).open()
        try:
            page, html, state = _open_first_page(session, args, pool)
            if page is not None:
                final_url = page.url

            if state != "content":
                blocked = page_flow.counts_as_blocked(state)
                stop_reason = {
                    "empty": "no_results",
                    "not_found": "not_found",
                    "captcha": "blocked_by_captcha",
                    "blocked": "blocked",
                    "load_failed": "load_failed",
                    "unpainted": "never_painted",
                }.get(state, state or "blocked")
                logger.error("Page 1 came back as %s — stopping.", state)
                return finish_run(
                    [], args.out, args.format, args.allow_empty,
                    blocked=blocked, stop_reason=stop_reason,
                    pages_requested=args.pages, pages_completed=0,
                    start_url=args.url, final_url=final_url,
                    pages_failed=[1], mode=args.mode, sort=sort)

            # ---- single-page modes -------------------------------------
            if args.mode == "campaign":
                row = parse_campaign(html, url=page.url, sort=sort)
                if row is None:
                    logger.error("No campaign state on %s — the page was "
                                 "served but holds no campaign object.", page.url)
                    stop_reason = "parser_found_nothing"
                else:
                    rows = [row]
                    pages_completed = 1
                    stop_reason = "single_page_mode"
            elif args.mode == "rewards":
                rows = parse_rewards(html, url=page.url)
                pages_completed = 1
                stop_reason = "single_page_mode"
                if not rows:
                    logger.warning("No reward boxes on %s. A campaign that "
                                   "has ended, or one whose perks are not "
                                   "published, legitimately has none.", page.url)
            else:
                # ---- search -------------------------------------------
                result = extract_inline_search_result(html)
                if result is None:
                    # The page was served (state == content) but its inlined
                    # payload is gone: a site change, not a block. Say so by
                    # name rather than reporting "0 products", which sends
                    # the reader to check the URL instead of the parser.
                    logger.error(
                        "The search page was served but its inlined results "
                        "payload (%s) is missing. That is a SITE CHANGE, not "
                        "a block — the DOM fallback will be used for page 1, "
                        "but pagination needs the payload's parameters.",
                        "App.Components.Search.SearchProjectsResults")
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
                    logger.info("Page 1: %d rows. Site reports %s results "
                                "across %s pages (capped=%s).", len(sp.rows),
                                total_results, total_pages, capped)
                    if capped:
                        logger.info(
                            "This query is CAPPED by the site: it claims %s "
                            "matches but will serve at most %s rows. A run "
                            "that fetches every page is complete AND a "
                            "sample.", total_results,
                            page_flow.reachable_max(total_pages))

                    site_params = extract_search_params(html)
                    if site_params:
                        body_template = body_from_site_params(site_params, 0, sort)
                    else:
                        logger.info("No inlined search params; rebuilding the "
                                    "request body from the URL instead.")
                        body_template = build_search_body(args.url, 0, sort)

                    wanted = page_flow.plan_page_indices(
                        args.pages, total_pages, start_index=0)[1:]
                    if args.pages > 1 and not wanted:
                        stop_reason = "site_result_cap" if capped else "pagination_exhausted"

                    if wanted and args.concurrency > 1:
                        outcomes = _fetch_pages_concurrently(
                            args, pool, wanted, body_template, sort,
                            category, args.concurrency)
                        # Merge in PAGE ORDER, not arrival order.
                        for idx in sorted(outcomes):
                            o = outcomes[idx]
                            if o.load_failed:
                                pages_failed.append(idx + 1)
                                continue
                            if not o.rows:
                                break
                            rows.extend(o.rows)
                            pages_completed += 1
                    elif wanted:
                        seen_ids = {r.sku for r in rows if r.sku}
                        for idx in wanted:
                            body = dict(body_template)
                            body["pageIndex"] = idx
                            try:
                                payload = fetch_search_page(page, body)
                            except SearchAPIError as e:
                                logger.warning("pageIndex %d: %s", idx, e)
                                pages_failed.append(idx + 1)
                                stop_reason = "page_failed"
                                break
                            sp = parse_search_api(
                                payload, page_index=idx, sort=sort,
                                url=args.url, category=category)
                            fresh = [r for r in sp.rows
                                     if r.sku and r.sku not in seen_ids]
                            if not sp.rows:
                                logger.info("pageIndex %d returned no rows — "
                                            "end of the listing.", idx)
                                stop_reason = ("site_result_cap" if capped
                                               else "pagination_exhausted")
                                break
                            if not fresh:
                                # A terminating condition based on DATA, not
                                # on a selector: an exhausted listing is a
                                # property of the catalogue.
                                logger.info("pageIndex %d added no new ids — "
                                            "stopping.", idx)
                                stop_reason = "no_new_products"
                                break
                            seen_ids.update(r.sku for r in fresh if r.sku)
                            rows.extend(sp.rows)
                            pages_completed += 1
                            if page_flow.is_thin_page(len(sp.rows)):
                                logger.info("pageIndex %d returned only %d "
                                            "rows.", idx, len(sp.rows))
                            if args.delay:
                                time.sleep(args.delay)
                        else:
                            if pages_completed >= args.pages:
                                stop_reason = "completed"
        except RemoteAPIError as e:
            logger.error("%s", _mask_credentials(str(e)))
            return EXIT_REMOTE_API_ERROR
        finally:
            session.close()

    # Dedupe across pages, then renumber `position` so it is unique across
    # the whole run. `page` + `position` must be unique on a multi-page run;
    # leaving position restarting at 1 per page would have 24 rows claiming
    # a position another row already held.
    rows = dedupe_by_key(rows, set(), key="sku")
    before = len(rows)
    rows = _filter_platform(rows, args.platform)
    if len(rows) != before:
        logger.info("--platform %s kept %d of %d rows.",
                    args.platform, len(rows), before)
    for i, r in enumerate(rows, start=1):
        r.position = i

    return finish_run(
        rows, args.out, args.format, args.allow_empty,
        blocked=blocked, stop_reason=stop_reason,
        pages_requested=args.pages, pages_completed=pages_completed,
        start_url=args.url, final_url=final_url, pages_failed=pages_failed,
        mode=args.mode, sort=sort, total_results=total_results,
        pages_available=total_pages, capped_by_site=capped,
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
    except RemoteAPIError as e:
        logger.error("%s", _mask_credentials(str(e)))
        return EXIT_REMOTE_API_ERROR
    except KeyboardInterrupt:
        logger.warning("Interrupted.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
