"""
page_flow.py
-------------
Policy and pure algorithms shared by the three engines: what a fetched page
MEANS, what to do about it, and how this site's pagination works.

It exists for the reason CLAUDE.md gives — indiegogo.com answers a request
in six distinguishable ways and five of them want a different response:

    content     the page was served and holds results
    empty       the page was served and the SITE says the query matched
                nothing (`data-qa="search-count:0"`) — a correct answer
    unpainted   the page was served but its grid has not hydrated yet
    not_found   the site's own 404 — most often a gamefound.com-hosted
                campaign asked for on indiegogo.com
    captcha     a Cloudflare managed challenge, with a Turnstile on it
    blocked     a refusal with no widget to solve

Three copies of that triage across three engines would drift, and the drift
would be silent: one engine reporting exit 3 where its twin reports exit 4
on the same page. `STATE_POLICY` keeps the retry/solve/blocked decision as
DATA rather than as three copies of an if-chain.

Deliberately no JavaScript crosses this boundary. Selenium's
`execute_script` takes a function BODY with an explicit `return`, while
Playwright and pyppeteer take `() => expr`; naming the OPERATION instead
(`count`, `sleep`) is what keeps this module from acquiring one driver's
dialect.

Pagination, and why this site needed its own answer
---------------------------------------------------
Indiegogo's search listing has NO per-page URL. `?page=2` on
/en/projects/search is accepted, answers HTTP 200, and returns the
IDENTICAL first 24 cards — measured for `page`, `pageNumber` and `p`, all
three giving the same first project id as page 1. A `page_url()` convention
ported from a sibling repo would therefore refetch page 1 forever, find no
new ids, conclude the listing was exhausted and report a COMPLETE run
holding 24 rows. `product_parser.page_url()` raises rather than returning
anything, so that mistake cannot be made quietly.

Pages are addressed by a 0-based `pageIndex` inside the
`searchProjectsForCards` POST body instead. That IS an independent address —
page 5 does not need page 4 to have been fetched — so the family's
concurrency model still applies, with one caveat this site adds: every
worker owns a browser, and every browser must clear Cloudflare on its own
before it can call the API at all. See `concurrency_warning()`.
"""

import logging
from typing import Callable, Dict, List, Optional, Tuple

from product_parser import detect_page_state, PAGE_SIZE

logger = logging.getLogger("page_flow")


# What "the page has painted" means, per mode.
#
# Unlike the server-rendered members of this family, the search page's raw
# document holds ZERO cards (measured: 137 KB, 0 matches) and hydrates its
# grid client-side. That does NOT mean this repo waits for the paint: page
# 1's results are inlined as a Vue component prop in the same raw document,
# which is what `--mode search` reads. These anchors are the fallback path's
# readiness check, and the campaign modes'.
READY_SELECTORS = {
    "search": "[data-qa^='search-result-project:']",
    "campaign": "[data-qa^='project-navigation-link']",
    "rewards": "[data-qa^='reward-box:']",
}

# How many matches mean "rendered" rather than one lucky hit. Must be > 1,
# per the family rule: waiting for a single match resolves on an unrelated
# element long before the grid paints. The site serves exactly 24 cards per
# full page, so 3 is a floor a real page clears immediately while a short
# final page does not wait out the timeout. A campaign page has one of its
# anchor, so `campaign` opts out with 0.
MIN_ROW_MATCHES = {"search": 3, "campaign": 0, "rewards": 1}

CONTENT_TIMEOUT_MS = {"search": 20000, "campaign": 15000, "rewards": 15000}


def ready_selector(mode: str) -> str:
    return READY_SELECTORS.get(mode, READY_SELECTORS["search"])


def min_matches(mode: str) -> int:
    return MIN_ROW_MATCHES.get(mode, 3)


def content_timeout_ms(mode: str) -> int:
    return CONTENT_TIMEOUT_MS.get(mode, 20000)


def ready_count(mode: str) -> int:
    """How many matches the readiness wait must SEE before it stops.

    `min_matches` is a FLOOR a page must clear — "more than three cards",
    not "three". `wait_for_count` is written the other way round, waiting
    until it has seen at least `minimum`, because that is the shape the rest
    of the family uses. Converting between the two in ONE place keeps a
    stray `+ 1` out of three engines, where the first one to lose it would
    wait on a different threshold than its twins with nothing offline
    noticing.
    """
    return min_matches(mode) + 1


