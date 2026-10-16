"""Media API for Dashie - serves photos from HA's media folder.

Supports configurable media base path via:
1. DASHIE_MEDIA_PATH environment variable
2. media_base_path in integration options
3. Default: hass.config.path("media") = /config/media

Note: EXIF metadata extraction (date, GPS, location) is handled by the
Android Kotlin layer (PhotoMetadataExtractor) when photos are downloaded,
not server-side. This API returns file metadata only.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_MEDIA_BASE_PATH, DEFAULT_MEDIA_BASE_PATH

_LOGGER = logging.getLogger(__name__)

# Supported image formats
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}

# A symlink skip stops a loop; this bounds a pathologically deep real tree too.
MAX_SCAN_DEPTH = 12

# How many directory entries the select's "does this folder hold a photo?" probe may
# examine per top-level folder before giving up and answering no.
#
# 🔑 MEASURED on John's real /media shape (55,953 files / 1,739 folders), reproduced
# synthetically 2026-10-16. An EXISTS check short-circuits only when it FINDS
# something; proving a folder has NO image costs the whole subtree. The split was:
#     immich   (132 .JPG, hash-sharded 5 deep)   has_image=True    221 entries
#     frigate  (55,800 .mp4, no snapshots)       has_image=False  59,330 entries
# ⇒ 99.6% of the scan was one folder answering "no". First image in immich appeared
# after 240 entries, so the two cases are three orders of magnitude apart and a cap
# between them is safe.
#
# ⚠️ What the cap TRADES: a folder whose first image lies beyond the budget is
# reported as having none, so it drops out of the dropdown. That is why exhausting
# the budget logs a DROP marker — the exclusion is diagnosable, not silent.
# 🔑 And the property that matters most: the cost stops tracking recording volume.
# Without it the scan grows forever with camera retention; with it, it cannot.
MAX_EXISTENCE_PROBE_ENTRIES = 5000



def _get_media_base_path(hass: HomeAssistant) -> Path:
    """Get the media base directory path.

    In Home Assistant OS/Supervisor, the Media Browser sidebar uses /media
    which is separate from /config/media.

    Priority:
    1. DASHIE_MEDIA_PATH environment variable
    2. media_base_path from integration domain data
    3. /media (HA Media Browser location - the sidebar "Media" option)
    4. Default: hass.config.path("media") = /config/media
    """
    # Check environment variable first
    env_path = os.environ.get("DASHIE_MEDIA_PATH")
    if env_path:
        _LOGGER.info("Using DASHIE_MEDIA_PATH: %s", env_path)
        return Path(env_path)

    # Check integration domain data for configured path
    domain_data = hass.data.get(DOMAIN, {})
    configured_path = domain_data.get(CONF_MEDIA_BASE_PATH, DEFAULT_MEDIA_BASE_PATH)
    if configured_path:
        _LOGGER.info("Using configured media_base_path: %s", configured_path)
        return Path(configured_path)

    # Check for /media mount (HA Media Browser - sidebar "Media" option)
    # This is separate from /config/media and is where media shares appear
    external_media = Path("/media")
    config_media = Path(hass.config.path("media"))

    _LOGGER.info("Checking media paths: /media exists=%s, /config/media exists=%s",
                 external_media.exists(), config_media.exists())

    # Prefer /media (Media Browser) if it exists and has content
    if external_media.exists() and external_media.is_dir():
        # Check if it has any subdirectories (actual media folders)
        try:
            subdirs = [d for d in external_media.iterdir() if d.is_dir() and not d.name.startswith('.')]
            if subdirs:
                _LOGGER.info("Using /media (Media Browser) with %d folders: %s",
                            len(subdirs), [d.name for d in subdirs[:5]])
                return external_media
        except PermissionError:
            _LOGGER.warning("Permission denied accessing /media")

    # Fall back to /config/media
    _LOGGER.info("Using /config/media (default)")
    return config_media


class DashieMediaListView(HomeAssistantView):
    """View to list photos from HA's media folder."""

    url = "/api/dashie/media"
    name = "api:dashie:media"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for photo list from media folder.

        Query params:
        - folder: Subfolder within /config/media (default: "dashie-photos")
        - limit: Max photos to return (default: 100)
        - offset: Pagination offset (default: 0)
        - random: Shuffle results (default: false)
        """
        hass: HomeAssistant = request.app["hass"]

        # Get folder from query params (default to "." for root media folder)
        folder = request.query.get("folder", ".")
        limit = int(request.query.get("limit", 100))
        offset = int(request.query.get("offset", 0))
        random_order = request.query.get("random", "false").lower() == "true"

        # Build path to media folder ("." means root, "*" means all folders)
        media_base = await hass.async_add_executor_job(_get_media_base_path, hass)

        if folder == "*":
            # Scan ALL folders recursively
            if not media_base.exists():
                return web.json_response({
                    "photos": [],
                    "total": 0,
                    "folder": folder,
                    "message": "Media directory not found."
                })
            photos = await hass.async_add_executor_job(
                _scan_all_folders, media_base
            )
        elif folder == ".":
            media_dir = media_base
            if not media_dir.exists():
                return web.json_response({
                    "photos": [],
                    "total": 0,
                    "folder": folder,
                    "message": f"Folder '{folder}' not found in /config/media. Create it via Media > Local Media > Manage."
                })
            photos = await hass.async_add_executor_job(
                _scan_media_folder, media_dir, folder
            )
        else:
            media_dir = media_base / folder
            if not media_dir.exists():
                return web.json_response({
                    "photos": [],
                    "total": 0,
                    "folder": folder,
                    "message": f"Folder '{folder}' not found in /config/media. Create it via Media > Local Media > Manage."
                })
            photos = await hass.async_add_executor_job(
                _scan_media_folder, media_dir, folder
            )

        total = len(photos)

        # Sort or shuffle
        if random_order:
            import random
            random.shuffle(photos)
        else:
            # Sort by modification time (newest first)
            photos.sort(key=lambda p: p.get("modified", 0), reverse=True)

        # Apply pagination
        photos = photos[offset:offset + limit]

        return web.json_response({
            "photos": photos,
            "total": total,
            "limit": limit,
            "offset": offset,
            "folder": folder,
        })


class DashieMediaImageView(HomeAssistantView):
    """View to serve a photo from HA's media folder."""

    url = "/api/dashie/media/image/{folder}/{filename:.*}"
    name = "api:dashie:media:image"
    requires_auth = True

    async def get(self, request: web.Request, folder: str, filename: str) -> web.Response:
        """Serve an image file from the media folder."""
        hass: HomeAssistant = request.app["hass"]

        # Build path and validate ("." means root media folder)
        media_dir = await hass.async_add_executor_job(_get_media_base_path, hass)
        if folder == ".":
            file_path = media_dir / filename
        else:
            file_path = media_dir / folder / filename

        # Security: ensure path is within media directory
        try:
            file_path = file_path.resolve()
            media_dir = media_dir.resolve()
            if not str(file_path).startswith(str(media_dir)):
                return web.json_response({"error": "Invalid path"}, status=403)
        except (ValueError, RuntimeError):
            return web.json_response({"error": "Invalid path"}, status=400)

        if not file_path.exists() or not file_path.is_file():
            return web.json_response({"error": "File not found"}, status=404)

        # Determine content type
        content_type = _get_content_type(file_path)

        return web.FileResponse(file_path, headers={
            "Content-Type": content_type,
            "Cache-Control": "public, max-age=86400",
        })


