# Releasing the Dashie HA integration

HACS serves the integration from **GitHub Releases**, not from tags and not from the
default branch. A pushed `main` reaches nobody. **A tag reaches nobody.** Only a
published Release does.

> 🔴 **Proof, and the reason this file was rewritten (2026-09-17):** `v1.5.0` has been
> tagged since August and the newest *Release* is `1.4.15` from 2026-06-29. Eleven weeks
> of work sat on `main` and in a tag, and not one user ever saw it.

A release therefore = **a `vX.Y.Z` tag whose `custom_components/dashie/manifest.json`
version matches, plus a published GitHub Release pointing at it.**

---

## ⚠️ `tools/release.sh` is superseded. Do not use it.

It was written when `origin/main` was **behind** local `main`, so it cuts releases as a
side-tag off the *latest release tag* and asks you to cherry-pick onto it. **That premise
is dead** — `main` is now ahead and it is the tested tree. Using `prepare` today would
publish a tree nobody has run.

Its other half is broken independently of that: an earlier version of this file claimed
`finish` "still works on `main`". It does not. `finish()` exits at `tools/release.sh:61`
without the worktree `prepare` creates, and with one, it `cd`s into that worktree and tags
the **release branch** instead of `main`.

**The one part worth keeping is step 5 below** — re-reading the *published* tag's manifest.
Its absence is what shipped `v1.4.15` broken.

## The release

```bash
# 1. Pre-flight — all three must be clean before anything is pushed.
git status --short                    # nothing uncommitted
git log --oneline origin/main..main   # exactly what you intend to publish
.venv/bin/python -m pytest -q         # green

# 2. Bump the manifest and commit it BEFORE tagging.
#    A tag whose manifest disagrees with the version is the failure mode step 5 catches.
sed -i '' 's/"version": "[0-9.]*"/"version": "X.Y.Z"/' custom_components/dashie/manifest.json
grep '"version"' custom_components/dashie/manifest.json
git commit -o custom_components/dashie/manifest.json -m "release: X.Y.Z"

# 3. Push main.
git push origin main

# 4. Tag and push the tag. No pipes — see the gotcha below.
git tag -a vX.Y.Z -m "X.Y.Z"
git push origin vX.Y.Z

# 5. 🔴 Re-read the PUBLISHED tag. This is the check that was missing when v1.4.15 broke.
git fetch origin --tags
git show vX.Y.Z:custom_components/dashie/manifest.json | grep '"version"'
#    Must equal X.Y.Z. If it does not, stop and undo:
#      gh release delete vX.Y.Z --yes --cleanup-tag ; git tag -d vX.Y.Z

# 6. Publish — THIS is the step that reaches users.
gh release create vX.Y.Z --title "X.Y.Z — <summary>" --notes-file <notes>
gh release list --limit 3             # yours must show as "Latest"
```

**Then verify a user actually gets it**, because a green `gh release create` does not show
that: on a real box, HACS → Integrations → Dashie must offer the new version (HACS caches
GitHub — use ⋮ → *Reload data* if it sits on the old one), and after updating, Settings →
Devices & Services → Dashie must load with its devices not `unavailable`.

## Three gotchas that have actually bitten

1. **Never pipe a `git`/`gradle`/`pytest` command whose exit code you are about to read.**
   A piped `$?` is the *pipe's* status. This hid a cherry-pick conflict and `v1.4.15`
   shipped pointing at the previous release's commit. (`PIPESTATUS` is a bash feature and
   does not work in zsh, which is the shell here — it silently reports the last stage.)

2. **`hacs.json`'s `homeassistant` key decides who is even offered the update.** It is
   currently `2025.1.0`. Raising it silently excludes installs below it; lowering it offers
   the update to versions the suite has never run on. `tests/test_min_ha_version.py` holds
   the derived code floor and the declared floor separately and fails if they drift — read
   it before changing either number.

3. **A release cannot be recalled.** Deleting a Release does not retract it from anyone who
   already updated; the only remedy that reaches them is publishing a fixed version. Decide
   before publishing, not after.
