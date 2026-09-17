#!/usr/bin/env python3
"""
diff_runs.py
-------------
Compares two output files from this project (JSON, as written by
output_writer.save) and reports what changed between them, keyed on `sku`.

    python3 diff_runs.py --old projects.2026-09-01.json \\
                          --new projects.2026-09-08.json

Typical use is a scheduled re-run of --mode search under a dated filename,
diffed against the previous one to watch campaigns launch, finish, and move
their funding totals.

Four buckets, each keyed on sku:

  added     — sku present in --new, absent from --old
  removed   — sku present in --old, absent from --new
  changed   — sku present in both, with a different funding total, backer
              count, phase, or one of the other tracked fields
  unmatched — a row this project's parser could not recover a sku for
              (None), counted rather than folded into added/removed

`removed` has a THIRD meaning on this site, beyond "delisted" and "not
fetched this run": indiegogo.com caps every search at 10,000 results / 417
pages of 24 and says so itself (`hasCappedResults`). A campaign that falls
outside this run's slice of a capped result set reads as removed while being
perfectly alive. The sidecar's `capped_by_site` is what tells the two apart,
and the guard below surfaces it rather than leaving a reader to guess.

A `price_source` difference is reported as `source_changed`, never as
`changed`, and `--fail-on-change` ignores it: the API publishes an exact
float (12576.05) where the rendered card publishes a rounded one (12,576),
so a run that read the API and a run that fell back to the DOM would
otherwise report every row as having moved. That says something about our
own two snapshots, not about the site.

Two runs taken under different SORTS are refused outright. The site serves
at most 24 rows a page and caps the result set, so the sort decides WHICH
campaigns are in the file — two sorts are different samples of the same
query, and every line of that diff would be an artefact of the sampling.
"""

import argparse
import json
import re
import sys
from typing import Dict, List, Tuple

from output_writer import UNIQUE_BY_SKU_MODES

TRACKED_FIELDS = (
    # Family prefix, both schemas.
    "price", "currency",
    # Campaign-mode: the figures that actually move between two runs of a
    # live crowdfunding listing. `phase_label` catches a campaign crossing
    # from "Crowdfunding" to "Crowdfunding - Funded" even when its total did
    # not move in the window.
    "backers_count", "followers_count", "goal", "pct_funded",
    "phase_label", "phase", "outcome_code", "campaign_end", "platform",
    # Reward-mode: harmless on a campaign diff, where both sides read None
    # via `.get()`, so adding fields belonging to the OTHER schema never
    # manufactures a spurious "changed" entry.
    "original_price", "lowest_price_30d", "discount_pct",
    "remaining_stock", "purchased_count", "estimated_delivery",
)


def _load(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _by_sku(rows: List[dict]) -> Tuple[Dict[str, dict], int]:
    indexed = {}
    unmatchable = 0
    for r in rows:
        sku = r.get("sku")
        if sku is None:
            unmatchable += 1
            continue
        if sku in indexed:
            unmatchable += 1
            continue
        indexed[sku] = r
    return indexed, unmatchable


def diff_rows(old: List[dict], new: List[dict]) -> dict:
    old_by_sku, old_unmatchable = _by_sku(old)
    new_by_sku, new_unmatchable = _by_sku(new)

    added = [new_by_sku[sku] for sku in new_by_sku.keys() - old_by_sku.keys()]
    removed = [old_by_sku[sku] for sku in old_by_sku.keys() - new_by_sku.keys()]

    changed = []
    for sku in old_by_sku.keys() & new_by_sku.keys():
        before, after = old_by_sku[sku], new_by_sku[sku]
        field_changes = {
            field: {"old": before.get(field), "new": after.get(field)}
            for field in TRACKED_FIELDS
            if before.get(field) != after.get(field)
        }
        if field_changes:
            changed.append({"sku": sku, "title": after.get("title"),
                            "changes": field_changes})

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unmatchable_old": old_unmatchable,
        "unmatchable_new": new_unmatchable,
    }


