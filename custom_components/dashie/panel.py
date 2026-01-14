"""Web panel for Dashie Photo Hub - drag and drop upload UI."""
from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# HTML for the photo upload panel
PANEL_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashie Photo Hub</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--primary-background-color, #fafafa);
            color: var(--primary-text-color, #212121);
            padding: 24px;
            min-height: 100vh;
        }
        .container { max-width: 800px; margin: 0 auto; }
        h1 {
            font-size: 24px;
            font-weight: 500;
            margin-bottom: 8px;
        }
        .subtitle {
            color: var(--secondary-text-color, #727272);
            margin-bottom: 24px;
        }
        .stats {
            display: flex;
            gap: 24px;
            margin-bottom: 24px;
        }
        .stat {
            background: var(--card-background-color, white);
            padding: 16px 24px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .stat-value {
            font-size: 32px;
            font-weight: 600;
            color: var(--primary-color, #03a9f4);
        }
        .stat-label {
            font-size: 14px;
            color: var(--secondary-text-color, #727272);
        }
        .upload-zone {
            background: var(--card-background-color, white);
            border: 2px dashed var(--divider-color, #e0e0e0);
            border-radius: 12px;
            padding: 48px;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s;
            margin-bottom: 24px;
        }
        .upload-zone:hover, .upload-zone.dragover {
            border-color: var(--primary-color, #03a9f4);
            background: var(--primary-color, #03a9f4)10;
        }
        .upload-icon {
            font-size: 48px;
            margin-bottom: 16px;
        }
        .upload-text {
            font-size: 18px;
            margin-bottom: 8px;
        }
        .upload-hint {
            font-size: 14px;
            color: var(--secondary-text-color, #727272);
        }
        .or {
            color: var(--secondary-text-color, #727272);
            margin: 12px 0;
        }
        .browse-btn {
            display: inline-block;
            background: var(--primary-color, #03a9f4);
            color: white;
            padding: 12px 24px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 500;
        }
        .browse-btn:hover {
            opacity: 0.9;
        }
        input[type="file"] { display: none; }
        .progress-container {
            display: none;
            background: var(--card-background-color, white);
            border-radius: 8px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .progress-container.visible { display: block; }
        .progress-bar {
            height: 8px;
            background: var(--divider-color, #e0e0e0);
            border-radius: 4px;
            overflow: hidden;
            margin-bottom: 12px;
        }
        .progress-fill {
            height: 100%;
            background: var(--primary-color, #03a9f4);
            width: 0%;
            transition: width 0.3s;
        }
        .progress-text {
            font-size: 14px;
            color: var(--secondary-text-color, #727272);
        }
        .result {
            display: none;
            background: var(--card-background-color, white);
            border-radius: 8px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .result.visible { display: block; }
        .result.success { border-left: 4px solid #4caf50; }
        .result.error { border-left: 4px solid #f44336; }
        .result-icon { font-size: 24px; margin-right: 12px; }
        .photos-section {
            background: var(--card-background-color, white);
            border-radius: 8px;
            padding: 24px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .photos-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }
        .photos-title { font-size: 18px; font-weight: 500; }
        .photos-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
            gap: 12px;
        }
        .photo-thumb {
            aspect-ratio: 1;
            border-radius: 8px;
            overflow: hidden;
            background: var(--divider-color, #e0e0e0);
        }
        .photo-thumb img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        .empty-state {
            text-align: center;
            padding: 48px;
            color: var(--secondary-text-color, #727272);
        }
        .refresh-btn {
            background: none;
            border: 1px solid var(--divider-color, #e0e0e0);
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
        }
        .refresh-btn:hover {
            background: var(--divider-color, #e0e0e0)40;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Dashie Photo Hub</h1>
        <p class="subtitle">Upload photos for your Dashie screensavers</p>

        <div class="stats">
            <div class="stat">
                <div class="stat-value" id="photoCount">-</div>
                <div class="stat-label">Photos</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="sourceCount">-</div>
                <div class="stat-label">Sources</div>
            </div>
        </div>

        <div class="upload-zone" id="uploadZone">
            <div class="upload-icon">📁</div>
            <div class="upload-text">Drag & drop photos or ZIP file here</div>
            <div class="upload-hint">Supports JPG, PNG, WebP, GIF, HEIC</div>
            <div class="or">or</div>
            <label for="fileInput" class="browse-btn">Browse Files</label>
            <input type="file" id="fileInput" accept=".jpg,.jpeg,.png,.webp,.gif,.heic,.zip" multiple>
        </div>

        <div class="progress-container" id="progressContainer">
            <div class="progress-bar">
                <div class="progress-fill" id="progressFill"></div>
            </div>
            <div class="progress-text" id="progressText">Uploading...</div>
        </div>

        <div class="result" id="result">
            <span class="result-icon" id="resultIcon"></span>
            <span id="resultText"></span>
        </div>

        <div class="photos-section">
            <div class="photos-header">
                <span class="photos-title">Recent Photos</span>
                <button class="refresh-btn" onclick="loadPhotos()">Refresh</button>
            </div>
            <div class="photos-grid" id="photosGrid">
                <div class="empty-state">Loading...</div>
            </div>
        </div>
    </div>

    <script>
        const uploadZone = document.getElementById('uploadZone');
        const fileInput = document.getElementById('fileInput');
        const progressContainer = document.getElementById('progressContainer');
        const progressFill = document.getElementById('progressFill');
        const progressText = document.getElementById('progressText');
        const result = document.getElementById('result');
        const resultIcon = document.getElementById('resultIcon');
        const resultText = document.getElementById('resultText');

        // Drag and drop
        uploadZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            uploadZone.classList.add('dragover');
        });

        uploadZone.addEventListener('dragleave', () => {
            uploadZone.classList.remove('dragover');
        });

        uploadZone.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadZone.classList.remove('dragover');
            handleFiles(e.dataTransfer.files);
        });

        uploadZone.addEventListener('click', () => {
            fileInput.click();
        });

        fileInput.addEventListener('change', () => {
            handleFiles(fileInput.files);
        });

        async function handleFiles(files) {
            if (files.length === 0) return;

            progressContainer.classList.add('visible');
            result.classList.remove('visible', 'success', 'error');
            progressFill.style.width = '0%';

            let totalImported = 0;
            let errors = [];

            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                progressText.textContent = `Uploading ${file.name} (${i + 1}/${files.length})...`;
                progressFill.style.width = `${((i + 0.5) / files.length) * 100}%`;

                try {
                    if (file.name.toLowerCase().endsWith('.zip')) {
                        // Upload as ZIP
                        const result = await uploadZip(file);
                        totalImported += result.imported || 0;
                        if (result.errors && result.errors.length > 0) {
                            errors.push(...result.errors);
                        }
                    } else {
                        // Upload as single image
                        const result = await uploadImage(file);
                        if (result.id) {
                            totalImported += 1;
                        } else {
                            errors.push({ file: file.name, error: 'Upload failed' });
                        }
                    }
                } catch (err) {
                    errors.push({ file: file.name, error: err.message });
                }

                progressFill.style.width = `${((i + 1) / files.length) * 100}%`;
            }

            progressContainer.classList.remove('visible');
            result.classList.add('visible');

            if (totalImported > 0) {
                result.classList.add('success');
                resultIcon.textContent = '✓';
                resultText.textContent = `Imported ${totalImported} photo${totalImported !== 1 ? 's' : ''}`;
                if (errors.length > 0) {
                    resultText.textContent += ` (${errors.length} errors)`;
                }
            } else {
                result.classList.add('error');
                resultIcon.textContent = '✗';
                resultText.textContent = errors.length > 0 ? errors[0].error : 'Upload failed';
            }

            // Refresh stats and photos
            loadStats();
            loadPhotos();

            // Clear input
            fileInput.value = '';
        }

        async function uploadZip(file) {
            const formData = new FormData();
            formData.append('file', file);

            const response = await fetch('/api/dashie/import-zip', {
                method: 'POST',
                body: formData,
                headers: {
                    'Authorization': 'Bearer ' + getToken()
                }
            });

            if (!response.ok) {
                throw new Error('Upload failed: ' + response.status);
            }

            return await response.json();
        }

        async function uploadImage(file) {
            const formData = new FormData();
            formData.append('file', file);

            const response = await fetch('/api/dashie/photos/upload', {
                method: 'POST',
                body: formData,
                headers: {
                    'Authorization': 'Bearer ' + getToken()
                }
            });

            if (!response.ok) {
                throw new Error('Upload failed: ' + response.status);
            }

            return await response.json();
        }

        async function loadStats() {
            try {
                const response = await fetch('/api/dashie/config', {
                    headers: { 'Authorization': 'Bearer ' + getToken() }
                });
                const data = await response.json();
                document.getElementById('photoCount').textContent = data.total_photos || 0;
                document.getElementById('sourceCount').textContent = data.sources || 0;
            } catch (err) {
                console.error('Failed to load stats:', err);
            }
        }

        async function loadPhotos() {
            const grid = document.getElementById('photosGrid');
            try {
                const response = await fetch('/api/dashie/photos?limit=20', {
                    headers: { 'Authorization': 'Bearer ' + getToken() }
                });
                const data = await response.json();

                if (data.photos && data.photos.length > 0) {
                    grid.innerHTML = data.photos.map(photo => `
                        <div class="photo-thumb">
                            <img src="${photo.thumb_url}" alt="${photo.filename}" loading="lazy">
                        </div>
                    `).join('');
                } else {
                    grid.innerHTML = '<div class="empty-state">No photos yet. Upload some!</div>';
                }
            } catch (err) {
                grid.innerHTML = '<div class="empty-state">Failed to load photos</div>';
                console.error('Failed to load photos:', err);
            }
        }

        function getToken() {
            // Try to get token from parent window (HA frontend) or use empty string
            try {
                return window.parent.__hass?.auth?.data?.access_token || '';
            } catch {
                return '';
            }
        }

        // Initial load
        loadStats();
        loadPhotos();
    </script>
</body>
</html>
"""


class DashiePanelView(HomeAssistantView):
    """View to serve the Dashie Photo Hub panel."""

    url = "/dashie-photos"
    name = "dashie:panel"
    requires_auth = False  # Panel HTML served without auth; API calls use auth

    async def get(self, request: web.Request) -> web.Response:
        """Serve the panel HTML."""
        return web.Response(
            text=PANEL_HTML,
            content_type="text/html",
        )


class DashiePhotoUploadView(HomeAssistantView):
    """View to handle single photo uploads."""

    url = "/api/dashie/photos/upload"
    name = "api:dashie:photos:upload"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        """Handle single photo upload."""
        from .photo_hub import PhotoHub

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

        filename = field.filename or "upload.jpg"
        file_data = await field.read()

        if len(file_data) == 0:
            return web.json_response(
                {"error": "Empty file"},
                status=400,
            )

        # Add photo
        photo_id = await photo_hub.add_photo(file_data, filename)

        if photo_id:
            return web.json_response({
                "id": photo_id,
                "filename": filename,
            })
        else:
            return web.json_response(
                {"error": "Failed to save photo"},
                status=500,
            )


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register the Dashie Photo Hub panel."""
    hass.http.register_view(DashiePanelView())
    hass.http.register_view(DashiePhotoUploadView())

    # Register as a panel in HA sidebar using the frontend integration
    from homeassistant.components.frontend import async_register_built_in_panel

    async_register_built_in_panel(
        hass,
        component_name="iframe",
        sidebar_title="Dashie Photos",
        sidebar_icon="mdi:image-multiple",
        frontend_url_path="dashie-photos",
        config={"url": "/dashie-photos"},
        require_admin=False,
    )

    _LOGGER.info("Registered Dashie Photo Hub panel")
