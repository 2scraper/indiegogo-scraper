"""
output_writer.py
-----------------
Shared row models + JSON/CSV writers used by all three scrapers.

Two kinds of row, not one
-------------------------
Indiegogo's discovery surface describes a CAMPAIGN — a thing being funded,
with a creator, a goal, a deadline and a running total. Its campaign pages
additionally list REWARDS: the individual perks a backer can buy, each with
its own price and stock. Those are different objects, and folding them into
one dataclass would leave `remaining_stock`/`estimated_delivery` permanently
null on every campaign row and `backers_count`/`campaign_end` permanently
null on every reward row — the exact column CLAUDE.md's family rule says
should not exist ("a column that is null on every row of every run should
not exist"). So there are two row classes, the way amazon-scraper added a
second (`Review`) and transfermarkt-scraper a second (`Transfer`):

    Campaign   --mode search, campaign
    Reward     --mode rewards

Both open on the SAME family prefix — `source`, `scraped_at`, `url`, `sku`,
`title`, `image_url`, `price`, `currency`, `category` — so a consumer
already written against another repo in this family reads the first nine
columns unchanged.

`sku` is reused for whichever id is actually unique for the mode, per the
family rule ("dedupe on whatever is actually unique"): Indiegogo's numeric
`projectID` for Campaign rows, the reward's own `productID` for Reward rows.
One campaign has many rewards, so the project id is NOT unique on a rewards
run.

What `price` holds, and why that is not a stretch
------------------------------------------------
On a Reward row `price` is a price in the ordinary sense — what a backer
pays for that perk.

On a Campaign row `price` is the campaign's FUNDS RAISED so far. That is
the number a crowdfunding watcher actually monitors, it is denominated in a
currency the site states, and it moves between runs — which is precisely
what diff_runs.py's added/removed/changed-by-sku model was built for. A
campaign's `goal` is a separate column because it does not move; the ratio
between them is `pct_funded`.

Why `sort` is a COLUMN and not a sidecar field
----------------------------------------------
Indiegogo's search API returns at most 24 rows per page and caps every query
(see `run_meta`), so the sort order decides WHICH campaigns end up in the
file, not merely the order they sit in. Two runs taken under different sorts
are therefore different SAMPLES of the same query, and comparing them would
report an arbitrary slice difference as campaigns appearing and vanishing.
It rides on every row so that a merged file stays self-describing, and
diff_runs.py refuses to compare two runs whose rows disagree about it —
the same reasoning bbb-scraper applied to its own result ordering.
"""

import csv
import json
from dataclasses import dataclass, asdict, field, fields
from datetime import datetime, timezone
from typing import Optional, List, Set, Sequence, Any, Type


# Every row this repo writes was read from a page served by indiegogo.com.
# It is kept as the family's provenance column, and it is NOT the same thing
# as the `platform` column below: indiegogo.com's own search results include
# campaigns hosted on gamefound.com, so `source` says where the row was READ
# and `platform` says where the campaign actually LIVES. See product_parser.
SOURCE_DEFAULT = "indiegogo.com"


