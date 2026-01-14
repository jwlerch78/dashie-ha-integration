"""HTTP API for Dashie Photo Hub."""
from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .photo_hub import PhotoHub

_LOGGER = logging.getLogger(__name__)


class DashiePhotoListView(HomeAssistantView):
    """View to list photos."""

    url = "/api/dashie/photos"
    name = "api:dashie:photos"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for photo list."""
        hass: HomeAssistant = request.app["hass"]

        photo_hub: PhotoHub | None = hass.data.get(DOMAIN, {}).get("photo_hub")
        if not photo_hub:
            return web.json_response(
                {"error": "Photo Hub not initialized"},
                status=503,
            )

        # Parse query params
        source_id = request.query.get("source")
        limit = int(request.query.get("limit", 100))
        offset = int(request.query.get("offset", 0))
        random = request.query.get("random", "false").lower() == "true"

        photos = await photo_hub.get_photos(
            source_id=source_id,
            limit=limit,
            offset=offset,
            random=random,
        )

        total = await photo_hub.get_photo_count(source_id=source_id)

        # Build response with URLs
        photo_list = []
        for photo in photos:
            photo_list.append({
                "id": photo["id"],
                "filename": photo["filename"],
                "url": f"/api/dashie/photos/{photo['id']}/image",
                "thumb_url": f"/api/dashie/photos/{photo['id']}/thumbnail",
                "width": photo["width"],
                "height": photo["height"],
                "taken_at": photo["taken_at"],
                "created_at": photo["created_at"],
                "source_id": photo["source_id"],
            })

        return web.json_response({
            "photos": photo_list,
            "total": total,
            "limit": limit,
            "offset": offset,
        })


class DashiePhotoDetailView(HomeAssistantView):
    """View to get single photo metadata."""

    url = "/api/dashie/photos/{photo_id}"
    name = "api:dashie:photo"
    requires_auth = True

    async def get(self, request: web.Request, photo_id: str) -> web.Response:
        """Handle GET request for photo metadata."""
        hass: HomeAssistant = request.app["hass"]

        photo_hub: PhotoHub | None = hass.data.get(DOMAIN, {}).get("photo_hub")
        if not photo_hub:
            return web.json_response(
                {"error": "Photo Hub not initialized"},
                status=503,
            )

        photo = await photo_hub.get_photo(photo_id)
        if not photo:
            return web.json_response(
                {"error": "Photo not found"},
                status=404,
            )

        return web.json_response({
            "id": photo["id"],
            "filename": photo["filename"],
            "url": f"/api/dashie/photos/{photo['id']}/image",
            "thumb_url": f"/api/dashie/photos/{photo['id']}/thumbnail",
            "width": photo["width"],
            "height": photo["height"],
            "taken_at": photo["taken_at"],
            "created_at": photo["created_at"],
            "source_id": photo["source_id"],
            "metadata": photo.get("metadata"),
        })

    async def delete(self, request: web.Request, photo_id: str) -> web.Response:
        """Handle DELETE request for photo."""
        hass: HomeAssistant = request.app["hass"]

        photo_hub: PhotoHub | None = hass.data.get(DOMAIN, {}).get("photo_hub")
        if not photo_hub:
            return web.json_response(
                {"error": "Photo Hub not initialized"},
                status=503,
            )

        success = await photo_hub.delete_photo(photo_id)
        if not success:
            return web.json_response(
                {"error": "Photo not found or could not be deleted"},
                status=404,
            )

        return web.json_response({"success": True})


class DashiePhotoImageView(HomeAssistantView):
    """View to serve photo image."""

    url = "/api/dashie/photos/{photo_id}/image"
    name = "api:dashie:photo:image"
    requires_auth = True

    async def get(self, request: web.Request, photo_id: str) -> web.Response:
        """Handle GET request for photo image."""
        hass: HomeAssistant = request.app["hass"]

        photo_hub: PhotoHub | None = hass.data.get(DOMAIN, {}).get("photo_hub")
        if not photo_hub:
            return web.json_response(
                {"error": "Photo Hub not initialized"},
                status=503,
            )

        photo_path = await photo_hub.get_photo_path(photo_id)
        if not photo_path:
            return web.json_response(
                {"error": "Photo not found"},
                status=404,
            )

        # Determine content type
        content_type = _get_content_type(photo_path)

        # Stream the file
        return web.FileResponse(photo_path, headers={
            "Content-Type": content_type,
            "Cache-Control": "public, max-age=86400",  # Cache for 24 hours
        })


class DashiePhotoThumbnailView(HomeAssistantView):
    """View to serve photo thumbnail."""

    url = "/api/dashie/photos/{photo_id}/thumbnail"
    name = "api:dashie:photo:thumbnail"
    requires_auth = True

    async def get(self, request: web.Request, photo_id: str) -> web.Response:
        """Handle GET request for photo thumbnail."""
        hass: HomeAssistant = request.app["hass"]

        photo_hub: PhotoHub | None = hass.data.get(DOMAIN, {}).get("photo_hub")
        if not photo_hub:
            return web.json_response(
                {"error": "Photo Hub not initialized"},
                status=503,
            )

        thumb_path = await photo_hub.get_thumbnail_path(photo_id)
        if not thumb_path:
            return web.json_response(
                {"error": "Thumbnail not found"},
                status=404,
            )

        return web.FileResponse(thumb_path, headers={
            "Content-Type": "image/jpeg",
            "Cache-Control": "public, max-age=86400",
        })


class DashiePhotoSourcesView(HomeAssistantView):
    """View to list photo sources."""

    url = "/api/dashie/sources"
    name = "api:dashie:sources"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for photo sources."""
        hass: HomeAssistant = request.app["hass"]

        photo_hub: PhotoHub | None = hass.data.get(DOMAIN, {}).get("photo_hub")
        if not photo_hub:
            return web.json_response(
                {"error": "Photo Hub not initialized"},
                status=503,
            )

        sources = await photo_hub.get_sources()

        return web.json_response({
            "sources": sources,
        })