def wait_for_count(count: Callable[[str], int], sleep: Callable[[int], None],
                   selector: str, minimum: int, timeout_ms: int,
                   poll_ms: int = 250) -> int:
    """Poll `selector` until `minimum` elements match, or the budget runs out.

    Polls a COUNT rather than waiting on an evaluated string. Playwright's
    `wait_for_function` and pyppeteer's `waitForFunction` both hand the
    browser a STRING to evaluate, which a site whose CSP lacks `unsafe-eval`
    refuses outright — on a sibling repo that was an `EvalError` and exit 1
    on the site's most obvious URL.

    That is not a hypothetical here. indiegogo.com's own challenge page
    ships `script-src 'nonce-…' 'unsafe-eval' https://challenges.cloudflare.com`,
    and its served pages carry a CSP of their own; polling
    `querySelectorAll` through the protocol is a CDP call, works under any
    CSP, and spells the same in all three drivers.

    `count` is expected to swallow its own driver errors and return 0: this
    polls a page that may be navigating under it, and an exception from the
    500th millisecond of a 20-second wait should read as "nothing there
    yet", not take the run down.

    Returns the last count seen, so a caller can tell "painted" from "timed
    out with three of them".
    """
    waited = 0
    seen = count(selector)
    while seen < minimum and waited < timeout_ms:
        sleep(poll_ms)
        waited += poll_ms
        seen = count(selector)
    if seen < minimum:
        logger.info("readiness wait ended at %d/%d matches for %s after %dms",
                    seen, minimum, selector, waited)
    return seen


# A page holding less than this fraction of a full page is logged as thin —
# INFORMATIONAL ONLY, never a retry trigger and never a reason to change the
# exit code. The site serves exactly 24 per page, so a short page is either
# the last one (correct) or a regression (worth a line in the log).
THIN_PAGE_RATIO = 0.4


def is_thin_page(row_count: int, first_page_count: int = PAGE_SIZE) -> bool:
    if first_page_count <= 0:
        return False
    return row_count < first_page_count * THIN_PAGE_RATIO


def classify(html: Optional[str], *, status: Optional[int] = None,
             url: Optional[str] = None,
             headers: Optional[Dict[str, str]] = None,
             mode: str = "search") -> str:
    """The page's state, as the engines see it.

    A thin wrapper over product_parser.detect_page_state so every engine
    reaches the policy through one name.

    `status` and `headers` are both OPTIONAL because not every engine can
    supply them, and the classifier has to stay correct for the ones that
    cannot. Playwright exposes the response object and the Scraper API
    returns `{"status", "headers", "body"}`, so those two pass both — which
    matters more here than on most sites in this family, because Cloudflare
    announces a managed challenge in a `cf-mitigated: challenge` RESPONSE
    HEADER as well as in the body. Selenium and pyppeteer read the rendered
    DOM through the driver and have no response object to ask, so for them
    the body markers are the only signal.

    Keyword-only after `html` on purpose: `classify(html, status, url)` with
    `status` positional is the exact signature that crashed two of three
    engines in a sibling repo on their first fetch, because a caller wrote
    `classify(html, url=...)` and bound `url` to `status`.
    """
    return detect_page_state(html or "", status=status, url=url,
                             headers=headers, mode=mode)


# What each state means for the run. Kept as data, not as three copies of an
# if-chain, for the reason CLAUDE.md gives: an engine cannot then quietly
# disagree with its twins about whether a page is worth retrying or paying
# for.
STATE_POLICY = {
    "content": {"retry": False, "solve": False, "blocked": False, "wait": False},
    # A managed challenge renders a Turnstile, and 2Captcha solves those
    # (`TurnstileTaskProxyless`). Worth a solve, and worth a retry after one.
    "captcha": {"retry": True, "solve": True, "blocked": True, "wait": False},
    # A refusal with no widget on it. Retried (a different exit may be
    # served normally) but never paid for: there is nothing to solve.
    "blocked": {"retry": True, "solve": False, "blocked": True, "wait": False},
    # NOT retried and NOT blocked: the site ran the query and said it
    # matched nothing. Retrying a correct answer wastes a fetch, and
    # reporting it as blocked sends the reader hunting for a proxy problem.
    "empty": {"retry": False, "solve": False, "blocked": False, "wait": False},
    # NOT retried: the URL does not exist on this host. The usual cause is a
    # gamefound.com-hosted campaign asked for on indiegogo.com, which no
    # number of retries and no solve will change.
    "not_found": {"retry": False, "solve": False, "blocked": False, "wait": False},
    # Served, but the grid has not hydrated. WAITS rather than retrying —
    # refetching a page that was already served correctly is how a sibling
    # repo turned a slow paint into a doubled fetch and 0 rows.
    "unpainted": {"retry": False, "solve": False, "blocked": False, "wait": True},
}