@dataclass
class Campaign:
    source: str = SOURCE_DEFAULT
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    url: str = ""
    sku: Optional[str] = None          # Indiegogo's numeric projectID
    title: Optional[str] = None        # the campaign's own name
    image_url: Optional[str] = None    # tile image
    # Funds raised so far, in `currency`. See the module docstring for why a
    # running total lives in the family's `price` column.
    price: Optional[float] = None
    # ISO 4217 where the site states or implies one unambiguously, else None.
    # Deliberately never a defaulted "USD": the search API publishes only a
    # SYMBOL, and one of the symbols the site itself uses ("kr") is shared by
    # two of its own currencies. See product_parser.CURRENCY_BY_SYMBOL.
    currency: Optional[str] = None
    # `category` closes the nine-column family prefix; `price_source` follows
    # it rather than preceding it, so Campaign and Reward open on byte-
    # identical columns. (Asserted in smoke_test.py -- the check was written
    # first and immediately caught this pair in the wrong order.)
    #
    # Where `price` was read from:
    #   "api"      — the site's own searchProjectsForCards JSON, which
    #                publishes an exact float (e.g. 12576.05).
    #   "dom"      — the rendered card, whose figure is ROUNDED for display
    #                (the same campaign reads "€12,576" there).
    #   "detail"   — a --mode campaign run, reading the campaign page's own
    #                inlined statistics object.
    # This is real provenance rather than the tile-overlay bookkeeping the
    # e-commerce members of this family carry: the two sources genuinely
    # disagree in the last two decimal places, and a diff between an api-read
    # run and a dom-read run would otherwise report every row as changed.
    # The site's own catalogue category NAME, as the fetched page spelled
    # it. This is DISPLAY TEXT and it is localised: the same campaign reads
    # "Board & card games" on /en/, "Brett- & Kartenspiele" on /de/ and
    # "交通" on /zh/. Do not join on it -- join on `category_code`.
    category: Optional[str] = None
    price_source: Optional[str] = None

    # ---- Indiegogo-specific, appended so the family prefix above is stable ----
    # "indiegogo" or "gamefound". indiegogo.com's search results mix in
    # campaigns hosted on gamefound.com (measured: 49 of 120 rows under the
    # site's default sort on 2026-09-17), and those are NOT reachable as
    # Indiegogo campaign pages — /en/projects/<creator>/<slug> answers 404
    # for them. A consumer filtering for Indiegogo campaigns needs this
    # column, and --mode campaign refuses a row that does not carry
    # "indiegogo".
    platform: Optional[str] = None
    # The site's numeric catalogue-category id. Locale-independent, and the
    # thing to join or group on.
    #
    # It exists because `category` alone was measurably inconsistent BETWEEN
    # MODES on a non-English locale: the search API publishes the localised
    # name, while a campaign page publishes only this number, which
    # product_parser resolves through an English-only table harvested from
    # an /en/ run. The same German campaign therefore came back as
    # "Produktivität" from --mode search and "Productivity" from --mode
    # campaign. Rather than pick a language and throw the other away, the
    # id is carried alongside the text and the text is documented as
    # display-only.
    category_code: Optional[int] = None
    sort: Optional[str] = None         # the ordering this row was sampled under
    page: Optional[int] = None         # which listing page (1-based, as printed)
    position: Optional[int] = None     # position within the whole run (1-based)

    creator: Optional[str] = None
    creator_url: Optional[str] = None
    goal: Optional[float] = None       # campaignGoal; often null — not every campaign publishes one
    pct_funded: Optional[float] = None # price/goal*100, only when BOTH are known and goal > 0
    backers_count: Optional[int] = None
    followers_count: Optional[int] = None
    # ISO 8601 timestamps exactly as the site publishes them (UTC, "Z").
    # Preferred over the card's rendered "29 days left", which is localised
    # ("29 Tage verbleiben" on /de/) and relative to the moment of the fetch.
    campaign_start: Optional[str] = None
    campaign_end: Optional[str] = None
    days_left: Optional[int] = None    # computed from campaign_end at scrape time
    phase_label: Optional[str] = None  # the site's own label, e.g. "Crowdfunding"
    phase: Optional[int] = None        # its numeric code
    # The site's raw numeric `campaignOutcome`, carried through WITHOUT a
    # label. It is tempting to read it as "did this campaign succeed", and
    # that reading is measurably wrong: on 2026-09-17, 20 of 60 sampled rows
    # with outcome=0 had already passed their goal (one raised 640,492 on a
    # 50,000 goal) while every outcome=1 row sat in a concluded phase
    # ("Crowdfunding - Funded", "Express crowdfunding"). It tracks whether
    # the funding phase has ENDED, not whether it worked. Only 0 and 1 were
    # ever observed, and the site publishes no names for them, so inventing
    # "successful"/"failed" here would be presenting a guess as a fact.
    # `phase_label` is the site's own human-readable status and is what a
    # consumer should read instead.
    outcome_code: Optional[int] = None
    # How long the campaign took to reach its goal. The API returns 0 rather
    # than null when this does not apply -- an upcoming campaign that has
    # raised nothing, or an "Express crowdfunding" campaign with no goal at
    # all, both report 0 (28 of 60 sampled rows). Written through, that says
    # "funded in zero seconds"; it is normalised to None here, and a check
    # pins that no row carries the sentinel.
    funded_in_seconds: Optional[int] = None
    short_description: Optional[str] = None
    tags: Optional[List[str]] = None

    # ---- populated by --mode campaign (a campaign page) only ----
    # Null on a search run. `currency_iso_source` records how `currency` was
    # resolved, because a campaign page states the ISO code outright while a
    # search row only carries a symbol.
    currency_iso_source: Optional[str] = None
    campaign_day: Optional[int] = None
    stretch_goal_count: Optional[int] = None
    reward_count: Optional[int] = None
    published_date: Optional[str] = None


