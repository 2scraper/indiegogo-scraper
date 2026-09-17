#!/usr/bin/env sh
# Check the surfaces this repo PUBLISHES, not just the files it contains.
#
# smoke_test.py's banned-wording check scans repo FILES. A GitHub description
# is not a file, and that gap is not hypothetical: a sibling family shipped
# the exact phrase that check bans into two public repo descriptions, because
# every automated guard was looking at the working tree.
#
# Needs `gh` and network, so it cannot live in the offline suite. Run it
# before a release, and after any `gh repo edit`.
#
#   sh tools/check_repo_metadata.sh [owner/repo]
set -eu

REPO="${1:-2scraper/indiegogo-scraper}"

# Same list as smoke_test.BANNED_PHRASES. Kept in sync by hand, which is
# stated rather than pretended otherwise: there is no way to import a Python
# tuple into a shell script without running Python, and this script has to
# work when the repo is not installed.
BANNED="cloud browser
antidetect browser
2scraper antidetect browser
gate.2prx.com
antidetect_local_api
--antidetect"

meta=$(gh repo view "$REPO" --json description,homepageUrl,repositoryTopics \
        --jq '.description + "\n" + .homepageUrl + "\n" + (.repositoryTopics | map(.name) | join(" "))')
notes=$(gh release list --repo "$REPO" --limit 20 --json tagName --jq '.[].tagName' 2>/dev/null \
        | while read -r tag; do gh release view "$tag" --repo "$REPO" --json body --jq .body 2>/dev/null; done || true)

surface=$(printf '%s\n%s\n' "$meta" "$notes" | tr '[:upper:]' '[:lower:]')

rc=0
echo "$BANNED" | while IFS= read -r phrase; do
  [ -n "$phrase" ] || continue
  if printf '%s' "$surface" | grep -qF -- "$phrase"; then
    echo "FAILED: the published metadata for $REPO contains the banned phrase: $phrase"
    echo "        Write 'Scraping Browser API' instead, and fix it with:"
    echo "          gh repo edit $REPO --description '...'"
    rc=1
  fi
done

# Topics and a homepage are discovery, not decoration: GitHub's own search is
# off until they are set.
topics=$(gh repo view "$REPO" --json repositoryTopics --jq '.repositoryTopics | length')
home=$(gh repo view "$REPO" --json homepageUrl --jq '.homepageUrl')
[ "$topics" -ge 10 ] || { echo "FAILED: only $topics topics set (want 10-15)"; rc=1; }
[ -n "$home" ] || { echo "FAILED: homepage is empty -- the repo's only outbound link"; rc=1; }

[ "$rc" = 0 ] && echo "ok       $REPO: description, homepage, $topics topics and release notes are clean"
exit "$rc"