def _print_summary(result: dict) -> None:
    print(f"[+] {len(result['added'])} added, {len(result['removed'])} removed, "
          f"{len(result['changed'])} changed.")
    for r in result["added"]:
        print(f"  + {r.get('sku')}  {r.get('title')}  {r.get('price')} {r.get('currency')}")
    for r in result["removed"]:
        print(f"  - {r.get('sku')}  {r.get('title')}  {r.get('price')} {r.get('currency')}")
    for c in result["changed"]:
        deltas = ", ".join(f"{f}: {v['old']!r} -> {v['new']!r}" for f, v in c["changes"].items())
        print(f"  ~ {c['sku']}  {c['title']}  {deltas}")
    unmatchable = result["unmatchable_old"] + result["unmatchable_new"]
    if unmatchable:
        print(f"[!] {unmatchable} row(s) across both files had no sku or a "
              f"duplicate sku, and could not be matched across runs.")


def _run_status(path: str):
    meta_path = re.sub(r"\.json$", "", path) + ".meta.json"
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None, None
    return meta.get("status"), meta


def _check_comparable(args) -> bool:
    """Refuse a diff between runs that are not comparable.

    Three ways they can fail to be: not both complete, different modes, or
    different SORTS. The last is this site's own addition -- see the module
    docstring.
    """
    problems = []
    modes = {}
    sorts = {}
    for label, path in (("--old", args.old), ("--new", args.new)):
        status, meta = _run_status(path)
        if status is None:
            continue
        mode = (meta or {}).get("mode")
        if mode:
            modes[label] = mode
        run_sort = (meta or {}).get("sort")
        if run_sort:
            sorts[label] = run_sort
        if (meta or {}).get("capped_by_site"):
            print(f"[i] {label} ({path}) was CAPPED by the site: it holds at "
                  f"most {meta.get('reachable_max')} of "
                  f"{meta.get('total_results')} matches. A campaign in "
                  f"`removed` may simply be outside this run's slice rather "
                  f"than delisted.")
        if mode and mode not in UNIQUE_BY_SKU_MODES:
            problems.append(
                f"{label} ({path}) is a {mode!r} run, which this tool does "
                f"not know how to diff.")
        if status != "complete":
            problems.append(
                f"{label} ({path}) was a {status!r} run — stopped after "
                f"{meta.get('pages_completed')} of {meta.get('pages_requested')} "
                f"page(s), reason {meta.get('stop_reason')!r}")
    if len(set(modes.values())) > 1:
        problems.append(
            f"the two runs are different modes ({modes}). A campaign row and "
            f"a reward row carry different fields, so added/removed would "
            f"describe the mode change rather than the data.")
    if len(set(sorts.values())) > 1:
        problems.append(
            f"the two runs used different sorts ({sorts}). The site caps "
            f"every query and serves 24 rows a page, so the sort decides "
            f"WHICH campaigns are in the file -- these are two different "
            f"samples, and every line of the diff would be an artefact of "
            f"that rather than a change on the site.")
    if not problems:
        return True

    print("[!] Refusing to diff these two runs:")
    for line in problems:
        print(f"      {line}")
    print("    Re-run the incomplete side, or pass --force to compare "
          "anyway (added/removed will include rows that were simply never "
          "fetched).")
    return False


def parse_args():
    p = argparse.ArgumentParser(
        description="Diff two indiegogo-scraper JSON outputs by sku.")
    p.add_argument("--old", required=True, help="Earlier run's JSON output.")
    p.add_argument("--new", required=True, help="Later run's JSON output.")
    p.add_argument("--out", default=None,
                   help="Write the full diff as JSON to this path too.")
    p.add_argument("--fail-on-change", action="store_true",
                   help="Exit 1 if anything was added, removed or changed — "
                        "for a cron job that should only notify on a real diff.")
    p.add_argument("--force", action="store_true",
                   help="Diff even when a run's .meta.json says it was "
                        "partial or failed, or the modes differ.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.force and not _check_comparable(args):
        return 2

    try:
        old = _load(args.old)
        new = _load(args.new)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[!] Could not read one of the input files: {e}")
        return 2

    result = diff_rows(old, new)
    _print_summary(result)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[+] Full diff written to {args.out}")

    if args.fail_on_change and (result["added"] or result["removed"] or result["changed"]):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