@dataclass
class Reward:
    """One purchasable perk on a campaign page.

    Deliberately NOT a Campaign: a campaign has many rewards, which is
    exactly why `sku` here is the reward's own productID and not the
    project's — see the module docstring.
    """
    source: str = SOURCE_DEFAULT
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    url: str = ""
    sku: Optional[str] = None          # the reward's productID
    title: Optional[str] = None        # the reward's name
    image_url: Optional[str] = None
    price: Optional[float] = None      # what a backer pays now (the effective price)
    currency: Optional[str] = None
    category: Optional[str] = None     # the parent campaign's catalogue category

    # ---- Indiegogo-specific ----
    project_id: Optional[str] = None
    project_title: Optional[str] = None
    position: Optional[int] = None     # order on the page, 1-based
    # The pre-discount price, when the site shows one struck through. Kept
    # distinct from `price` the way the e-commerce members of this family
    # keep `original_price`: a reward with no discount leaves this null
    # rather than repeating `price`.
    original_price: Optional[float] = None
    # The EU Omnibus "lowest price in the last 30 days" disclosure, which
    # every European seller must print beside a reduced price. It sits in
    # the SAME reward box as the struck-through old price and in nearly
    # identical markup, told apart only by its data-qa value
    # (`price-type:ThirtyDaysLowest` vs `price-type:Old`). Read as a
    # was-price it produces a zero or negative discount on a genuinely
    # discounted reward -- measured on one capture where the 30-day low
    # (HK$13,723.00) equals the CURRENT price while the real old price is
    # HK$15,677.00. It gets its own column, and discount_pct is computed
    # only from `original_price`.
    lowest_price_30d: Optional[float] = None
    # None -- not 0 -- when there is no struck-through price to compute it
    # from, and never negative: a negative would mean the two figures were
    # not what they were taken for.
    discount_pct: Optional[float] = None
    abstract: Optional[str] = None     # the short "388+32GB+1TB"-style variant line
    is_featured: Optional[bool] = None
    is_digital: Optional[bool] = None
    purchased_count: Optional[int] = None
    remaining_stock: Optional[int] = None   # null when the reward is not stock-limited
    has_limited_stock: Optional[bool] = None
    estimated_delivery: Optional[str] = None


# Row class by --mode, so an engine maps its mode to a schema in one place.
ROW_CLASS_BY_MODE = {
    "search": Campaign,
    "campaign": Campaign,
    "rewards": Reward,
}

# Modes whose rows are one-per-sku, and therefore safe to dedupe on `sku` and
# to hand to diff_runs.py. All three qualify: a search or campaign run is one
# row per projectID, and a rewards run is one row per productID (a DIFFERENT
# kind of id — see the module docstring — which is fine here, because
# diff_runs only requires that `sku` be unique WITHIN one run).
UNIQUE_BY_SKU_MODES = ("search", "campaign", "rewards")