class DashieMediaFoldersView(HomeAssistantView):
    """View to list available folders in HA's media directory."""

    url = "/api/dashie/media/folders"
    name = "api:dashie:media:folders"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """List folders in the media directory that contain images."""
        hass: HomeAssistant = request.app["hass"]

        media_dir = await hass.async_add_executor_job(_get_media_base_path, hass)

        if not media_dir.exists():
            return web.json_response({
                "folders": [],
                "message": "Media directory not found. Enable media browser in configuration.yaml."
            })

        folders = await hass.async_add_executor_job(_list_media_folders, media_dir)

        return web.json_response({"folders": folders})


def _scan_media_folder(media_dir: Path, folder_name: str) -> list[dict]:
    """Scan a media folder for image files (runs in executor)."""
    photos = []

    def scan_recursive(directory: Path, prefix: str = ""):
        """Recursively scan directory for images."""
        try:
            for item in directory.iterdir():
                if item.is_file():
                    ext = item.suffix.lower()
                    if ext in SUPPORTED_EXTENSIONS:
                        relative_path = f"{prefix}{item.name}" if not prefix else f"{prefix}/{item.name}"
                        photos.append({
                            "filename": item.name,
                            "path": relative_path,
                            "url": f"/api/dashie/media/image/{folder_name}/{relative_path}",
                            "size": item.stat().st_size,
                            "modified": item.stat().st_mtime,
                        })
                elif item.is_dir() and not item.name.startswith("."):
                    new_prefix = f"{prefix}/{item.name}" if prefix else item.name
                    scan_recursive(item, new_prefix)
        except PermissionError:
            _LOGGER.warning("Permission denied scanning %s", directory)

    scan_recursive(media_dir)
    return photos


