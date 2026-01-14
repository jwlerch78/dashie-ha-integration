"""Photo Hub for Dashie - Central photo storage and serving."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import uuid
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import aiofiles
import aiofiles.os
from PIL import Image

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Photo storage paths (relative to HA config dir)
PHOTOS_DIR = "dashie/photos"
THUMBNAILS_DIR = "dashie/thumbnails"
DATABASE_FILE = "dashie/dashie.db"

# Supported image formats
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}

# Thumbnail settings
THUMBNAIL_SIZE = (400, 400)


class PhotoHub:
    """Central photo hub for Dashie screensavers."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the Photo Hub."""
        self.hass = hass
        self._config_dir = Path(hass.config.path())
        self._photos_dir = self._config_dir / PHOTOS_DIR
        self._thumbnails_dir = self._config_dir / THUMBNAILS_DIR
        self._db_path = self._config_dir / DATABASE_FILE
        self._db: sqlite3.Connection | None = None
        self._initialized = False

    async def async_initialize(self) -> bool:
        """Initialize the photo hub (create directories and database)."""
        if self._initialized:
            return True

        try:
            # Create directories
            await aiofiles.os.makedirs(self._photos_dir, exist_ok=True)
            await aiofiles.os.makedirs(self._thumbnails_dir, exist_ok=True)
            await aiofiles.os.makedirs(self._db_path.parent, exist_ok=True)

            # Initialize database
            await self.hass.async_add_executor_job(self._init_database)

            self._initialized = True
            _LOGGER.info("Photo Hub initialized at %s", self._photos_dir)
            return True

        except Exception as err:
            _LOGGER.error("Failed to initialize Photo Hub: %s", err)
            return False

    def _init_database(self) -> None:
        """Initialize SQLite database with schema."""
        self._db = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row

        cursor = self._db.cursor()

        # Photo sources table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS photo_sources (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                name TEXT,
                config TEXT,
                enabled INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_sync TEXT
            )
        """)

        # Photos table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                remote_id TEXT,
                filename TEXT NOT NULL,
                local_path TEXT,
                width INTEGER,
                height INTEGER,
                taken_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT,
                synced_at TEXT,
                FOREIGN KEY (source_id) REFERENCES photo_sources(id) ON DELETE CASCADE
            )
        """)

        # Albums table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS albums (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                remote_id TEXT,
                name TEXT NOT NULL,
                photo_count INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                last_sync TEXT,
                FOREIGN KEY (source_id) REFERENCES photo_sources(id) ON DELETE CASCADE
            )
        """)

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_photos_source ON photos(source_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_photos_taken ON photos(taken_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_albums_source ON albums(source_id)")

        # Create default "imported" source if not exists
        cursor.execute("""
            INSERT OR IGNORE INTO photo_sources (id, type, name, enabled)
            VALUES ('imported', 'imported', 'Imported Photos', 1)
        """)

        # Create default "local" source if not exists
        cursor.execute("""
            INSERT OR IGNORE INTO photo_sources (id, type, name, enabled)
            VALUES ('local', 'local', 'Local Folder', 1)
        """)

        self._db.commit()
        _LOGGER.debug("Database initialized at %s", self._db_path)

    async def get_photos(
        self,
        source_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        random: bool = False,
    ) -> list[dict[str, Any]]:
        """Get photos from the database."""
        if not self._initialized:
            await self.async_initialize()

        def _query():
            cursor = self._db.cursor()

            sql = "SELECT * FROM photos WHERE 1=1"
            params = []

            if source_id:
                sql += " AND source_id = ?"
                params.append(source_id)

            if random:
                sql += " ORDER BY RANDOM()"
            else:
                sql += " ORDER BY taken_at DESC, created_at DESC"

            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor.execute(sql, params)
            rows = cursor.fetchall()

            return [dict(row) for row in rows]

        return await self.hass.async_add_executor_job(_query)

    async def get_photo(self, photo_id: str) -> dict[str, Any] | None:
        """Get a single photo by ID."""
        if not self._initialized:
            await self.async_initialize()

        def _query():
            cursor = self._db.cursor()
            cursor.execute("SELECT * FROM photos WHERE id = ?", (photo_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

        return await self.hass.async_add_executor_job(_query)

    async def get_photo_count(self, source_id: str | None = None) -> int:
        """Get total photo count."""
        if not self._initialized:
            await self.async_initialize()

        def _query():
            cursor = self._db.cursor()
            if source_id:
                cursor.execute(
                    "SELECT COUNT(*) as count FROM photos WHERE source_id = ?",
                    (source_id,)
                )
            else:
                cursor.execute("SELECT COUNT(*) as count FROM photos")
            return cursor.fetchone()["count"]

        return await self.hass.async_add_executor_job(_query)

    async def get_photo_path(self, photo_id: str) -> Path | None:
        """Get the file path for a photo."""
        photo = await self.get_photo(photo_id)
        if not photo or not photo.get("local_path"):
            return None

        path = self._photos_dir / photo["local_path"]
        if path.exists():
            return path
        return None

    async def get_thumbnail_path(self, photo_id: str) -> Path | None:
        """Get the thumbnail path for a photo."""
        thumb_path = self._thumbnails_dir / f"{photo_id}_thumb.jpg"
        if thumb_path.exists():
            return thumb_path

        # Generate thumbnail if it doesn't exist
        photo_path = await self.get_photo_path(photo_id)
        if photo_path:
            await self._generate_thumbnail(photo_path, thumb_path)
            if thumb_path.exists():
                return thumb_path

        return None

    async def _generate_thumbnail(self, source_path: Path, thumb_path: Path) -> bool:
        """Generate a thumbnail for a photo."""
        def _generate():
            try:
                with Image.open(source_path) as img:
                    # Convert to RGB if necessary (for PNG with transparency)
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")

                    img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
                    img.save(thumb_path, "JPEG", quality=85)
                    return True
            except Exception as err:
                _LOGGER.error("Failed to generate thumbnail for %s: %s", source_path, err)
                return False

        return await self.hass.async_add_executor_job(_generate)

    async def add_photo(
        self,
        file_data: bytes,
        filename: str,
        source_id: str = "imported",
        metadata: dict | None = None,
    ) -> str | None:
        """Add a photo to the hub."""
        if not self._initialized:
            await self.async_initialize()

        # Generate unique ID
        photo_id = str(uuid.uuid4())

        # Sanitize filename and ensure unique
        safe_filename = self._sanitize_filename(filename)
        ext = Path(safe_filename).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            _LOGGER.warning("Unsupported file type: %s", ext)
            return None

        # Create unique filename with ID prefix
        local_filename = f"{photo_id[:8]}_{safe_filename}"
        local_path = self._photos_dir / source_id / local_filename

        # Ensure source directory exists
        await aiofiles.os.makedirs(local_path.parent, exist_ok=True)

        # Write file
        async with aiofiles.open(local_path, "wb") as f:
            await f.write(file_data)

        # Get image dimensions
        width, height = await self._get_image_dimensions(local_path)

        # Extract EXIF taken_at if available
        taken_at = await self._extract_taken_at(local_path)

        # Store relative path in database
        relative_path = f"{source_id}/{local_filename}"

        def _insert():
            cursor = self._db.cursor()
            cursor.execute("""
                INSERT INTO photos (id, source_id, filename, local_path, width, height, taken_at, metadata, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                photo_id,
                source_id,
                filename,
                relative_path,
                width,
                height,
                taken_at,
                str(metadata) if metadata else None,
                datetime.utcnow().isoformat(),
            ))
            self._db.commit()

        await self.hass.async_add_executor_job(_insert)

        _LOGGER.info("Added photo %s (%s)", photo_id, filename)
        return photo_id

    async def delete_photo(self, photo_id: str) -> bool:
        """Delete a photo from the hub."""
        photo = await self.get_photo(photo_id)
        if not photo:
            return False

        # Delete files
        if photo.get("local_path"):
            photo_path = self._photos_dir / photo["local_path"]
            if photo_path.exists():
                await aiofiles.os.remove(photo_path)

        thumb_path = self._thumbnails_dir / f"{photo_id}_thumb.jpg"
        if thumb_path.exists():
            await aiofiles.os.remove(thumb_path)

        # Delete from database
        def _delete():
            cursor = self._db.cursor()
            cursor.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
            self._db.commit()

        await self.hass.async_add_executor_job(_delete)

        _LOGGER.info("Deleted photo %s", photo_id)
        return True

    async def import_zip(self, zip_data: bytes) -> dict[str, Any]:
        """Import photos from a ZIP file."""
        if not self._initialized:
            await self.async_initialize()

        imported = []
        skipped = []
        errors = []

        def _extract_and_import():
            nonlocal imported, skipped, errors

            with zipfile.ZipFile(BytesIO(zip_data)) as zf:
                for name in zf.namelist():
                    # Skip directories
                    if name.endswith("/"):
                        continue

                    # Check extension
                    ext = Path(name).suffix.lower()
                    if ext not in SUPPORTED_EXTENSIONS:
                        skipped.append(name)
                        continue

                    try:
                        # Read file
                        file_data = zf.read(name)

                        # Get just the filename (ignore nested folder structure)
                        filename = Path(name).name

                        # Check for duplicates by hash
                        file_hash = hashlib.md5(file_data).hexdigest()

                        imported.append({
                            "filename": filename,
                            "data": file_data,
                            "hash": file_hash,
                        })

                    except Exception as err:
                        errors.append({"file": name, "error": str(err)})
                        _LOGGER.error("Failed to extract %s: %s", name, err)

        await self.hass.async_add_executor_job(_extract_and_import)

        # Add photos to database
        added_ids = []
        for item in imported:
            photo_id = await self.add_photo(
                item["data"],
                item["filename"],
                source_id="imported",
            )
            if photo_id:
                added_ids.append(photo_id)

        return {
            "imported": len(added_ids),
            "skipped": len(skipped),
            "errors": errors,
            "photo_ids": added_ids[:10],  # Return first 10 IDs
        }

    async def get_sources(self) -> list[dict[str, Any]]:
        """Get all configured photo sources."""
        if not self._initialized:
            await self.async_initialize()

        def _query():
            cursor = self._db.cursor()
            cursor.execute("""
                SELECT ps.*, COUNT(p.id) as photo_count
                FROM photo_sources ps
                LEFT JOIN photos p ON p.source_id = ps.id
                GROUP BY ps.id
                ORDER BY ps.name
            """)
            return [dict(row) for row in cursor.fetchall()]

        return await self.hass.async_add_executor_job(_query)

    async def _get_image_dimensions(self, path: Path) -> tuple[int | None, int | None]:
        """Get image dimensions."""
        def _get_dims():
            try:
                with Image.open(path) as img:
                    return img.size
            except Exception:
                return (None, None)

        return await self.hass.async_add_executor_job(_get_dims)

    async def _extract_taken_at(self, path: Path) -> str | None:
        """Extract taken_at date from EXIF data."""
        def _extract():
            try:
                with Image.open(path) as img:
                    exif = img._getexif()
                    if exif:
                        # DateTimeOriginal tag
                        date_str = exif.get(36867) or exif.get(306)
                        if date_str:
                            # Parse EXIF date format: "YYYY:MM:DD HH:MM:SS"
                            dt = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                            return dt.isoformat()
            except Exception:
                pass
            return None

        return await self.hass.async_add_executor_job(_extract)

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize a filename for safe storage."""
        # Remove path separators and other dangerous characters
        safe = "".join(c for c in filename if c.isalnum() or c in "._- ")
        return safe or "unnamed"

    async def scan_local_folder(self, folder_path: str) -> int:
        """Scan a local folder and add photos to the database."""
        if not self._initialized:
            await self.async_initialize()

        folder = Path(folder_path)
        if not folder.exists():
            _LOGGER.warning("Folder does not exist: %s", folder_path)
            return 0

        added = 0

        for file_path in folder.iterdir():
            if not file_path.is_file():
                continue

            ext = file_path.suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue

            # Check if already in database by filename
            existing = await self._find_photo_by_path(str(file_path))
            if existing:
                continue

            # Read and add photo
            try:
                async with aiofiles.open(file_path, "rb") as f:
                    file_data = await f.read()

                photo_id = await self.add_photo(
                    file_data,
                    file_path.name,
                    source_id="local",
                )
                if photo_id:
                    added += 1

            except Exception as err:
                _LOGGER.error("Failed to add %s: %s", file_path, err)

        _LOGGER.info("Scanned local folder %s, added %d photos", folder_path, added)
        return added

    async def _find_photo_by_path(self, path: str) -> dict | None:
        """Find a photo by its original path."""
        filename = Path(path).name

        def _query():
            cursor = self._db.cursor()
            cursor.execute(
                "SELECT * FROM photos WHERE filename = ? AND source_id = 'local'",
                (filename,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

        return await self.hass.async_add_executor_job(_query)