def dedupe_by_key(rows: Sequence[Any], seen: Set[str], key: str = "sku") -> List[Any]:
    """Drop rows whose key already appeared earlier in this same run.

    `seen` is mutated in place, so callers thread the same set across pages —
    a repeated page then re-parses without duplicating its rows into the
    final output. That matters more here than on a URL-paginated site: this
    repo's pages are addressed by a POST body's `pageIndex`, and an
    off-by-one there would otherwise double every row instead of failing.

    A row with no key is always kept: there is nothing to check a duplicate
    against, and dropping it would be a silent data loss rather than a
    duplicate removal.
    """
    fresh = []
    for r in rows:
        val = getattr(r, key, None)
        if val is None or val not in seen:
            if val is not None:
                seen.add(val)
            fresh.append(r)
    return fresh


def dedupe_by_sku(rows: Sequence[Any], seen: Set[str]) -> List[Any]:
    return dedupe_by_key(rows, seen, key="sku")


# CSV cannot hold a list (tags). Joining with " | " keeps the cell readable in
# a spreadsheet and round-trippable by splitting on the same separator; the
# JSON output keeps the real list.
LIST_CSV_SEPARATOR = " | "


def _csv_value(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return LIST_CSV_SEPARATOR.join(str(x) for x in v)
    return v


def write_json(rows: Sequence[Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in rows], f, ensure_ascii=False, indent=2)


def write_csv(rows: Sequence[Any], path: str, row_cls: Type = Campaign) -> None:
    # An empty result still gets the header row, from `row_cls` rather than
    # the first row, so a mode that finds nothing still writes the columns
    # that mode would have used.
    fieldnames = [f.name for f in fields(row_cls)]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _csv_value(v) for k, v in asdict(r).items()})


# Exit code used when a run completes but produced nothing.
EXIT_NO_PRODUCTS = 4

# Exit code for a run blocked by a bot-check/challenge page before parsing
# even started.
EXIT_BLOCKED = 3

# Exit code for a run that gathered SOME rows and then stopped early.
EXIT_PARTIAL = 6


# Exit code for a run that never GOT its pages: a navigation timeout, a dead
# or unauthenticated proxy, a DNS failure, or an edge answering with
# something that is not the page that was asked for.
#
# Distinct from EXIT_NO_PRODUCTS because those are opposite facts. Exit 4 is
# a statement about the CATALOGUE — "we asked, and the answer was nothing" —
# so handing it to a run that never reached the site tells a pipeline the
# listing is empty when nothing was read at all.
#
# 5 rather than a new number, and 5 rather than EXIT_PARTIAL:
#
#   * this family's contract already reserves 5 for a transport failure
#     (scraper_api_client has used it for a remote API error since it was
#     written), so this needs no new code and no per-repo table for a caller
#     driving more than one of these scrapers;
#   * EXIT_PARTIAL (6) means "some rows were gathered and the output is
#     incomplete". A run holding nothing writes no output at all, so a
#     consumer that reads the file on a 6 finds either nothing or the
#     PREVIOUS run's good data, which `save` deliberately does not
#     overwrite. Exit 5 promises no file.
#
# Deliberately NOT applied when rows WERE gathered: a timeout on page 7 of
# 10 is a partial run (exit 6, output written), which is already right. This
# decides only what a run holding nothing reports.
EXIT_FETCH_FAILED = 5

# Exit code for a failure in one of THIS PROJECT's own 2Captcha-product calls
# -- the Fingerprint API rejecting a request (bad key, bad --fp-tags, rate
# limit), or a Scraping Browser CDP connection failing (e.g. profile_locked)
# -- as opposed to EXIT_BLOCKED (the TARGET SITE refusing a page) or an
# uncaught crash (1). Per CLAUDE.md's family exit-code contract ("5" =
# "remote API error"). Deliberately NOT what a captcha-solve failure gets:
# per that same document's captcha section, a solver error is a WARNING that
# lets the run continue (see captcha_solver.py and each engine's
# handle_captcha_if_present) -- exit 5 is for calls the user explicitly
# opted into (--fingerprint, --cdp-endpoint) where silently continuing
# without them would hide a billing/plan/profile-lock problem rather than a
# page the site declined to serve.
EXIT_REMOTE_API_ERROR = 5