def _scan_all_folders(media_base: Path) -> list[dict]:
    """Scan ALL folders in media directory for images (runs in executor).

    Returns photos from all folders, with URLs that include the folder path.
    """
    photos = []

    def scan_folder_recursive(directory: Path, folder_name: str, prefix: str = ""):
        """Recursively scan a folder for images."""
        try:
            for item in directory.iterdir():
                if item.is_file():
                    ext = item.suffix.lower()
                    if ext in SUPPORTED_EXTENSIONS:
                        relative_path = f"{prefix}{item.name}" if not prefix else f"{prefix}/{item.name}"
                        photos.append({
                            "filename": item.name,
                            "path": f"{folder_name}/{relative_path}" if folder_name != "." else relative_path,
                            "url": f"/api/dashie/media/image/{folder_name}/{relative_path}",
                            "size": item.stat().st_size,
                            "modified": item.stat().st_mtime,
                        })
                elif item.is_dir() and not item.name.startswith("."):
                    new_prefix = f"{prefix}/{item.name}" if prefix else item.name
                    scan_folder_recursive(item, folder_name, new_prefix)
        except PermissionError:
            _LOGGER.warning("Permission denied scanning %s", directory)

    # Scan root folder
    for item in media_base.iterdir():
        if item.is_file():
            ext = item.suffix.lower()
            if ext in SUPPORTED_EXTENSIONS:
                photos.append({
                    "filename": item.name,
                    "path": item.name,
                    "url": f"/api/dashie/media/image/./{item.name}",
                    "size": item.stat().st_size,
                    "modified": item.stat().st_mtime,
                })
        elif item.is_dir() and not item.name.startswith("."):
            scan_folder_recursive(item, item.name)

    return photos


def _list_media_folders(media_dir: Path) -> list[dict]:
    """List folders in media directory with image counts (runs in executor)."""
    folders = []
    total_count = 0

    # Check root media folder for images
    root_count = sum(1 for f in media_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS)
    if root_count > 0:
        folders.append({
            "name": ".",
            "path": ".",
            "photo_count": root_count,
        })
        total_count += root_count

    # Check subdirectories
    for item in media_dir.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            count = _count_images_recursive(item)
            if count > 0:
                folders.append({
                    "name": item.name,
                    "path": item.name,
                    "photo_count": count,
                })
                total_count += count

    # Add "All" option at the beginning if there are multiple folders with photos
    if len(folders) > 1 or (len(folders) == 1 and folders[0]["path"] != "."):
        folders.insert(0, {
            "name": "*",
            "path": "*",
            "photo_count": total_count,
        })

    return folders


