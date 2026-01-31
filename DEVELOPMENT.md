# Development Workflow

This document explains how to work with the Dashie Home Assistant integration and the camera card feature branch.

## Branch Strategy

### Branches

- **`main`** - Stable integration releases
- **`dev`** - Integration development (active work)
- **`feature/camera-card`** - Camera card development (this branch)

### Workflow

```
main (stable)
  ↓
dev (integration development)
  ↓
feature/camera-card (camera card development)
```

## Working on the Integration (Without Camera Card)

If you're making changes to the integration itself (not the camera card):

```bash
# Switch to dev branch
git checkout dev

# Make your changes to custom_components/dashie/
# ...

# Commit and push
git add custom_components/
git commit -m "Your integration changes"
git push origin dev

# When ready, merge to main
git checkout main
git merge dev
git push origin main
```

**Note:** The `www/dashie-camera-card/` directory only exists in `feature/camera-card` branch.

## Working on the Camera Card

If you're developing the camera card:

```bash
# Switch to camera card branch
git checkout feature/camera-card

# Build the card
cd www/dashie-camera-card
npm install
npm run build

# Test the card
# (Deploy dist/dashie-camera-card.js to Home Assistant)

# Commit camera card changes
git add www/
git commit -m "Camera card: your changes"
git push origin feature/camera-card
```

## Deploying Integration Updates (While Camera Card is in Development)

### Scenario: You need to push an integration update to users

**Problem:** You've made changes on `dev` but don't want to include the unfinished camera card.

**Solution:** Merge `dev` to `main` directly (camera card only exists in feature branch):

```bash
# On dev branch
git checkout dev
git add custom_components/
git commit -m "Fix: your integration update"
git push origin dev

# Merge to main (camera card won't be included)
git checkout main
git merge dev
git push origin main

# Users get the integration update
# Camera card stays in feature/camera-card branch
```

### Scenario: Camera card is ready to ship

**Solution:** Merge `feature/camera-card` to `dev`, then to `main`:

```bash
# Step 1: Merge camera card to dev
git checkout dev
git merge feature/camera-card

# Step 2: Test integration + camera card together
# ...

# Step 3: Merge to main
git checkout main
git merge dev
git push origin main

# Now users get both integration + camera card
```

## Directory Structure

### On `main` and `dev` branches:
```
dashie-ha-integration/
├── custom_components/
│   └── dashie/
│       ├── __init__.py
│       ├── manifest.json
│       └── ...
├── docs/
├── README.md
└── hacs.json
```

### On `feature/camera-card` branch:
```
dashie-ha-integration/
├── custom_components/
│   └── dashie/
│       ├── __init__.py
│       ├── manifest.json
│       └── ...
├── www/                    # ONLY on this branch
│   └── dashie-camera-card/
│       ├── src/
│       ├── dist/
│       └── package.json
├── docs/
├── README.md
└── hacs.json
```

## Camera Card Development

### Build

```bash
cd www/dashie-camera-card
npm run build
```

Output: `www/dashie-camera-card/dist/dashie-camera-card.js`

### Deploy to Home Assistant

```bash
# Copy to HA server
scp www/dashie-camera-card/dist/dashie-camera-card.js homeassistant@192.168.86.46:/config/www/

# Or use the integration's bundling (when ready)
```

### Watch Mode (Auto-rebuild)

```bash
cd www/dashie-camera-card
npm run watch
```

## Integration Bundling (Future)

When the camera card is ready to ship with the integration, update `custom_components/dashie/__init__.py`:

```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dashie from a config entry."""

    # Copy camera card from integration bundle to www/
    if entry.options.get("install_camera_card", True):
        await install_camera_card(hass)

    # ... rest of setup
```

See `.reference/build-plans/integration-optional-card-install.py` for implementation.

## Git Tips

### Check which branch you're on

```bash
git branch --show-current
```

### See differences between branches

```bash
# What's in feature/camera-card that's not in dev?
git diff dev..feature/camera-card

# Just show file names
git diff --name-only dev..feature/camera-card
```

### Safely test merging before doing it

```bash
git merge --no-commit --no-ff feature/camera-card
# Review changes
git merge --abort  # Cancel if not ready
```

## Common Workflows

### Update Integration, Don't Touch Camera Card

```bash
git checkout dev
# Edit custom_components/dashie/something.py
git commit -am "Update integration"
git push origin dev
git checkout main
git merge dev
git push origin main
```

### Update Camera Card, Don't Touch Integration

```bash
git checkout feature/camera-card
cd www/dashie-camera-card
# Edit src/dashie-camera-card.ts
npm run build
git commit -am "Update camera card"
git push origin feature/camera-card
```

### Deploy Just Integration to Users

```bash
git checkout main
# main branch has no www/ directory
# HACS/users only get custom_components/
```

### Deploy Integration + Camera Card to Users

```bash
# First merge camera card to dev
git checkout dev
git merge feature/camera-card

# Then merge dev to main
git checkout main
git merge dev
git push origin main

# Now HACS users get both
```

## Benefits of This Strategy

✅ **Independent development** - Work on integration without camera card getting in the way
✅ **Selective deployment** - Deploy integration updates without unfinished camera card
✅ **Clean main branch** - Main stays clean until camera card is ready
✅ **Easy testing** - Test camera card in isolation on feature branch
✅ **Safe merging** - Merge when ready, not before

## Questions?

- **"I'm on dev and don't see www/ directory"** - That's correct! It only exists on `feature/camera-card` branch.
- **"How do I deploy integration changes without camera card?"** - Work on `dev` or `main` branches. Camera card isn't there.
- **"When should I merge camera card to main?"** - When it's tested and ready for users. Until then, keep it in feature branch.
