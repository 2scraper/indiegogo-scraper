"""
product_parser.py
------------------
Everything that knows what indiegogo.com looks like. The engines and the
shared modules around this file are site-agnostic; if you are porting this
repo to another site, this is the file you rewrite.

Where the data actually comes from
----------------------------------
Indiegogo publishes **zero** `<script type="application/ld+json">` blocks —
measured on a search listing, a campaign page, a campaign's rewards tab and
a German-locale listing, all on 2026-09-17. So the JSON-LD-first pattern the
e-commerce members of this family use does not apply, and the primary path
is the site's OWN JSON, in two shapes:

  --mode search    POST /api/projectSearch/searchProjectsForCards
                   The exact call the site's own "Load more" button makes.
                   Returns the same objects the server-rendered first page
                   is built from, so one parser reads both routes and they
                   cannot drift.

  --mode campaign  window.__INITIAL_STATE__, inlined in the campaign page.
  --mode rewards   the reward boxes on the campaign page's rewards tab.

The rendered DOM is the FALLBACK, not the primary, and it is strictly worse
in three measured ways rather than merely less convenient:

  * counts are abbreviated — a card prints "3.4k" backers where the JSON
    says 3412;
  * money is rounded for display — a card prints "€12,576" where the JSON
    says 12576.05;
  * the countdown is localised and relative — "29 days left" on /en/ is
    "29 Tage verbleiben" on /de/, while the JSON carries an absolute ISO
    `campaignEnd`.

The fallback anchors on the site's own `data-qa` attributes rather than on
CSS classes, for the reason CLAUDE.md gives for preferring `data-asin` on
Amazon: they are the site's test hooks, they are semantic, and they survive
a restyle. The class names here (`gfu-project-card__title`) are semantic too
rather than build hashes, but `data-qa` is still the better anchor.

A note on what this site IS, as of 2026-09-17
---------------------------------------------
www.indiegogo.com now runs on the Gamefound platform (its markup is prefixed
`gfu-`, its images come off `imgcdn.gamefound.com` for some rows), and its
search results MIX campaigns from both sites: 110 of 432 sampled rows were
hosted on gamefound.com, not on indiegogo.com. Those rows are real search
results that indiegogo.com serves, and they are not Indiegogo campaigns —
`/en/projects/<creator>/<slug>` answers 404 for them on this host. Hence the
`platform` column, and hence `--mode campaign` refusing a row that is not
`platform == "indiegogo"` rather than fetching a 404 and reporting it as an
empty page.
"""

import html as _html
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from bs4 import BeautifulSoup

from output_writer import Campaign, Reward, SOURCE_DEFAULT


# --------------------------------------------------------------------------
# Hosts and locales
# --------------------------------------------------------------------------

# Indiegogo is ONE host with a locale path prefix, not a set of country
# hostnames like MediaMarkt's ten. Taken from the site's own
# <link rel="alternate" hreflang=...> set on /en/projects/search rather than
# guessed, per CLAUDE.md's per-site checklist.
LOCALES = ("en", "it", "fr", "de", "pl", "es", "cs", "pt", "zh")

CANONICAL_HOST = "www.indiegogo.com"

# Hosts a row may legitimately point at. gamefound.com appears because
# indiegogo.com's search results include campaigns hosted there; it is
# recognised so those rows can be LABELLED, never so they can be fetched
# from this repo.
PLATFORM_BY_HOST = {
    "www.indiegogo.com": "indiegogo",
    "indiegogo.com": "indiegogo",
    "gamefound.com": "gamefound",
    "www.gamefound.com": "gamefound",
}

# The API's own numeric platform flag, measured against the `url` host on 432
# sampled rows (322 host=www.indiegogo.com <-> platform 1, 110
# host=gamefound.com <-> platform 0, no exceptions).
PLATFORM_BY_CODE = {0: "gamefound", 1: "indiegogo"}


# --------------------------------------------------------------------------
# The search API
# --------------------------------------------------------------------------

SEARCH_API_PATH = "/api/projectSearch/searchProjectsForCards"

# The site serves exactly 24 cards per page, on the server-rendered first
# page and from the API alike.
PAGE_SIZE = 24

# The site's own sort menu, read off its rendered radio inputs rather than
# inferred: each label below sits next to that value in the "Sort" modal on
# /en/projects/search.
#
# These are NOT interchangeable samples. Under `default` the first page is
# heavily weighted toward gamefound.com-hosted campaigns, and `a-z`, `z-a`
# and `oldest` returned zero of them across 120 rows each. Two runs under
# different sorts are different samples of the same query -- see
# output_writer.Campaign's `sort` column.
SORT_TYPES = {
    "default": 0,
    "newest": 1,
    "oldest": 2,
    "most-popular": 3,
    "most-funded": 4,
    "a-z": 5,
    "z-a": 6,
    "ending-soon": 7,
    "most-discussed": 8,
    "recently-updated": 9,
}
SORT_NAME_BY_TYPE = {v: k for k, v in SORT_TYPES.items()}
DEFAULT_SORT = "default"

# The request body the site's own Load-more button sends. Every key is kept,
# including the ones this repo never sets: the endpoint is the site's, not
# ours, and sending it a body shaped differently from the one it builds for
# itself is how a scraper starts getting a different answer than the page.
SEARCH_BODY_TEMPLATE: Dict[str, Any] = {
    "creatorID": None,
    "creatorName": None,
    "sortType": 0,
    "term": "",
    "projectPhaseSearchTypes": [],
    "projectBenefits": [],
    "projectTags": [],
    "projectCatalogCategories": [],
    "playerAges": [],
    "playerCounts": [],
    "playTimes": [],
    "creator": {"creatorID": None, "name": None},
    "retailerOfferTypes": [],
    "userCommunitySearchTypes": [],
    "source": None,
    "pageIndex": 0,
}

# Query parameters on a /projects/search URL that map straight onto body
# fields. The site accepts the STRING form of a category
# (["BoardAndCardGames"]) as well as its internal numeric enum ([11]) -- both
# measured to return the identical result set (3774 items, same first id) --
# so no enum table has to be maintained here.
_LIST_PARAMS = {
    "projectCatalogCategories": "projectCatalogCategories",
    "projectTags": "projectTags",
    "projectPhaseSearchTypes": "projectPhaseSearchTypes",
    "projectBenefits": "projectBenefits",
    "playerAges": "playerAges",
    "playerCounts": "playerCounts",
    "playTimes": "playTimes",
}