class RemoteAPIError(RuntimeError):
    """A 2Captcha product call (Fingerprint API, Scraping Browser CDP
    connect) failed on its own terms, not the target site blocking a page.

    Raised by fingerprint_client.get_fingerprint and by each engine's
    --cdp-endpoint connect path; caught once at each engine's entry point
    and mapped to EXIT_REMOTE_API_ERROR, so the three engines cannot drift
    on which of 1 (crash) / 3 (blocked) / 5 (remote API error) a given
    failure gets.
    """


def write_run_meta(out_prefix: str, meta: dict) -> str:
    """Write a run-metadata sidecar next to the output, return its path."""
    path = f"{out_prefix}.meta.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[+] Wrote run metadata -> {path} (status={meta.get('status')})")
    return path


def run_meta(status: str, stop_reason: str, pages_requested: int,
             pages_completed: int, start_url: str, final_url: str,
             products: int, pages_failed: Optional[List[int]] = None,
             mode: str = "search", source: str = SOURCE_DEFAULT,
             sort: Optional[str] = None,
             total_results: Optional[int] = None,
             pages_available: Optional[int] = None,
             capped_by_site: Optional[bool] = None,
             reachable_max: Optional[int] = None) -> dict:
    """Build the metadata dict for a finished run.

    The last four fields are the site's own arithmetic about the query, and
    they exist because "complete" and "exhaustive" are different words here.
    Indiegogo caps any search at 10,000 results / 417 pages of 24 and says so
    itself (`hasCappedResults`), so a run that fetched every page the site
    will serve is honestly `status: complete` while still being a SAMPLE of
    a larger result set. A sidecar saying only "complete" would be lying by
    omission, and diff_runs.py needs `capped_by_site` to know that a row
    which vanished between two runs may simply have fallen outside this
    run's slice rather than being delisted. Same reasoning as bbb-scraper's.

    `sort` rides here as well as on every row: on the rows so a merged file
    stays self-describing, here so a consumer reading only the sidecar can
    tell which sample it has.
    """
    return {
        "source": source,
        "mode": mode,
        "status": status,
        "stop_reason": stop_reason,
        "sort": sort,
        "pages_requested": pages_requested,
        "pages_completed": pages_completed,
        "pages_failed": pages_failed or [],
        "products": products,
        "total_results": total_results,
        "pages_available": pages_available,
        "capped_by_site": capped_by_site,
        "reachable_max": reachable_max,
        "start_url": start_url,
        "final_url": final_url,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def save(rows: Sequence[Any], out_prefix: str, fmt: str,
         allow_empty: bool = False, row_cls: Type = Campaign) -> int:
    """Write JSON/CSV and return a process exit code.

    On zero rows, nothing is written at all unless `allow_empty` — see the
    family invariant in CLAUDE.md §8: a run that finds nothing must not
    silently replace yesterday's good output with an empty file.
    """
    if not rows and not allow_empty:
        print(f"[!] 0 rows — refusing to write {out_prefix}.json/.csv, so an "
              f"earlier good result isn't overwritten with an empty one. "
              f"Pass --allow-empty if an empty result is the expected answer.")
        return EXIT_NO_PRODUCTS

    if fmt in ("json", "both"):
        write_json(rows, f"{out_prefix}.json")
        print(f"[+] Saved {len(rows)} row(s) -> {out_prefix}.json")
    if fmt in ("csv", "both"):
        write_csv(rows, f"{out_prefix}.csv", row_cls=row_cls)
        print(f"[+] Saved {len(rows)} row(s) -> {out_prefix}.csv")
    return 0 if rows else EXIT_NO_PRODUCTS


# Stop reasons that mean the run saw everything there was to see.
#
# `site_result_cap` is one of them, and that is deliberate rather than
# sloppy: it means the run walked to the end of what indiegogo.com will
# serve for this query. The run really is complete; `capped_by_site` in the
# sidecar is what tells a consumer it is also a sample.
COMPLETE_STOP_REASONS = ("completed", "pagination_exhausted", "no_new_products",
                         "single_page_mode", "site_result_cap")


def finish_run(rows: Sequence[Any], out_prefix: str, fmt: str,
               allow_empty: bool, *, blocked: bool, stop_reason: str,
               pages_requested: int, pages_completed: int,
               start_url: str, final_url: str,
               pages_failed: Optional[List[int]] = None,
               mode: str = "search", source: str = SOURCE_DEFAULT,
               sort: Optional[str] = None,
               total_results: Optional[int] = None,
               pages_available: Optional[int] = None,
               capped_by_site: Optional[bool] = None,
               reachable_max: Optional[int] = None) -> int:
    """Write output + the run-metadata sidecar; return the exit code.

    Shared by all three browser engines so the status/exit-code mapping
    cannot drift between them.
    """
    # Completeness is decided by the reason AND by the evidence. A named
    # list of stop reasons cannot cover a failure recorded somewhere else,
    # and `pages_failed` is somewhere else: a run whose loop ended for a
    # COMPLETE reason while individual pages failed reported exit 0 and
    # `status: complete` with a non-empty `pages_failed` in the same
    # sidecar — a file that contradicts itself, and a pipeline branching
    # on `status` reading a short run as a whole one.
    #
    # Found by a third-party audit of a sibling repo and measured across
    # the family by CALLING each `finish_run` rather than grepping for the
    # fix: 28 of 32 repos behaved this way. Same shape as the exit-code
    # unification this file already carries — a rule keyed on a list of
    # names has a hole for every name nobody added to it.
    complete = stop_reason in COMPLETE_STOP_REASONS and not pages_failed
    row_cls = ROW_CLASS_BY_MODE.get(mode, Campaign)
    rc = save(rows, out_prefix, fmt, allow_empty=allow_empty, row_cls=row_cls)
    wrote_output = bool(rows) or allow_empty

    if wrote_output:
        status = "complete" if (rows and complete) else (
            "partial" if rows else "failed")
        write_run_meta(out_prefix, run_meta(
            status=status, stop_reason=stop_reason,
            pages_requested=pages_requested, pages_completed=pages_completed,
            pages_failed=pages_failed, mode=mode, source=source, sort=sort,
            total_results=total_results, pages_available=pages_available,
            capped_by_site=capped_by_site, reachable_max=reachable_max,
            start_url=start_url, final_url=final_url, products=len(rows)))

    if not rows:
        # Nothing gathered at all, and WHY decides the code. The three
        # outcomes are different facts and a pipeline branches on them
        # (blocked is not empty is not "never reached"):
        #
        #   blocked            something stood between the run and the content
        #   did not complete   we never got the pages — a dead proxy, a load
        #                      timeout, an edge serving something else
        #   completed          we asked, and the answer was nothing
        #
        # Keyed on `not complete` rather than on a list of stop reasons, on
        # purpose: a list cannot cover a reason nobody has added to it yet,
        # so a new one falls silently through to "the catalogue is empty" —
        # which is the defect this branch exists to prevent.
        if blocked:
            return EXIT_BLOCKED
        if not complete:
            print(f"[!] Nothing was gathered and the run did not finish "
                  f"({stop_reason}) — exit {EXIT_FETCH_FAILED}, NOT an empty "
                  f"result (exit {EXIT_NO_PRODUCTS}). Nothing can be "
                  f"concluded about the catalogue from this run.")
            return EXIT_FETCH_FAILED
        return rc
    if not complete:
        print(f"[!] Partial run: stopped after {pages_completed} of "
              f"{pages_requested} page(s) ({stop_reason}). The output holds "
              f"what was gathered, but it is NOT a complete view — see "
              f"{out_prefix}.meta.json.")
        return EXIT_PARTIAL
    return rc