def _count_images_recursive(directory: Path, _depth: int = 0) -> int:
    """Count images in a directory recursively. EXACT — serves the HTTP view only.

    ``photo_count`` from this function goes over the wire and is DISPLAYED to the
    user: ``HaMediaPhotoSource.kt`` reads it and ``SidebarScreensaverDialogs.kt``
    renders "<n> photos" in the app's screensaver picker, defaulting to 0 when the
    field is absent. So it fails SOFT — weakening it shows "0 photos" everywhere and
    raises nothing. It is a cross-boundary wire contract; do not make it approximate
    without a JS_KOTLIN_CONTRACTS row.

    ``is_dir()`` follows symlinks, so a link to an ancestor recurses forever. The
    HTTP view has no try/except around this call, so that escapes as a 500. The
    symlink skip and the depth cap are both load-bearing.
    """
    if _depth > MAX_SCAN_DEPTH:
        _LOGGER.warning("DROP: media scan depth cap hit at %s", directory)
        return 0
    count = 0
    try:
        for item in directory.iterdir():
            if item.is_symlink():
                continue
            if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS:
                count += 1
            elif item.is_dir() and not item.name.startswith("."):
                count += _count_images_recursive(item, _depth + 1)
    except PermissionError:
        pass
    return count


def _has_any_image(directory: Path, budget: int | None = None) -> bool:
    """Does this tree contain at least one image? Stops at the first one, or at a budget.

    This exists because the SELECT entity never used the count: its only use of
    ``_count_images_recursive``'s return value was ``if count > 0``, evaluated after
    the whole tree had already been walked. A SUM has no early exit — it must visit
    every term — so producing a dropdown's folder list cost one ``stat()`` per file
    in the entire tree, which is what outran HA's bootstrap timeout (D-279).

    Iterative and ``os.scandir``-based on purpose: scandir carries the entry type
    from the directory read, so ``is_file()``/``is_dir()`` usually need no extra
    stat, and an explicit queue makes the budget exact rather than per-branch.

    Returns False when the budget runs out, which is the honest answer to "did we
    find one" but NOT to "is there one" — see MAX_EXISTENCE_PROBE_ENTRIES.
    """
    if budget is None:
        budget = MAX_EXISTENCE_PROBE_ENTRIES
    examined = 0
    queue: list[tuple[Path, int]] = [(directory, 0)]
    while queue:
        current, depth = queue.pop(0)
        if depth > MAX_SCAN_DEPTH:
            continue
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    examined += 1
                    if examined > budget:
                        _LOGGER.warning(
                            "DROP: media probe gave up on %s after %d entries; it will "
                            "be treated as having no images",
                            directory, budget,
                        )
                        return False
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_file(follow_symlinks=False):
                            if Path(entry.name).suffix.lower() in SUPPORTED_EXTENSIONS:
                                return True
                        elif entry.is_dir(follow_symlinks=False):
                            queue.append((Path(entry.path), depth + 1))
                    except OSError:
                        continue
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            continue
    return False


def _list_media_folder_names(media_dir: Path) -> list[dict]:
    """Folders that contain at least one image, WITHOUT counting them.

    Same ``name``/``path`` shape the select reads; deliberately no ``photo_count``,
    because the select never displays one and computing it is the D-279 defect. The
    HTTP view keeps ``_list_media_folders`` and its exact counts.
    """
    folders: list[dict] = []
    try:
        has_root = any(
            f.is_file() and not f.is_symlink() and f.suffix.lower() in SUPPORTED_EXTENSIONS
            for f in media_dir.iterdir()
        )
    except PermissionError:
        has_root = False
    if has_root:
        folders.append({"name": ".", "path": "."})

    try:
        for item in sorted(media_dir.iterdir()):
            if item.is_symlink() or not item.is_dir() or item.name.startswith("."):
                continue
            if _has_any_image(item):
                folders.append({"name": item.name, "path": item.name})
    except PermissionError:
        pass

    if len(folders) > 1 or (len(folders) == 1 and folders[0]["path"] != "."):
        folders.insert(0, {"name": "*", "path": "*"})
    return folders


def _get_content_type(path: Path) -> str:
    """Get content type for a file."""
    ext = path.suffix.lower()
    content_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".heic": "image/heic",
    }
    return content_types.get(ext, "application/octet-stream")


def register_media_api_views(hass: HomeAssistant) -> None:
    """Register Media API views."""
    hass.http.register_view(DashieMediaListView())
    hass.http.register_view(DashieMediaImageView())
    hass.http.register_view(DashieMediaFoldersView())

    _LOGGER.info("Registered Dashie Media API views")
