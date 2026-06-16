# Contributing

Thanks for your interest in the Dashie Home Assistant integration. This document covers local development and how to deploy the integration to a Home Assistant instance for testing.

## Repository layout

- `custom_components/dashie/` — the integration itself (entities, config flow, coordinator, proxies)
- `docs/` — guides and troubleshooting
- `hacs.json` / `manifest.json` — HACS and Home Assistant metadata

## Validation

Two CI workflows must stay green; they also run on every PR:

- **HACS Validation** (`.github/workflows/hacs.yml`)
- **Hassfest Validation** (`.github/workflows/hassfest.yml`)

## Deploying to Home Assistant for testing

The fastest way to test changes on a real HA instance is to copy the integration
files over a Samba share and restart HA.

### Prerequisites

1. **Samba add-on** installed and running in Home Assistant
   - Settings → Add-ons → Add-on Store → search "Samba share" → install
   - Configure username/password and start the add-on
2. Your Home Assistant instance's IP address (referred to below as `<HA_IP>`)

### Connecting via Samba (macOS)

1. Open **Finder**
2. Press **Cmd+K** (Go → Connect to Server)
3. Enter `smb://<HA_IP>/config`
4. Enter your Samba credentials
5. The HA config folder mounts at `/Volumes/config`

### Copying files

```bash
# Copy all integration files to Home Assistant
cd custom_components/dashie
find . -type f -exec cp {} /Volumes/config/custom_components/dashie/ \;
```

Clean install (removes old files first):

```bash
rm -rf /Volumes/config/custom_components/dashie/*
cd custom_components/dashie
find . -type f -exec cp {} /Volumes/config/custom_components/dashie/ \;
```

### After deploying

Restart Home Assistant to load the updated integration:

- Settings → System → Restart, or
- Developer Tools → Actions → `homeassistant.restart`