class ListingNotAddressable(ValueError):
    """Raised when a caller asks for a page URL this listing cannot produce.

    Indiegogo's search listing has no per-page addresses at all: `?page=2`
    on /en/projects/search is accepted, returns HTTP 200, and serves the
    IDENTICAL first 24 cards (measured -- same first project id as page 1,
    for `page`, `pageNumber` and `p` alike). A `page_url()`-style convention
    would therefore refetch page 1 forever, find no new ids, conclude the
    listing was exhausted and report a COMPLETE run holding 24 rows.

    Pages are addressed by `pageIndex` in the POST body instead; see
    build_search_body().
    """


def page_url(url: str, page: int) -> str:
    """Deliberately refuses. See ListingNotAddressable.

    This function exists so that the mistake is loud. Every other repo in
    this family has a `page_url()` that rebuilds `?page=N`, and porting one
    here would produce a silent single-page run that reports success.
    """
    raise ListingNotAddressable(
        "indiegogo.com's search listing has no per-page URL: ?page=N is "
        "accepted and returns page 1 again. Pages are requested by "
        "pageIndex in the searchProjectsForCards POST body -- see "
        "build_search_body()."
    )


def build_search_body(url: str, page_index: int,
                      sort: Optional[str] = None) -> Dict[str, Any]:
    """Build the searchProjectsForCards body for one page of `url`.

    `page_index` is 0-BASED, because the site's is: pageIndex=0 returns
    items 1-24 (`firstItemNumber` 1) and pageIndex=1 returns items 25-48.
    The `page` column on a row is 1-based, as printed. Getting this
    backwards silently skips the first 24 rows of every run, which is why
    the boundary is asserted in the offline suite against a real capture.
    """
    if page_index < 0:
        raise ValueError(f"page_index is 0-based and cannot be negative: {page_index}")

    body = json.loads(json.dumps(SEARCH_BODY_TEMPLATE))  # deep copy
    body["pageIndex"] = page_index

    q = parse_qs(urlparse(url).query)

    if sort is not None:
        if sort not in SORT_TYPES:
            raise ValueError(
                f"unknown sort {sort!r}; known: {', '.join(sorted(SORT_TYPES))}")
        body["sortType"] = SORT_TYPES[sort]

    term = (q.get("term") or q.get("q") or [""])[0]
    if term:
        body["term"] = term

    for param, field in _LIST_PARAMS.items():
        vals = [v for v in q.get(param, []) if v != ""]
        if vals:
            # a repeated param and a comma-joined one both occur in the
            # site's own links
            flat: List[Any] = []
            for v in vals:
                flat.extend(p for p in v.split(",") if p)
            body[field] = [_maybe_int(p) for p in flat]

    creator = (q.get("creatorName") or [""])[0]
    if creator:
        body["creatorName"] = creator
        body["creator"] = {"creatorID": None, "name": creator}

    return body


def _maybe_int(v: str) -> Any:
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def sort_from_url(url: str, override: Optional[str] = None) -> str:
    """Pick the sort for a run: an explicit --sort wins over the URL's own.

    The site's legacy `?sort=trending` parameter is NOT honoured by the
    current site -- /explore/all?sort=trending redirects to
    /en/projects/search and the sort menu still reads "Default" -- so a URL
    carrying one is reported rather than silently obeyed.
    """
    if override:
        if override not in SORT_TYPES:
            raise ValueError(
                f"unknown sort {override!r}; known: {', '.join(sorted(SORT_TYPES))}")
        return override
    q = parse_qs(urlparse(url).query)
    raw = (q.get("sortType") or [""])[0]
    if raw:
        name = SORT_NAME_BY_TYPE.get(_maybe_int(raw))
        if name:
            return name
    return DEFAULT_SORT


# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------

# The site writes an explicit ":443" into its own absolute links
# (href="https://www.indiegogo.com:443/en/projects/...") and appends a
# `?ref=explore` / `?ref=source-indiegogo_explore` tracking parameter. Both
# are stripped so that the same campaign fetched from a listing and from a
# campaign page produces the same `url`, and so that two runs do not diff as
# "changed" over a tracking token.
_DEFAULT_PORTS = {"https": "443", "http": "80"}
_TRACKING_PARAMS = ("ref", "utm_source", "utm_medium", "utm_campaign",
                    "utm_term", "utm_content")

_PROJECT_PATH_RE = re.compile(
    r"^/(?:(?P<locale>%s)/)?projects/(?P<creator>[^/]+)/(?P<slug>[^/?#]+)"
    % "|".join(LOCALES)
)
# The pre-Gamefound Indiegogo URL shape, still redirected by the site:
# /projects/<slug> with no creator segment.
_LEGACY_PROJECT_PATH_RE = re.compile(r"^/(?:(?:%s)/)?projects/(?P<slug>[^/?#]+)/?$"
                                     % "|".join(LOCALES))

SELECTORS = {
    # How a campaign link is recognised. A URL pattern, never a CSS class --
    # classes churn, this path is a contract with search engines.
    "item_link": "a[href*='/projects/']",
    # The site's own test hooks, which is what the DOM fallback anchors on.
    "card": "[data-qa^='search-result-project:']",
    "card_id": "[data-qa^='project-card-ID:']",
    "reward_box": "[data-qa^='reward-box:']",
    "search_count": "[data-qa^='search-count:']",
}


def normalise_url(u: Optional[str]) -> Optional[str]:
    """Strip the site's explicit :443 and its tracking params."""
    if not u:
        return u
    u = _html.unescape(u.strip())
    try:
        p = urlparse(u)
    except ValueError:
        return u
    if not p.scheme:
        return u
    netloc = p.netloc
    if ":" in netloc:
        host, _, port = netloc.rpartition(":")
        if host and port == _DEFAULT_PORTS.get(p.scheme):
            netloc = host
    q = [(k, v) for k, v in
         [(k, v) for k, vs in parse_qs(p.query, keep_blank_values=True).items()
          for v in vs]
         if k not in _TRACKING_PARAMS]
    return urlunparse((p.scheme, netloc, p.path, p.params,
                       urlencode(q), ""))


def project_id_from_url(u: Optional[str]) -> Optional[str]:
    """Indiegogo's campaign URLs carry a slug, NOT the numeric id.

    Kept as an explicit None-returning function rather than a regex that
    might accidentally match something: every other repo in this family has
    a `_SKU_IN_URL_RE` and porting the idea here would invent ids. The id
    comes from the JSON (`projectID`) or from the card's own
    `data-qa="search-result-project:<id>"` attribute.
    """
    return None