class DashiePhotoImportView(HomeAssistantView):
    """View to import photos from ZIP."""

    url = "/api/dashie/import-zip"
    name = "api:dashie:import"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        """Handle POST request for ZIP import."""
        hass: HomeAssistant = request.app["hass"]

        photo_hub: PhotoHub | None = hass.data.get(DOMAIN, {}).get("photo_hub")
        if not photo_hub:
            return web.json_response(
                {"error": "Photo Hub not initialized"},
                status=503,
            )

        # Read multipart data
        reader = await request.multipart()
        field = await reader.next()

        if field is None or field.name != "file":
            return web.json_response(
                {"error": "No file provided"},
                status=400,
            )

        # Read ZIP file
        zip_data = await field.read()

        if len(zip_data) == 0:
            return web.json_response(
                {"error": "Empty file"},
                status=400,
            )

        # Import photos
        result = await photo_hub.import_zip(zip_data)

        return web.json_response(result)


class DashiePhotoConfigView(HomeAssistantView):
    """View to get photo hub configuration."""

    url = "/api/dashie/config"
    name = "api:dashie:config"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request for configuration."""
        hass: HomeAssistant = request.app["hass"]

        photo_hub: PhotoHub | None = hass.data.get(DOMAIN, {}).get("photo_hub")
        if not photo_hub:
            return web.json_response(
                {"error": "Photo Hub not initialized"},
                status=503,
            )

        total_photos = await photo_hub.get_photo_count()
        sources = await photo_hub.get_sources()

        return web.json_response({
            "version": "1.0.0",
            "total_photos": total_photos,
            "sources": len(sources),
            "features": {
                "streaming": True,
                "import_zip": True,
                "thumbnails": True,
            },
        })


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


def register_photo_api_views(hass: HomeAssistant) -> None:
    """Register all Photo API views."""
    hass.http.register_view(DashiePhotoListView())
    hass.http.register_view(DashiePhotoDetailView())
    hass.http.register_view(DashiePhotoImageView())
    hass.http.register_view(DashiePhotoThumbnailView())
    hass.http.register_view(DashiePhotoSourcesView())
    hass.http.register_view(DashiePhotoImportView())
    hass.http.register_view(DashiePhotoConfigView())

    _LOGGER.info("Registered Dashie Photo API views")
