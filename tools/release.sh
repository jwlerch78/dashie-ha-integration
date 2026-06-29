#!/usr/bin/env bash
#
# Safe release helper for the Dashie HA integration.
#
# WHY: while origin/main is behind local main (unpushed §17/Frigate WIP), releases
# are cut as tags off the LATEST RELEASE TAG, not off main. That side-tag dance is
# error-prone — v1.4.15 first shipped broken because a cherry-pick silently
# conflicted (a file had diverged on main) and the tag was never verified. This
# script removes both failure modes: it never masks errors (set -euo pipefail) and
# the `finish` step REFUSES to publish unless the pushed tag actually contains the
# version.
#
# Usage:
#   tools/release.sh prepare 1.4.16
#     → makes a worktree off the latest tag with manifest bumped to 1.4.16.
#       Apply your change there (cherry-pick a main commit, OR — if the file
#       diverged on main — edit it directly), then `git commit` in that worktree.
#   tools/release.sh finish 1.4.16
#     → verifies manifest + no conflict markers, runs tests, tags, pushes, and
#       RE-VERIFIES the published tag before declaring success. Then prints the
#       exact `gh release create` line.
#
# Once §17/Frigate is pushed and origin/main is current, releases are just
# `git tag vX && git push origin vX` on main — but `finish` still works and is
# worth running for the verification.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="custom_components/dashie/manifest.json"
VENV="$REPO/.venv/bin/python"

cmd="${1:-}"; version="${2:-}"
[ -n "$cmd" ] && [ -n "$version" ] || { echo "usage: release.sh {prepare|finish} <version>"; exit 1; }
tag="v$version"
wt="/tmp/dashie-release-$version"
branch="release-$version"

manifest_version() { grep -o '"version": "[^"]*"' "$1" | head -1; }

prepare() {
  local base
  git -C "$REPO" fetch origin --tags --quiet
  base="$(git -C "$REPO" tag --sort=-v:refname | grep -E '^v[0-9]' | head -1)"
  [ -n "$base" ] || { echo "❌ no base release tag found"; exit 1; }
  git -C "$REPO" worktree remove "$wt" --force >/dev/null 2>&1 || true
  git -C "$REPO" branch -D "$branch" >/dev/null 2>&1 || true
  rm -rf "$wt"
  git -C "$REPO" worktree add -b "$branch" "$wt" "$base" --quiet
  sed -i '' "s/\"version\": \"[0-9.]*\"/\"version\": \"$version\"/" "$wt/$MANIFEST"
  echo "✅ worktree off $base ready: $wt"
  echo "   manifest bumped → $(manifest_version "$wt/$MANIFEST")"
  echo
  echo "Next: apply your change in that worktree and commit it, e.g."
  echo "   cd $wt"
  echo "   git cherry-pick <main-commit>      # or edit the diverged file directly"
  echo "   git commit -am 'fix: ...'          # if you edited directly"
  echo "   cd - && tools/release.sh finish $version"
}

finish() {
  [ -d "$wt" ] || { echo "❌ no prepared worktree at $wt — run: tools/release.sh prepare $version"; exit 1; }
  cd "$wt"

  # 1. no leftover conflict markers
  if git grep -nE '^(<<<<<<<|=======|>>>>>>>) ' >/dev/null 2>&1; then
    echo "❌ conflict markers present — resolve and commit first"; exit 1
  fi
  # 2. everything committed
  [ -z "$(git status --porcelain)" ] || { echo "❌ uncommitted changes — commit them first:"; git status --short; exit 1; }
  # 3. manifest matches the version we're cutting
  got="$(manifest_version "$MANIFEST")"
  [ "$got" = "\"version\": \"$version\"" ] || { echo "❌ manifest is [$got], expected $version — aborting"; exit 1; }
  # 4. tests must pass
  if [ -x "$VENV" ]; then
    PYTHONPATH=. "$VENV" -m pytest tests/ -q
  else
    echo "⚠️  no venv at $VENV — skipping tests (install it to enable this gate)"
  fi

  # 5. tag + push (no pipes — exit codes are real)
  git tag -a "$tag" -m "$version"
  git push origin "$tag"

  # 6. THE CHECK THAT WAS MISSING: does the PUBLISHED tag actually contain it?
  cd "$REPO"
  git fetch origin --tags --quiet
  pub="$(git show "$tag:$MANIFEST" | grep -o '"version": "[^"]*"' | head -1)"
  if [ "$pub" != "\"version\": \"$version\"" ]; then
    echo "❌ PUBLISHED $tag manifest is [$pub], expected $version — RELEASE IS BROKEN."
    echo "   Delete it:  gh release delete $tag --yes --cleanup-tag; git tag -d $tag"
    exit 1
  fi

  # cleanup worktree
  git worktree remove "$wt" --force >/dev/null 2>&1 || true
  git branch -D "$branch" >/dev/null 2>&1 || true

  echo "✅ $tag pushed and verified (published manifest = $version)"
  echo
  echo "Create the GitHub release:"
  echo "   gh release create $tag --title \"$version — <summary>\" --notes \"<notes>\""
}

case "$cmd" in
  prepare) prepare ;;
  finish)  finish ;;
  *) echo "usage: release.sh {prepare|finish} <version>"; exit 1 ;;
esac
