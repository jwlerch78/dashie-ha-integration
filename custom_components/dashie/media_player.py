"""Media player platform for Dashie integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    API_SET_VOLUME,
    API_PLAY_SOUND,
    API_STOP_SOUND,
    API_PAUSE_SOUND,
    API_RESUME_SOUND,
    API_TEXT_TO_SPEECH,
    API_STOP_TEXT_TO_SPEECH,
)
from .coordinator import DashieCoordinator
from .entity import DashieEntity

_LOGGER = logging.getLogger(__name__)


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
    """Representation of a Dashie device as a media player."""

    _attr_name = "Speaker"
    _attr_icon = "mdi:speaker"

    # Supported features
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
    )

    def __init__(
        self,
        coordinator: DashieCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the media player."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_media_player"
        self._is_playing = False
        self._is_paused = False
        self._media_title: str | None = None

    @property
    def state(self) -> MediaPlayerState:
        """Return the state of the media player."""
        if not self.coordinator.last_update_success:
            return MediaPlayerState.UNAVAILABLE
        if self._is_paused:
            return MediaPlayerState.PAUSED
        if self._is_playing:
            return MediaPlayerState.PLAYING
        return MediaPlayerState.IDLE

    @property
    def volume_level(self) -> float | None:
        """Volume level of the media player (0..1)."""
        if self.coordinator.data:
            # audioVolume is 0-100, convert to 0-1
            volume = self.coordinator.data.get("audioVolume", 50)
            return volume / 100
        return 0.5

    @property
    def media_title(self) -> str | None:
        """Title of current playing media."""
        return self._media_title

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        # Convert 0-1 to 0-100
        level = int(volume * 100)
        await self.coordinator.send_command(API_SET_VOLUME, level=level)
        # Optimistically update local data
        self.coordinator.update_local_data(audioVolume=level)

    async def async_volume_up(self) -> None:
        """Volume up the media player."""
        current = self.volume_level or 0.5
        new_volume = min(1.0, current + 0.1)
        await self.async_set_volume_level(new_volume)

    async def async_volume_down(self) -> None:
        """Volume down the media player."""
        current = self.volume_level or 0.5
        new_volume = max(0.0, current - 0.1)
        await self.async_set_volume_level(new_volume)

    async def async_play_media(
        self,
        media_type: MediaType | str,
        media_id: str,
        enqueue: str | None = None,
        announce: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Play a piece of media.

        Args:
            media_type: Type of media (music, tts, etc.)
            media_id: URL for audio, or text for TTS
            announce: If True, use TTS instead of audio playback
        """
        _LOGGER.debug(
            "Play media: type=%s, id=%s, announce=%s",
            media_type, media_id, announce
        )

        # Handle TTS / announcements
        if announce or (media_type == MediaType.MUSIC and media_id.startswith("tts:")):
            # Extract text from "tts:Hello world" format
            text = media_id[4:] if media_id.startswith("tts:") else media_id
            await self.coordinator.send_command(API_TEXT_TO_SPEECH, text=text)
            self._is_playing = True
            self._media_title = f"TTS: {text[:50]}..." if len(text) > 50 else f"TTS: {text}"

        # Handle HA TTS service output (media-source://tts/...)
        elif media_type == "provider" or "tts" in str(media_id).lower():
            # HA TTS generates a URL, play it as audio
            await self.coordinator.send_command(API_PLAY_SOUND, url=media_id)
            self._is_playing = True
            self._media_title = "TTS Audio"

        # Handle regular audio URLs
        elif media_type in (MediaType.MUSIC, MediaType.URL, "audio"):
            await self.coordinator.send_command(API_PLAY_SOUND, url=media_id)
            self._is_playing = True
            self._media_title = media_id.split("/")[-1][:50]

        else:
            _LOGGER.warning("Unsupported media type: %s", media_type)
            return

        self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        """Stop the media player."""
        # Stop both audio and TTS
        await self.coordinator.send_command(API_STOP_SOUND)
        await self.coordinator.send_command(API_STOP_TEXT_TO_SPEECH)
        self._is_playing = False
        self._is_paused = False
        self._media_title = None
        self.async_write_ha_state()

    async def async_media_pause(self) -> None:
        """Pause the media player."""
        await self.coordinator.send_command(API_PAUSE_SOUND)
        self._is_paused = True
        self._is_playing = False
        self.async_write_ha_state()

    async def async_media_play(self) -> None:
        """Resume the media player."""
        await self.coordinator.send_command(API_RESUME_SOUND)
        self._is_paused = False
        self._is_playing = True
        self.async_write_ha_state()
