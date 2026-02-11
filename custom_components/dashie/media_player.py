"""Media player platform for Dashie integration.

Modeled exactly after Fully Kiosk Browser's media player for Music Assistant
compatibility. Kept intentionally simple - just play, stop, volume.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_DEVICE_ID, API_SET_VOLUME, API_PLAY_SOUND, API_STOP_SOUND
from .coordinator import DashieCoordinator
from .entity import DashieEntity

_LOGGER = logging.getLogger(__name__)

# Match FK's minimal feature set exactly
MEDIA_SUPPORT_DASHIE = (
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.VOLUME_SET
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dashie media player from a config entry."""
    coordinator: DashieCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = entry.data[CONF_DEVICE_ID]
    async_add_entities([DashieMediaPlayer(coordinator, device_id)])


class DashieMediaPlayer(DashieEntity, MediaPlayerEntity):
    """Representation of a Dashie device as a media player.

    Kept intentionally simple to match Fully Kiosk Browser's implementation.
    """

    _attr_name = "Speaker"
    _attr_supported_features = MEDIA_SUPPORT_DASHIE
    _attr_assumed_state = True

    def __init__(
        self,
        coordinator: DashieCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the media player entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_media_player"
        self._attr_state = MediaPlayerState.IDLE

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a piece of media."""
        # Resolve media source URLs (media-source://...)
        if media_source.is_media_source_id(media_id):
            play_item = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = async_process_play_media_url(self.hass, play_item.url)

        # Normalize MIME types
        if isinstance(media_type, str):
            if media_type.startswith("audio/"):
                media_type = MediaType.MUSIC

        # Play audio
        if media_type == MediaType.MUSIC:
            self._attr_media_content_type = MediaType.MUSIC
            await self.coordinator.send_command(API_PLAY_SOUND, url=media_id)
        else:
            raise HomeAssistantError(f"Unsupported media type {media_type}")

        self._attr_state = MediaPlayerState.PLAYING
        self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        """Stop playing media."""
        await self.coordinator.send_command(API_STOP_SOUND)
        self._attr_state = MediaPlayerState.IDLE
        self.async_write_ha_state()

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        await self.coordinator.send_command(API_SET_VOLUME, level=int(volume * 100))
        self._attr_volume_level = volume
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_state = (
            MediaPlayerState.PLAYING
            if self.coordinator.data and "soundUrlPlaying" in self.coordinator.data
            else MediaPlayerState.IDLE
        )
        self.async_write_ha_state()