def should_retry(state: str) -> bool:
    return STATE_POLICY.get(state, {}).get("retry", False)


def should_solve(state: str) -> bool:
    return STATE_POLICY.get(state, {}).get("solve", False)


def counts_as_blocked(state: str) -> bool:
    return STATE_POLICY.get(state, {}).get("blocked", False)


def should_wait(state: str) -> bool:
    return STATE_POLICY.get(state, {}).get("wait", False)


def comparable(url: str) -> str:
    """`url` reduced to the parts that decide WHICH PAGE it addresses.

    Drops the fragment, normalises query-parameter ORDER, and strips the
    site's own tracking parameter (`?ref=explore` on its internal links) so
    that a campaign reached from a listing and the same campaign reached
    directly compare equal.
    """
    from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
    from product_parser import _TRACKING_PARAMS
    parts = urlparse(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k not in _TRACKING_PARAMS]
    return urlunparse(parts._replace(query=urlencode(sorted(kept)), fragment=""))


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------

def listing_is_url_addressable(url: str = "") -> bool:
    """Whether page N of this listing has a URL of its own. It does not.

    Stated as a function rather than left implicit so the offline suite can
    assert it, and so the answer is in ONE place rather than repeated as a
    comment in three engines. See the module docstring for the measurement.
    """
    return False


def pages_are_independently_addressable(mode: str) -> bool:
    """Whether page N can be REQUESTED without first fetching page N-1.

    True for `search`, even though `listing_is_url_addressable()` is False:
    the address is a 0-based `pageIndex` in a POST body rather than a URL,
    and pageIndex=5 is answered without pageIndex=4 ever having been asked
    for (measured — pages 0, 1, 2, 208, 415 all answered directly, with zero
    id overlap between consecutive pages).

    The two functions say different things on purpose. The first is about
    URLs, and answers "can this be handed to `--dump-html` or curl". The
    second is about whether the family's concurrency model applies.
    """
    return mode == "search"


def plan_page_indices(pages_requested: int, total_pages: Optional[int],
                      start_index: int = 0) -> List[int]:
    """The 0-based pageIndex values to fetch, planned against the SITE's own
    arithmetic rather than against a guess.

    The site reports `totalPageCount` on every response, so a run that asks
    for more pages than exist is trimmed here instead of discovering the end
    by fetching empty pages. That matters because the site OVERSTATES it by
    one: `totalPageCount` is 417 for an uncapped-looking query, and
    pageIndex=416 (the 417th page) returns zero items while pageIndex=415
    returns a full 24. Measured by binary search on 2026-09-17.

    So the last index actually worth requesting is `total_pages - 2`, and
    that off-by-one is applied HERE, once, rather than in three engines.
    """
    if pages_requested <= 0:
        return []
    idxs = list(range(start_index, start_index + pages_requested))
    if total_pages is not None and total_pages > 0:
        last_useful = total_pages - 2
        idxs = [i for i in idxs if i <= last_useful]
    return idxs


def reachable_max(total_pages: Optional[int],
                  page_size: int = PAGE_SIZE) -> Optional[int]:
    """How many rows this query can actually yield, given the site's cap.

    `totalItemCount` is what the site claims matched; this is what it will
    actually serve. For the uncapped-looking default query that is
    416 pages x 24 = 9,984 rows against a claimed 10,000.
    """
    if not total_pages or total_pages <= 0:
        return None
    return (total_pages - 1) * page_size


def concurrency_warning(concurrency: int, has_proxy_pool: bool,
                        cdp_endpoint: Optional[str]) -> Optional[str]:
    """The warning or refusal a given --concurrency deserves on this site.

    Returns None when the setting is fine, a string to print otherwise. The
    engines share this so they cannot disagree about when concurrency is
    allowed.
    """
    if concurrency <= 1:
        return None
    if cdp_endpoint:
        return ("--concurrency > 1 is refused with --cdp-endpoint: the "
                "Scraping Browser API allows one live connection per "
                "profile, so workers collide (profile_locked). Use several "
                "pids, one run each.")
    if not has_proxy_pool:
        return ("--concurrency %d without a proxy pool sends %dx the traffic "
                "from one address. Every indiegogo.com page is behind a "
                "Cloudflare managed challenge, and each worker has to clear "
                "it separately from that same address — which is a faster "
                "way to get the address scored than to gather data."
                % (concurrency, concurrency))
    return None
