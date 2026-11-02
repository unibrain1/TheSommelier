#!/usr/bin/env bash
# Fails while the release notes for the checked-out milestone still hold a
# placeholder. The deploy steps (for example a Home Assistant change) are
# manual, so a release must not ship before someone writes them down.
#
# .claude/project.md points milestone_checks (/flow:finish-milestone) and
# release_checks (/flow:release-milestone) at this script. The flow commands
# pass no version, but both run on milestone/<version>, so the branch name
# gives the version.
#
# Exit codes:
#   0 = no placeholder
#   1 = placeholder found (stderr names each section)
#   2 = cannot check: not on a milestone/v* branch, or no release notes file
set -euo pipefail

PLACEHOLDER='_To be completed as issues land._'

TOPLEVEL="$(git rev-parse --show-toplevel)" || {
  echo "Not inside a git work tree. Cannot check the release notes." >&2
  exit 2
}
cd "$TOPLEVEL"

BRANCH="$(git branch --show-current)"
VERSION="${BRANCH#milestone/}"
if [[ "$VERSION" == "$BRANCH" || "${VERSION#v}" == "$VERSION" ]]; then
  echo "Branch '${BRANCH}' is not milestone/v<version>. Cannot find the release notes." >&2
  exit 2
fi

# Read the path from .claude/project.md so that the path has one source.
# shellcheck disable=SC2016 # The backticks are literal Markdown, not a command.
PATTERN="$(sed -n 's/^- release_notes: `\(.*\)`$/\1/p' .claude/project.md)"
if [[ -z "$PATTERN" ]]; then
  echo "No release_notes key in .claude/project.md." >&2
  exit 2
fi
NOTES="${PATTERN//\{version\}/$VERSION}"
if [[ ! -f "$NOTES" ]]; then
  echo "No release notes at ${NOTES}." >&2
  exit 2
fi

SECTIONS="$(awk -v p="$PLACEHOLDER" '
  /^## / { section = substr($0, 4) }
  index($0, p) { print "  - " (section == "" ? "(no section)" : section) }
' "$NOTES" | sort -u)"

if [[ -n "$SECTIONS" ]]; then
  echo "${NOTES} still has the placeholder '${PLACEHOLDER}' in:" >&2
  echo "$SECTIONS" >&2
  echo "Write the real steps in each section, then run the command again." >&2
  exit 1
fi

echo "${NOTES}: no placeholder."