def slug_from_url(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    m = _PROJECT_PATH_RE.match(urlparse(_html.unescape(u)).path)
    if m:
        return m.group("slug")
    m = _LEGACY_PROJECT_PATH_RE.match(urlparse(_html.unescape(u)).path)
    return m.group("slug") if m else None


def creator_from_url(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    m = _PROJECT_PATH_RE.match(urlparse(_html.unescape(u)).path)
    return m.group("creator") if m else None


def platform_from_url(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    host = (urlparse(_html.unescape(u)).netloc or "").split(":")[0].lower()
    return PLATFORM_BY_HOST.get(host)


def is_project_url(u: Optional[str]) -> bool:
    if not u:
        return False
    path = urlparse(_html.unescape(u)).path
    return bool(_PROJECT_PATH_RE.match(path) or _LEGACY_PROJECT_PATH_RE.match(path))


_NOT_A_CATEGORY = {
    "search", "explore", "info", "creators", "users", "blog", "contact",
    "terms", "about", "login", "signup", "cart", "checkout",
}


def category_from_url(u: Optional[str]) -> Optional[str]:
    """The catalogue category a search URL filters on, if any.

    Reads the site's own `?projectCatalogCategories=` parameter. Returns None
    for an unfiltered search rather than inventing "all", so a null here
    means "the site was not asked to filter" rather than "filtered to
    everything".
    """
    if not u:
        return None
    q = parse_qs(urlparse(u).query)
    vals = [v for v in q.get("projectCatalogCategories", []) if v]
    if not vals:
        return None
    first = vals[0].split(",")[0]
    return first if first.lower() not in _NOT_A_CATEGORY else None


# --------------------------------------------------------------------------
# Money and counts
# --------------------------------------------------------------------------

# Symbol -> ISO 4217, taken from the site's OWN `displayCurrencies` and
# `checkoutCurrencies` tables (inlined in every campaign page) rather than
# from a general symbol table.
#
# "kr" is deliberately absent. The site uses it for BOTH its Norwegian krone
# (id 10) and its Swedish krona (id 13) -- its own table cannot tell them
# apart from the symbol alone, and neither can this parser, so a "kr" figure
# yields a price with a null currency rather than a coin flip between NOK
# and SEK. Danish krone is "\tkr." (with a leading tab, as the site writes
# it) and IS distinguishable.
CURRENCY_BY_SYMBOL = {
    "A$": "AUD",
    "C$": "CAD",
    "Fr.": "CHF",
    "kr.": "DKK",
    "€": "EUR",
    "£": "GBP",
    "NZ$": "NZD",
    "zł": "PLN",
    "S$": "SGD",
    "HK$": "HKD",
    "$": "USD",
}

# Longest-first, so "HK$" is never swallowed by the bare "$" -- the failure
# CLAUDE.md's currency section names, which would report a Hong Kong campaign
# raising HK$6.5m as a USD figure.
_CURRENCY_SYMBOLS_LONGEST_FIRST = tuple(
    sorted(CURRENCY_BY_SYMBOL, key=len, reverse=True))

# Ambiguous symbols the site itself uses for more than one currency. Listed
# so the ambiguity is visible and testable rather than implicit in the
# absence of a key above.
AMBIGUOUS_CURRENCY_SYMBOLS = {"kr": ("NOK", "SEK")}


def currency_from_symbol(symbol: Optional[str]) -> Optional[str]:
    """Resolve one of the site's currency symbols to an ISO 4217 code.

    Returns None for an unknown OR an ambiguous symbol. Never defaults to
    "USD": a missing currency is null, per the family invariant.
    """
    if not symbol:
        return None
    s = symbol.strip()
    if s in CURRENCY_BY_SYMBOL:
        return CURRENCY_BY_SYMBOL[s]
    if s in AMBIGUOUS_CURRENCY_SYMBOLS:
        return None
    # A figure like "HK$6,556,148" may arrive with the amount attached.
    for sym in _CURRENCY_SYMBOLS_LONGEST_FIRST:
        if s.startswith(sym):
            return CURRENCY_BY_SYMBOL[sym]
    return None


# Space grouping uses a no-break variant on a rendered page so the number
# does not wrap: plain space, NBSP, narrow NBSP and thin space all occur in
# this family's measurements.
_SPACES = "    "
_MONEY_RE = re.compile(
    r"(\d{1,3}(?:[.,%s]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)" % _SPACES)


def parse_money(text: Optional[str]) -> Optional[float]:
    """Parse a rendered money string into a float.

    Indiegogo renders every figure this repo has measured with comma
    thousands separators and no decimals ("HK$6,556,148"), identically on
    /en/ and /de/ -- the site does NOT localise the number format on a card.
    The general rules are kept anyway, because that was measured on two
    locales out of nine.
    """
    if not text:
        return None
    t = _html.unescape(str(text))
    # A percentage badge next to a figure would otherwise lend it a symbol;
    # strip percentages before matching, both word orders.
    t = re.sub(r"[-+]?\s*\d+(?:[.,]\d+)?\s*%", " ", t)
    t = re.sub(r"[-+]?\s*%\s*\d+(?:[.,]\d+)?", " ", t)
    m = _MONEY_RE.search(t)
    if not m:
        return None
    raw = m.group(1)
    for sp in _SPACES[1:]:
        raw = raw.replace(sp, " ")
    has_dot, has_comma = "." in raw, "," in raw
    if has_dot and has_comma:
        # whichever comes last is the decimal point
        dec = "." if raw.rfind(".") > raw.rfind(",") else ","
        grp = "," if dec == "." else "."
        raw = raw.replace(grp, "").replace(" ", "").replace(dec, ".")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        tail = raw.rsplit(sep, 1)[1]
        # exactly three trailing digits is a thousands grouping: no currency
        # here has a three-digit subunit
        if len(tail) == 3 and raw.count(sep) >= 1:
            raw = raw.replace(sep, "").replace(" ", "")
        else:
            raw = raw.replace(" ", "").replace(sep, ".")
    else:
        raw = raw.replace(" ", "")
    try:
        return float(raw)
    except ValueError:
        return None


_ABBREV_RE = re.compile(r"^\s*([\d.,]+)\s*([kmb])\s*$", re.I)
_ABBREV_MULT = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def parse_count(text: Optional[str]) -> Optional[int]:
    """Parse a rendered count, including the site's abbreviated form.

    A card prints "3.4k" backers and "11.6k" followers where the API says
    3412 and 11637. `int("3.4k")` raises and `int(re.sub(r"\\D","", "3.4k"))`
    silently returns 34, so this is not a formality: an abbreviated count
    read naively is wrong by three orders of magnitude.

    The abbreviated value is inherently LOSSY -- "3.4k" is anything from
    3350 to 3449 -- which is one of the three reasons the API is the primary
    source and the DOM only the fallback.
    """
    if text is None:
        return None
    t = _html.unescape(str(text)).strip()
    if not t:
        return None
    m = _ABBREV_RE.match(t)
    if m:
        base = parse_money(m.group(1))
        if base is None:
            return None
        return int(round(base * _ABBREV_MULT[m.group(2).lower()]))
    digits = re.sub(r"[^\d]", "", t)
    return int(digits) if digits else None


def pct_funded(raised: Optional[float], goal: Optional[float]) -> Optional[float]:
    """Percent of goal raised, or None when that is not a real number.

    None -- not 0 -- when either figure is missing or the goal is zero. 79 of
    432 sampled campaigns publish no goal at all (an "Express crowdfunding"
    campaign has none by design), and reporting those as 0% funded while
    they hold six figures would be a fabricated number, not a missing one.
    """
    if raised is None or goal is None or goal <= 0:
        return None
    return round(raised / goal * 100.0, 2)


def days_left(campaign_end: Optional[str], now: Optional[datetime] = None) -> Optional[int]:
    """Whole days from `now` until an ISO campaign end; None if past or absent.

    Computed from the site's absolute `campaignEnd` rather than read from the
    card's "29 days left", which is localised ("29 Tage verbleiben" on /de/).
    """
    if not campaign_end:
        return None
    try:
        end = datetime.fromisoformat(str(campaign_end).replace("Z", "+00:00"))
    except ValueError:
        return None
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    delta = end - ref
    return delta.days if delta.total_seconds() > 0 else None


def _nz(v: Optional[int]) -> Optional[int]:
    """Normalise the API's not-applicable sentinel 0 to None.

    `fundedInSeconds` is never null in 432 sampled rows: a campaign that has
    not reached a goal, and one with no goal at all, both report 0. Written
    through, that reads as "funded in zero seconds".
    """
    return None if not v else int(v)


# The site's catalogue-category codes, harvested from the `catalogCategory`
# objects its own search API returns (each carries the numeric
# `projectCategory`, the display name, and the url-name its filter link
# uses). A campaign page publishes only the NUMBER, so this is what lets a
# --mode campaign row carry the same `category` string a --mode search row
# does.
#
# It is not claimed to be exhaustive -- 34 codes were observed across seven
# sorts and four page depths on 2026-09-17, and the numbering has gaps
# (15-44, 73, 74, 76 were never seen), which means either that they do not
# exist or that nothing currently uses them. An unknown code therefore
# resolves to None rather than to a plausible-looking name.
CATEGORY_BY_CODE = {
    11: "Board & card games", 12: "TTRPG", 13: "Others", 14: "Accessories",
    45: "Phones & Accessories", 46: "Audio", 47: "Camera Gear", 48: "Home",
    49: "Health & Fitness", 50: "Productivity", 51: "Travel & Outdoors",
    52: "Transportation", 53: "Fashion & Wearables", 54: "General",
    55: "Art", 56: "Film", 57: "Music", 58: "Dance & Theater",
    59: "Comics", 60: "Writing & Publishing", 61: "Photography",
    62: "Video Games", 63: "Other Creations", 64: "Local Businesses",
    65: "Education", 66: "Human Rights", 67: "Wellness", 68: "Environment",
    69: "Other Community Projects", 70: "Energy & Green Tech",
    71: "Food & Beverages", 72: "Web Series & TV Shows", 75: "Culture",
    77: "Animal Rights",
}


def category_name(value: Any) -> Optional[str]:
    """Resolve whichever shape of catalogue category the site handed us.

    The search API publishes a dict (`{"projectCategory": 11, "name": "Board
    & card games", ...}`); a campaign page publishes the bare int (50). Both
    arrive here.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get("name") or CATEGORY_BY_CODE.get(value.get("projectCategory"))
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return CATEGORY_BY_CODE.get(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


# --------------------------------------------------------------------------
# --mode search : the site's own searchProjectsForCards JSON (PRIMARY)
# --------------------------------------------------------------------------

class SearchPage:
    """One page of search results plus the site's own arithmetic about them."""

    def __init__(self, rows: List[Campaign], *, page_index: int,
                 total_results: Optional[int], total_pages: Optional[int],
                 page_size: Optional[int], capped: Optional[bool],
                 first_item: Optional[int] = None,
                 last_item: Optional[int] = None):
        self.rows = rows
        self.page_index = page_index
        self.total_results = total_results
        self.total_pages = total_pages
        self.page_size = page_size
        self.capped = capped
        self.first_item = first_item
        self.last_item = last_item

    @property
    def page(self) -> int:
        """The 1-based page number, as a human would print it."""
        return self.page_index + 1

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"<SearchPage page={self.page} rows={len(self.rows)} "
                f"total={self.total_results} capped={self.capped}>")


def parse_search_api(payload: Any, *, page_index: int, sort: str,
                     url: str = "", category: Optional[str] = None,
                     position_offset: int = 0,
                     source: str = SOURCE_DEFAULT,
                     now: Optional[datetime] = None) -> SearchPage:
    """Parse one searchProjectsForCards response into Campaign rows.

    `payload` is the decoded JSON (or its text). Raises ValueError on a body
    that is not this endpoint's shape, rather than returning [] -- a
    function that returns an empty list on a parse failure is this
    codebase's most common historical bug class, and here it would be
    indistinguishable from a genuinely empty result page.
    """
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    if not isinstance(payload, dict) or "projects" not in payload:
        raise ValueError(
            "not a searchProjectsForCards response: expected a top-level "
            f"'projects' object, got keys {sorted(payload)[:8] if isinstance(payload, dict) else type(payload).__name__}")

    projects = payload.get("projects") or {}
    items = projects.get("pagedItems")
    if items is None:
        raise ValueError("searchProjectsForCards response has no 'pagedItems'")

    rows: List[Campaign] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        rows.append(_campaign_from_api_item(
            it, page=page_index + 1, position=position_offset + i + 1,
            sort=sort, category=category, source=source, now=now))

    return SearchPage(
        rows,
        page_index=page_index,
        total_results=projects.get("totalItemCount"),
        total_pages=projects.get("totalPageCount"),
        page_size=projects.get("pageSize"),
        capped=payload.get("hasCappedResults"),
        first_item=projects.get("firstItemNumber"),
        last_item=projects.get("lastItemNumber"),
    )


def _campaign_from_api_item(it: Dict[str, Any], *, page: int, position: int,
                            sort: str, category: Optional[str],
                            source: str, now: Optional[datetime]) -> Campaign:
    url = normalise_url(it.get("url")) or ""
    platform = PLATFORM_BY_CODE.get(it.get("platform")) or platform_from_url(url)

    raised = it.get("fundsGathered")
    raised = float(raised) if isinstance(raised, (int, float)) else None
    goal = it.get("campaignGoal")
    goal = float(goal) if isinstance(goal, (int, float)) else None

    creator = it.get("creator") or {}
    creator_url = normalise_url(creator.get("homeUrl")) if isinstance(creator, dict) else None

    tags = None
    raw_tags = it.get("projectTags")
    if isinstance(raw_tags, list) and raw_tags:
        tags = [t.get("name") for t in raw_tags
                if isinstance(t, dict) and t.get("name")] or None

    end = it.get("campaignEnd")

    return Campaign(
        source=source,
        url=url,
        sku=str(it["projectID"]) if it.get("projectID") is not None else None,
        title=it.get("name") or None,
        image_url=normalise_url(it.get("imageUrl")),
        price=raised,
        currency=currency_from_symbol(it.get("currencySymbol")),
        category=category or category_name(it.get("catalogCategory")),
        price_source="api",
        platform=platform,
        sort=sort,
        page=page,
        position=position,
        creator=(creator.get("name") if isinstance(creator, dict) else None) or None,
        creator_url=creator_url,
        goal=goal,
        pct_funded=pct_funded(raised, goal),
        backers_count=it.get("backersCount"),
        followers_count=it.get("followerCount"),
        campaign_start=it.get("campaignStart") or None,
        campaign_end=end or None,
        days_left=days_left(end, now=now),
        phase_label=it.get("phaseLabel") or None,
        phase=it.get("phase") if isinstance(it.get("phase"), int) else None,
        outcome_code=it.get("campaignOutcome") if isinstance(
            it.get("campaignOutcome"), int) else None,
        funded_in_seconds=_nz(it.get("fundedInSeconds")),
        short_description=(it.get("shortDescription") or None),
        tags=tags,
    )


# --------------------------------------------------------------------------
# --mode search : the rendered cards (FALLBACK)
# --------------------------------------------------------------------------

_CARD_ID_RE = re.compile(r"search-result-project:(\d+)")


def _qa(node, value: str):
    return node.find(attrs={"data-qa": value}) if node else None


def _qa_text(node, value: str) -> Optional[str]:
    el = _qa(node, value)
    if el is None:
        return None
    t = el.get_text(" ", strip=True)
    return t or None


def parse_search_html(html: str, *, page: int, sort: str,
                      url: str = "", category: Optional[str] = None,
                      position_offset: int = 0,
                      source: str = SOURCE_DEFAULT,
                      now: Optional[datetime] = None) -> List[Campaign]:
    """Parse the server-rendered first page of a search listing.

    The FALLBACK path: used when the API call could not be made, and as the
    cross-check that the API and the page agree. Everything it reads is
    available from the API in a better form -- see the module docstring for
    the three measured ways this is worse -- so `price_source` is "dom" on
    every row it produces and a consumer can tell the two apart.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    rows: List[Campaign] = []

    cards = soup.select(SELECTORS["card"])
    for i, card in enumerate(cards):
        m = _CARD_ID_RE.search(card.get("data-qa", "") or "")
        sku = m.group(1) if m else None

        link = None
        for a in card.find_all("a", href=True):
            if is_project_url(a["href"]):
                link = normalise_url(a["href"])
                break

        name_el = _qa(card, "project-card:ProjectName")
        title = None
        if name_el is not None:
            # the <h3> carries a title= attribute holding the untruncated
            # name, and wraps an <a> holding the same text
            title = (name_el.get("title") or "").strip() or \
                name_el.get_text(" ", strip=True) or None

        img = card.find("img", src=True)

        funds_text = _qa_text(card, "project-card:FundsGathered")
        raised = parse_money(funds_text)
        currency = currency_from_symbol(funds_text)

        rows.append(Campaign(
            source=source,
            url=link or "",
            sku=sku,
            title=title,
            image_url=normalise_url(img["src"]) if img else None,
            price=raised,
            currency=currency,
            category=category,
            price_source="dom",
            platform=platform_from_url(link),
            sort=sort,
            page=page,
            position=position_offset + i + 1,
            creator=_qa_text(card, "main-creator-name"),
            backers_count=parse_count(_qa_text(card, "project-card:BackersCount")),
            followers_count=parse_count(_qa_text(card, "project-card:NumberOfFollowers")),
            phase_label=_qa_text(card, "project-card:ProjectPhaseLabel"),
        ))
    return rows


def search_result_count(html: str) -> Optional[int]:
    """The site's own result count for this query, from its data-qa hook.

    `data-qa="search-count:0"` on a served page is the site saying, in its
    own markup, that this query matched nothing -- an UNAMBIGUOUS positive
    signal that no interstitial would carry. detect_page_state consults it
    before any threshold-based heuristic, per CLAUDE.md's
    classification-order rule.
    """
    m = re.search(r'data-qa="search-count:(\d+)"', html or "")
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------
# --mode campaign : window.__INITIAL_STATE__ (PRIMARY) with a DOM fallback
# --------------------------------------------------------------------------

_STATE_KEYS = ("__INITIAL_STATE__", "__CONFIG__")


def extract_inline_state(html: str, key: str = "__INITIAL_STATE__") -> Optional[dict]:
    """Pull one `window.<key> = {...};` object out of a page.

    Brace-balanced with string/escape awareness rather than regex: the
    payload contains campaign descriptions with braces and escaped quotes in
    them, and a non-greedy `\\{.*?\\}` truncates it at the first nested
    object. Returns None when the key is absent, raises ValueError when it
    is present but unparsable -- those are different problems and a caller
    that cannot tell them apart will "handle" a site change by silently
    producing no rows.
    """
    if not html:
        return None
    needle = "window." + key
    i = html.find(needle)
    if i < 0:
        return None
    try:
        start = html.index("{", i)
    except ValueError:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(html)):
        c = html[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                blob = html[start:j + 1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"window.{key} found but did not parse as JSON "
                        f"({e.msg} at char {e.pos}); the page shape changed"
                    ) from None
    raise ValueError(f"window.{key} is present but its object never closes")


def _dig(d: Any, *path, default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def campaign_currency(state: dict) -> Tuple[Optional[str], Optional[str]]:
    """The campaign's ISO currency and how it was resolved.

    A campaign page states the code outright -- `checkoutCurrencies[0]`
    carries `{"shortName": "HKD", "symbol": "HK$"}` -- which is a FACT and
    outranks any symbol reading. That is the one thing --mode campaign knows
    that --mode search cannot: the search API publishes only a symbol, and
    the site uses "kr" for two different currencies.
    """
    checkout = state.get("checkoutCurrencies")
    if isinstance(checkout, list) and checkout and isinstance(checkout[0], dict):
        iso = checkout[0].get("shortName")
        if iso:
            return str(iso), "checkout_currency"
        sym = checkout[0].get("symbol")
        if sym:
            resolved = currency_from_symbol(sym)
            if resolved:
                return resolved, "symbol"
    return None, None


def parse_campaign(html: str, *, url: str = "", sort: Optional[str] = None,
                   source: str = SOURCE_DEFAULT,
                   now: Optional[datetime] = None) -> Optional[Campaign]:
    """Parse a campaign page into one Campaign row.

    Returns None when the page carries no campaign state at all (a 404, a
    challenge, a search page handed here by mistake). Raises ValueError when
    the state is present but malformed -- see extract_inline_state.
    """
    state = extract_inline_state(html, "__INITIAL_STATE__")
    if not state:
        return None
    project = _dig(state, "projectContext", "project")
    if not isinstance(project, dict) or project.get("projectID") is None:
        return None

    stats = _dig(state, "projectState", "statistics", default={}) or {}
    raised = stats.get("totalFundsGathered")
    if raised is None:
        raised = stats.get("fundsGathered")
    raised = float(raised) if isinstance(raised, (int, float)) else None

    goal = project.get("campaignGoal")
    goal = float(goal) if isinstance(goal, (int, float)) else None

    iso, iso_source = campaign_currency(state)

    creator = project.get("creator") if isinstance(project.get("creator"), dict) else {}
    creator_url = normalise_url(creator.get("creatorPageUrl") or creator.get("homeUrl"))
    if creator_url and creator_url.startswith("/"):
        creator_url = f"https://{CANONICAL_HOST}{creator_url}"

    end = project.get("campaignEnd")
    stretch = _dig(state, "projectState", "stretchGoals")
    followers = _dig(state, "projectFollowerFacts", "followersCount")

    tile = project.get("tileImageUrl")
    if not tile and project.get("tileImageFileName"):
        # The page publishes the file name without its CDN prefix on some
        # campaigns; reconstructed from the site's own image host rather
        # than left null, and marked by being the only constructed URL here.
        tile = (f"https://cdn.images.indiegogo.com/projectimage/projects/"
                f"{project['projectID']}/{project['tileImageFileName']}")

    return Campaign(
        source=source,
        url=normalise_url(url) or "",
        sku=str(project["projectID"]),
        title=project.get("name") or None,
        image_url=normalise_url(tile),
        price=raised,
        currency=iso,
        category=category_name(project.get("catalogCategory")),
        price_source="detail",
        platform="indiegogo",
        sort=sort,
        page=1,
        position=1,
        creator=project.get("creatorName") or (creator.get("name") if creator else None),
        creator_url=creator_url,
        goal=goal,
        pct_funded=pct_funded(raised, goal),
        backers_count=stats.get("totalBackersCount") or stats.get("backersCount"),
        followers_count=followers if isinstance(followers, int) else None,
        campaign_start=project.get("campaignStart") or None,
        campaign_end=end or None,
        days_left=days_left(end, now=now),
        phase_label=project.get("phaseLabel") or None,
        phase=project.get("phase") if isinstance(project.get("phase"), int) else None,
        outcome_code=stats.get("campaignOutcome") if isinstance(
            stats.get("campaignOutcome"), int) else project.get("campaignOutcome"),
        funded_in_seconds=_nz(project.get("fundedInSeconds")),
        short_description=project.get("shortDescription") or None,
        currency_iso_source=iso_source,
        campaign_day=stats.get("campaignDay"),
        stretch_goal_count=len(stretch) if isinstance(stretch, list) else None,
        reward_count=len(parse_rewards(html, url=url)) or None,
        published_date=project.get("publishedDate") or None,
    )


# --------------------------------------------------------------------------
# --mode rewards : the reward boxes on a campaign page
# --------------------------------------------------------------------------

_REWARD_ID_RE = re.compile(r"reward-box:(\d+)")


def _reward_price(box, qa: str) -> Optional[float]:
    el = box.find(attrs={"data-qa": qa})
    return parse_money(el.get_text(" ", strip=True)) if el is not None else None


_PLEDGED_RE = re.compile(r"pledged\s+([\d.,]+[kmb]?)\s+times", re.I)


def parse_rewards(html: str, *, url: str = "", project_id: Optional[str] = None,
                  project_title: Optional[str] = None,
                  category: Optional[str] = None,
                  currency: Optional[str] = None,
                  source: str = SOURCE_DEFAULT) -> List[Reward]:
    """Parse the reward boxes on a campaign page into Reward rows.

    Anchored on `data-qa="reward-box:<productID>"`, which is both the
    container and the id, so a reward can never pick up a neighbour's price:
    every read below is scoped to the box, never to the document. That is
    the tile-scoping rule this family learned on MediaMarkt, applied where
    it is cheap -- the site has handed us an element that covers exactly one
    product, so there is no widening walk to get wrong.
    """
    soup = BeautifulSoup(html or "", "html.parser")

    if project_id is None or project_title is None or currency is None:
        state = None
        try:
            state = extract_inline_state(html, "__INITIAL_STATE__")
        except ValueError:
            state = None
        if state:
            project = _dig(state, "projectContext", "project", default={}) or {}
            if project_id is None and project.get("projectID") is not None:
                project_id = str(project["projectID"])
            if project_title is None:
                project_title = project.get("name") or None
            if category is None:
                category = category_name(project.get("catalogCategory"))
            if currency is None:
                currency = campaign_currency(state)[0]

    rows: List[Reward] = []
    for i, box in enumerate(soup.select(SELECTORS["reward_box"])):
        m = _REWARD_ID_RE.search(box.get("data-qa", "") or "")
        sku = m.group(1) if m else None

        effective = _reward_price(box, "price-type:Effective")
        old = _reward_price(box, "price-type:Old")
        low30 = _reward_price(box, "price-type:ThirtyDaysLowest")

        # A discount only when there is a struck-through price ABOVE the
        # current one. Never computed from the 30-day low, and never allowed
        # to come out negative -- see output_writer.Reward.lowest_price_30d.
        discount = None
        if effective is not None and old is not None and old > effective > 0:
            discount = round((old - effective) / old * 100.0, 2)

        name_el = box.find(attrs={"data-qa": "reward-box-element:Name"})
        abstract_el = box.find(attrs={"data-qa": "reward-box-element:Abstract"})
        img = box.find("img", src=True)

        pledged = None
        pm = _PLEDGED_RE.search(box.get_text(" ", strip=True))
        if pm:
            pledged = parse_count(pm.group(1))

        # The site puts these on the box element itself as lowercased
        # attributes; BeautifulSoup preserves them as given.
        remaining = box.get("remainingstocklimit")
        has_limited = box.get("haslimitedstock")
        is_digital = box.get("isdigital")

        delivery = None
        action = box.find(attrs={"data-qa": "reward-box-action"})
        if action is not None:
            holder = action.find(attrs={"estimateddeliveryat": True})
            if holder is None and action.get("estimateddeliveryat"):
                holder = action
            if holder is not None:
                delivery = holder.get("estimateddeliveryat") or None

        rows.append(Reward(
            source=source,
            url=f"{normalise_url(url)}#/product/{sku}" if (url and sku) else (normalise_url(url) or ""),
            sku=sku,
            title=(name_el.get_text(" ", strip=True) if name_el is not None else None) or None,
            image_url=normalise_url(img["src"]) if img else None,
            price=effective,
            currency=currency,
            category=category,
            project_id=project_id,
            project_title=project_title,
            position=i + 1,
            original_price=old,
            lowest_price_30d=low30,
            discount_pct=discount,
            abstract=(abstract_el.get_text(" ", strip=True) if abstract_el is not None else None) or None,
            is_featured=box.find(attrs={"data-qa": "reward-card-badge:Featured"}) is not None,
            is_digital=_as_bool(is_digital),
            purchased_count=pledged,
            remaining_stock=int(remaining) if (remaining or "").isdigit() else None,
            has_limited_stock=_as_bool(has_limited),
            estimated_delivery=delivery,
        ))
    return rows


def _as_bool(v: Any) -> Optional[bool]:
    """Parse the site's string attribute booleans; unknown stays None."""
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


# --------------------------------------------------------------------------
# Page 1 comes inlined; pages 2+ come from the API. Same object either way.
# --------------------------------------------------------------------------

# The Vue component the search page hydrates its grid from. Its props carry
# BOTH the first page of results and the search parameters the site derived
# from the URL.
SEARCH_COMPONENT = "App.Components.Search.SearchProjectsResults"

_COMPONENT_RE_TMPL = (r"App\.registerComponent\(\s*'[^']*'\s*,\s*'%s'\s*,"
                      r"[^,]*,\s*")


def extract_component_props(html: str, component: str) -> Optional[dict]:
    """Pull one `App.registerComponent(id, name, Ctor, {props})` payload out.

    Brace-balanced for the same reason extract_inline_state is.
    """
    if not html:
        return None
    m = re.search(_COMPONENT_RE_TMPL % re.escape(component), html)
    if not m:
        return None
    try:
        start = html.index("{", m.end())
    except ValueError:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(html)):
        c = html[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    payload = json.loads(html[start:j + 1])
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"{component} props found but did not parse as JSON "
                        f"({e.msg} at char {e.pos})") from None
                return payload.get("props", payload)
    raise ValueError(f"{component} props object never closes")


def extract_inline_search_result(html: str) -> Optional[dict]:
    """Page 1's results, as the search page inlines them.

    This is the SAME object shape `searchProjectsForCards` returns over
    XHR -- verified key-for-key on a real capture, top level and item level,
    with no key present in one and absent from the other. That is what lets
    parse_search_api() read page 1 out of the HTML and pages 2+ out of the
    API with one code path, so the two routes cannot drift.

    It matters that page 1 comes from here rather than from the rendered
    cards: the raw document contains ZERO card elements (measured: 137 KB,
    0 `data-qa="search-result-project:"` matches) and the 24 cards exist
    only after the page hydrates. Reading the DOM instead would mean waiting
    for a paint this repo does not otherwise need.
    """
    props = extract_component_props(html, SEARCH_COMPONENT)
    if not props:
        return None
    result = props.get("result")
    return result if isinstance(result, dict) and "projects" in result else None


def extract_search_params(html: str) -> Optional[dict]:
    """The search parameters the SITE derived from the current URL.

    Preferred over rebuilding them from the query string with
    build_search_body(): it is the site's own mapping, so a filter this repo
    has never seen still round-trips, and a mapping the site changes cannot
    silently drift from ours. build_search_body() stays as the path for a
    caller that has no page 1 in hand.
    """
    props = extract_component_props(html, SEARCH_COMPONENT)
    if not props:
        return None
    params = props.get("params")
    return params if isinstance(params, dict) else None


def body_from_site_params(params: dict, page_index: int,
                          sort: Optional[str] = None) -> Dict[str, Any]:
    """Turn the site's own `params` object into a searchProjectsForCards body.

    The site writes `null` where the request body wants an empty list, so
    the template's shape is kept and only the keys the site actually set are
    copied over it.
    """
    body = json.loads(json.dumps(SEARCH_BODY_TEMPLATE))
    for key, value in (params or {}).items():
        if value is None or key not in body:
            continue
        body[key] = value
    if params.get("creatorName"):
        body["creator"] = {"creatorID": params.get("creatorID"),
                           "name": params["creatorName"]}
    if sort is not None:
        if sort not in SORT_TYPES:
            raise ValueError(f"unknown sort {sort!r}")
        body["sortType"] = SORT_TYPES[sort]
    body["pageIndex"] = page_index
    return body


# --------------------------------------------------------------------------
# Page state
# --------------------------------------------------------------------------

# Vendor markers for a page the site refused to serve.
#
# `cf-turnstile` is deliberately ABSENT, and this is the ninth site in this
# family to measure why. Counted on 2026-09-17 across nine captures:
#
#     capture                      cf-turnstile   challenges.cloudflare.com
#     6 served pages (en + de,          1 each            0 each
#       listing, campaign, rewards,
#       empty search, legacy URL)
#     the site's own 404 page             1                 0
#     a real challenge, via browser       1                 6
#     a real challenge, via curl          0                 5
#
# It fires on every page fetched through the Scraping Browser -- whose
# auto-solve extension injects
# `chrome-extension://.../content/captcha/turnstile/hunter.js` with
# `data-ts-input="cf-turnstile-response"` into everything it loads -- and it
# is ABSENT from the one challenge fetched without that extension. A marker
# that is present on every good page and missing from a bad one is worse
# than no marker: carrying it would report exit 3 on a 630 KB page holding
# the full result set.
#
# `challenges.cloudflare.com` was 0 on all seven served-or-404 captures and
# 5-6 on both challenges, so that is the one that is carried.
BOT_CHALLENGE_MARKERS = (
    "challenges.cloudflare.com",
    "just a moment...",
    "cf-mitigated",
    "cf_chl_opt",
    "/cdn-cgi/challenge-platform/",
)

# A page the site really served is built out of the site's own assets; an
# interstitial is not. Measured: 37-55 references on every served capture, 0
# on both challenge pages and 0 on the 404. It is a SECONDARY signal --
# strong enough to catch a refusal that carries no vendor marker at all
# (including Chromium's own network-error page, which carries the site's
# hostname in its <title> and would otherwise read as a real page), too weak
# to outrank an unambiguous positive one. See detect_page_state's ordering.
SITE_ASSET_HOST = "cdn.static.indiegogo.com"
MIN_ASSET_REFERENCES = 2

# The site's own 404. Distinguished from a refusal because the two want
# different answers: a 404 is a correct response to a URL that does not
# exist (most often a gamefound.com-hosted campaign asked for on
# indiegogo.com), and retrying or paying a solver for it is waste.
NOT_FOUND_MARKERS = ("404: Page Not Found", "gfu-error-page")

# Bounded, because a refusal page is small and unescaping 630 KB on every
# fetch buys nothing -- and risks a campaign title deep in a grid reading as
# a marker. The largest refusal measured here is 30,639 bytes.
MARKER_SCAN_BYTES = 65536


def _normalised_head(html: str) -> str:
    """The first MARKER_SCAN_BYTES, entity-unescaped and lowercased.

    Unescaped because an edge may entity-escape the punctuation in its own
    marker (`errors&#46;edgesuite&#46;net`), so a literal marker matches a
    browser's serialised DOM and silently misses the same page fetched with
    an HTTP client. Normalising once is cheaper and safer than listing both
    spellings of every marker added later.
    """
    return _html.unescape((html or "")[:MARKER_SCAN_BYTES]).lower()


def challenge_marker(html: str, headers: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Which challenge marker matched, or None. Named so a log can say which."""
    if headers:
        for k, v in headers.items():
            if k.lower() == "cf-mitigated" and str(v).strip().lower() == "challenge":
                return "cf-mitigated: challenge"
    head = _normalised_head(html)
    for m in BOT_CHALLENGE_MARKERS:
        if m in head:
            return m
    return None


def detect_page_state(html: str, *, status: Optional[int] = None,
                      url: Optional[str] = None,
                      headers: Optional[Dict[str, str]] = None,
                      mode: str = "search") -> str:
    """Classify one fetched page: content / empty / not_found / captcha / blocked / unpainted.

    Ordered by how much each signal PROVES, not by how cheap it is -- the
    ordering trap CLAUDE.md records from tokopedia-scraper, where a
    threshold-based heuristic ran ahead of an unambiguous positive marker
    and reported exit 3 for a correct answer.

    So, in order:

    1. The site's own "this query matched nothing" count. `search-count:0`
       is the site stating, in its own markup, that the search was run and
       returned nothing. No interstitial carries it. It is checked FIRST,
       ahead of the asset-reference threshold, because a correct empty
       answer must never be reported as a block.
    2. A real result payload. If page 1's inlined results or a rendered card
       is present, the page was served, whatever else is on it.
    3. The site's own 404.
    4. A challenge marker, or an HTTP status that means refusal.
    5. The structural asset-reference check, which catches a refusal with no
       vendor marker at all.
    """
    text = html or ""

    # (1) the site's own unambiguous no-results signal
    count = search_result_count(text)
    if count == 0:
        return "empty"

    # (2) a payload means it was served
    try:
        if extract_inline_search_result(text) is not None:
            return "content"
    except ValueError:
        pass
    if count and count > 0 and SITE_ASSET_HOST in text:
        return "content"
    if 'data-qa="search-result-project:' in text:
        return "content"
    if mode in ("campaign", "rewards") and "window.__INITIAL_STATE__" in text:
        if '"projectContext"' in text:
            return "content"

    # (3) the site's own 404 -- a correct answer, not a refusal
    head = _normalised_head(text)
    if any(m.lower() in head for m in NOT_FOUND_MARKERS) or status == 404:
        return "not_found"

    # (4) an explicit refusal
    marker = challenge_marker(text, headers)
    if marker:
        # Cloudflare's managed challenge renders a Turnstile widget, which is
        # solvable; its plain block page does not. Either way the run is
        # stopped, but only one of them is worth paying a solver for.
        if "challenges.cloudflare.com" in head or "cf_chl_opt" in head:
            return "captcha"
        return "blocked"
    if status in (401, 403, 429) or (status is not None and 500 <= status < 600):
        return "blocked"

    # (5) structural: a served page is built out of the site's own assets
    if text.count(SITE_ASSET_HOST) >= MIN_ASSET_REFERENCES:
        # Served, but nothing parsed out of it yet. On this site that is a
        # real state rather than a fallback: the raw search document holds
        # zero cards and hydrates its grid client-side, so a snapshot taken
        # too early is "served but not painted", which WAITS rather than
        # retrying.
        return "unpainted"

    return "blocked"
