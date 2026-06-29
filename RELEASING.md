# Releasing the Dashie HA integration

HACS serves the integration from **GitHub releases (tags)**, not the default
branch — so a release = a `vX.Y.Z` tag whose `custom_components/dashie/manifest.json`
`version` matches.

## Current state: side-tag releases (temporary)

`origin/main` is **behind** local `main` because the §17/Frigate work is committed
to `main` but not yet pushed. Until that lands, releases are cut as tags **off the
latest release tag**, not off `main` — otherwise we'd publish the unfinished work.

Use the helper, which makes that safe:

```bash
tools/release.sh prepare 1.4.16
#  → worktree off the latest tag with manifest bumped.
#    Apply your change in that worktree:
#      - `git cherry-pick <main-commit>`  if the file is unchanged on main, OR
#      - edit the file directly           if it DIVERGED on main (see gotcha 1)
#    then `git commit` in the worktree.
tools/release.sh finish 1.4.16
#  → verifies + tests + tags + pushes + RE-VERIFIES the published tag, then
#    prints the `gh release create` command to run.
```

## Two gotchas that have bitten us

1. **Diverged files can't be cherry-picked.** If a file you're releasing was also
   changed on `main` by the §17/Frigate work (e.g. `camera.py`, `image.py`,
   `__init__.py`), `git cherry-pick` of your fix **conflicts** against the older
   release base. Don't fight the conflict — **re-apply the change directly** to the
   file in the worktree and commit. (`tools/release.sh finish` will catch leftover
   conflict markers.)

2. **Always verify the published tag's content.** v1.4.15 first shipped broken: a
   piped `$?` hid a cherry-pick conflict and the tag ended up pointing at the
   previous release's commit. **Never `... | tail` a `git cherry-pick`/`commit`/`push`**
   (the pipe makes `$?` the *pipe's* exit). `finish` does the verification for you
   (`git show vX:.../manifest.json` must equal the version) — trust it, and if you
   release by hand, run that check yourself.

## After §17/Frigate is pushed (the goal)

Once `origin/main` is current, drop the side-tag dance entirely:

```bash
# bump manifest on main, commit, then:
git tag -a v1.5.0 -m "1.5.0"
git push origin v1.5.0
gh release create v1.5.0 --title "..." --notes "..."
```

`tools/release.sh finish` still works on `main` and is worth running for the
manifest/test/published-tag verification.
