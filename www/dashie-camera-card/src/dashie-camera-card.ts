/**
 * Dashie Camera Card
 * Adaptive camera card for Home Assistant
 */

import { LitElement, html, css } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import type {
  HomeAssistant,
  DashieCameraCardConfig,
  Platform,
  IPlayer,
  PlayerType,
} from './types';
import { PlatformDetector } from './platform-detector';
import { HLSPlayer } from './players/hls-player';
import { WebRTCPlayer } from './players/webrtc-player';
import { NativeRtspPlayer } from './players/native-rtsp-player';
import { MP4Player } from './players/mp4-player';
import { MSEPlayer } from './players/mse-player';

export class DashieCameraCard extends LitElement {
  @property({ attribute: false }) public hass!: HomeAssistant;
  @state() private config!: DashieCameraCardConfig;
  @state() private platform: Platform = 'browser';
  @state() private player: IPlayer | null = null;
  @state() private loading: boolean = true;
  @state() private error: string | null = null;
  @state() private maximized: boolean = false;

  static styles = css`
    :host {
      display: block;
      background: var(--ha-card-background, var(--card-background-color, white));
      border-radius: var(--ha-card-border-radius, 12px);
      box-shadow: var(
        --ha-card-box-shadow,
        0 2px 4px rgba(0, 0, 0, 0.1)
      );
      overflow: hidden;
    }

    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px;
      border-bottom: 1px solid var(--divider-color);
    }

    .card-title {
      font-size: 18px;
      font-weight: 500;
      color: var(--primary-text-color);
    }

    .platform-badge {
      font-size: 12px;
      padding: 4px 8px;
      border-radius: 4px;
      background: var(--primary-color);
      color: var(--text-primary-color);
    }

    .video-container {
      position: relative;
      width: 100%;
      background: #000;
      cursor: pointer;
      min-height: 200px;
    }

    .video-container:hover {
      opacity: 0.95;
    }

    .loading-overlay,
    .error-overlay {
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.7);
      color: white;
    }

    .spinner {
      width: 40px;
      height: 40px;
      border: 4px solid rgba(255, 255, 255, 0.3);
      border-top-color: white;
      border-radius: 50%;
      animation: spin 1s linear infinite;
    }

    @keyframes spin {
      to {
        transform: rotate(360deg);
      }
    }

    .error-text {
      margin-top: 16px;
      font-size: 14px;
      text-align: center;
      padding: 0 16px;
    }

    .maximize-overlay {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.95);
      z-index: 9999;
      display: flex;
      flex-direction: column;
    }

    .maximize-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px;
      background: rgba(0, 0, 0, 0.8);
    }

    .maximize-title {
      font-size: 20px;
      color: white;
    }

    .close-button {
      background: none;
      border: none;
      color: white;
      font-size: 24px;
      cursor: pointer;
      padding: 8px;
    }

    .maximize-video {
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
    }
  `;

  setConfig(config: DashieCameraCardConfig): void {
    // Either entity, stream_name, or camgrid config is required
    const hasBirdseye = config.camgrid?.cameras?.length || config.camgrid?.stream_name;
    if (!config.entity && !config.stream_name && !hasBirdseye) {
      throw new Error('You need to define either an entity, stream_name, or camgrid config');
    }

    this.config = {
      tap_action: 'maximize',
      hold_action: 'none',
      double_tap_action: 'none',
      quality: 'auto',
      protocol: 'auto',
      prefer_codec: 'auto',
      ...config,
    };

    console.log('[Dashie Camera Card] Config set:', this.config);
  }

  // Visibility tracking for memory optimization
  private intersectionObserver: IntersectionObserver | null = null;
  private isVisible = true;
  private wasPlaying = false;
  private hasInitialized = false; // Track if firstUpdated has run

  connectedCallback(): void {
    super.connectedCallback();
    this.platform = PlatformDetector.detectPlatform();
    console.log('[Dashie Camera Card] Platform detected:', this.platform);

    // Set up visibility tracking - stop streams when card scrolls out of view
    this.setupVisibilityTracking();

    // Handle page visibility changes (tab switching, etc.)
    document.addEventListener('visibilitychange', this.handlePageVisibility);

    // Reinitialize stream if we were previously disconnected (navigation away/back)
    // Only do this if firstUpdated has already run (hasInitialized = true)
    if (this.hasInitialized && !this.player) {
      console.log('[Dashie Camera Card] Reconnected after navigation, reinitializing stream');
      this.initializeStream();
    }
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();

    // Clean up visibility tracking
    if (this.intersectionObserver) {
      this.intersectionObserver.disconnect();
      this.intersectionObserver = null;
    }
    document.removeEventListener('visibilitychange', this.handlePageVisibility);

    this.destroyPlayer();
  }

  /**
   * Stop streams when card scrolls out of view to save memory and bandwidth.
   */
  private setupVisibilityTracking(): void {
    if (!('IntersectionObserver' in window)) return;

    this.intersectionObserver = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (!entry) return;

        const wasVisible = this.isVisible;
        this.isVisible = entry.isIntersecting;

        if (wasVisible && !this.isVisible) {
          // Card scrolled out of view - stop stream to free memory
          console.log('[Dashie Camera Card] Card hidden, stopping stream');
          this.wasPlaying = !!this.player;
          this.player?.stop();
        } else if (!wasVisible && this.isVisible && this.wasPlaying) {
          // Card scrolled back into view - restart stream
          console.log('[Dashie Camera Card] Card visible, restarting stream');
          this.initializeStream();
        }
      },
      { threshold: 0.1 } // Trigger when 10% visible
    );

    this.intersectionObserver.observe(this);
  }

  /**
   * Handle page visibility changes (tab switching, screen off, etc.)
   */
  private handlePageVisibility = (): void => {
    if (document.hidden) {
      console.log('[Dashie Camera Card] Page hidden, stopping stream');
      this.wasPlaying = !!this.player;
      this.player?.stop();
    } else if (this.wasPlaying && this.isVisible) {
      console.log('[Dashie Camera Card] Page visible, restarting stream');
      this.initializeStream();
    }
  };

  protected firstUpdated(): void {
    this.hasInitialized = true;
    this.initializeStream();
  }

  private async initializeStream(): Promise<void> {
    try {
      this.loading = true;
      this.error = null;

      const streamConfig = await this.getStreamConfig();
      console.log('[Dashie Camera Card] Stream config:', streamConfig);

      await this.createPlayer(streamConfig.url, streamConfig.playerType);

      this.loading = false;
    } catch (error) {
      console.error('[Dashie Camera Card] Failed to initialize stream:', error);
      this.error = error instanceof Error ? error.message : 'Failed to load stream';
      this.loading = false;
    }
  }

  /**
   * Get the stream URL based on platform and protocol.
   * Returns { url, playerType } to indicate which player to use.
   *
   * Platform Strategy (2026-02-02):
   * - Browsers: WebRTC (fast, firewall-friendly via go2rtc)
   * - Dashie Tablets: MSE via go2rtc stream.mp4 (plays in WebView, no native overlay complexity)
   * - Fallback: HLS (universal compatibility)
   */
  private async getStreamConfig(): Promise<{ url: string; playerType: PlayerType }> {
    // BIRDSEYE MODE: Use pre-configured stream or try dynamic provisioning
    if (this.config.camgrid?.stream_name || this.config.camgrid?.cameras?.length) {
      console.log('[Dashie Camera Card] Birdseye mode');

      // Get go2rtc URL
      const go2rtcUrl = this.config.go2rtc_url || `http://${window.location.hostname}:1984`;

      // If stream_name is provided, use it directly (pre-configured in go2rtc.yaml)
      if (this.config.camgrid.stream_name) {
        const streamName = this.config.camgrid.stream_name;
        console.log('[Dashie Camera Card] Using pre-configured camgrid stream:', streamName);

        // Verify the stream exists
        const exists = await this.streamExistsInGo2rtc(go2rtcUrl, streamName);
        if (!exists) {
          throw new Error(`Birdseye stream not found in go2rtc: ${streamName}. Add it to go2rtc.yaml.`);
        }

        const mp4Url = `${go2rtcUrl}/api/stream.mp4?src=${streamName}`;
        console.log('[Dashie Camera Card] Birdseye stream URL:', mp4Url);
        return { url: mp4Url, playerType: 'mp4' };
      }

      // Otherwise, try dynamic provisioning with cameras array
      // Validate all camera entities exist
      for (const cam of this.config.camgrid.cameras!) {
        if (!this.hass.states[cam]) {
          throw new Error(`CamGrid camera entity not found: ${cam}`);
        }
      }

      // Provision the CamGrid stream via HA service
      const streamName = await this.provisionCamGridStream(go2rtcUrl);

      // Return fMP4 URL for playback
      const mp4Url = `${go2rtcUrl}/api/stream.mp4?src=${streamName}`;
      console.log('[Dashie Camera Card] CamGrid stream URL:', mp4Url);
      return { url: mp4Url, playerType: 'mp4' };
    }

    // Entity is optional if stream_name is provided
    const entity = this.config.entity ? this.hass.states[this.config.entity] : null;

    // If entity is specified but not found, that's an error
    if (this.config.entity && !entity) {
      throw new Error(`Entity not found: ${this.config.entity}`);
    }

    // Check if it's a Frigate camera (only if entity exists)
    const frigateUrl = this.config.frigate?.url || entity?.attributes?.frigate_url;

    // Get stream name (from config, or from entity ID)
    // HA's go2rtc uses the FULL entity ID as stream name (including camera. prefix)
    const streamName = this.config.stream_name || this.config.entity;

    if (!streamName) {
      throw new Error('Could not determine stream name - specify entity or stream_name');
    }

    console.log('[Dashie Camera Card] Stream name:', streamName);
    console.log('[Dashie Camera Card] Platform:', this.platform);
    console.log('[Dashie Camera Card] Config protocol:', this.config.protocol);

    // Determine if we need to use HA's proxy (remote access over HTTPS)
    const isRemoteAccess = window.location.protocol === 'https:';
    const hasExplicitGo2rtcUrl = !!this.config.go2rtc_url;

    console.log('[Dashie Camera Card] Remote access:', isRemoteAccess, 'Has explicit go2rtc_url:', hasExplicitGo2rtcUrl);

    // For remote access WITHOUT explicit go2rtc_url, use HA's camera stream API
    // This provides an HLS URL that works through HA's authentication proxy
    if (isRemoteAccess && !hasExplicitGo2rtcUrl && this.config.entity) {
      console.log('[Dashie Camera Card] Using HA camera stream API for remote access');
      return await this.getHaCameraStream();
    }

    // For local access or when explicit go2rtc_url is provided
    // Auto-detect go2rtc at same host as HA (WebRTC addon runs on port 1984)
    const go2rtcUrl = this.config.go2rtc_url ||
                      frigateUrl?.replace(':5000', ':1984') ||
                      `http://${window.location.hostname}:1984`;
    console.log('[Dashie Camera Card] Using direct go2rtc:', go2rtcUrl);

    // CRITICAL FINDING (2026-02-03): Samsung tablet hardware decoder limitation
    // The SM-X200 tablet cannot decode 2560x1440 H.264 High Profile video
    // Solution: Transcode to 720p H.264 Baseline Profile

    const isDashieTablet = this.platform === 'dashie-tablet';
    const wantsAutoProtocol = this.config.protocol === 'auto' || this.config.protocol === undefined;
    const wantsTranscode = this.config.transcode?.enabled === true ||
                           (this.config.transcode?.enabled !== false && isDashieTablet);

    // Dynamic stream provisioning: Create transcoded stream referencing entity name directly
    // This works because go2rtc can reference streams by entity ID (either from HA's built-in go2rtc
    // or manually configured streams that match the entity naming convention)
    if (this.config.entity && wantsTranscode) {
      console.log('[Dashie Camera Card] Attempting dynamic transcode provisioning for entity:', this.config.entity);

      try {
        // First verify the base stream exists in go2rtc (required for transcoding)
        const baseStreamExists = await this.streamExistsInGo2rtc(go2rtcUrl, this.config.entity);

        if (!baseStreamExists) {
          console.log('[Dashie Camera Card] Base stream not in go2rtc, skipping transcoding:', this.config.entity);
          // Skip transcoding attempt - will fall through to HLS fallback below
        } else {
          const resolution = this.config.transcode?.resolution || '720p';
          const transcodedStreamName = `${this.config.entity}_${resolution}`;

          // Build FFmpeg transcode source referencing the entity directly
          const transcodeSource = this.buildTranscodeSource(this.config.entity, resolution);

          console.log('[Dashie Camera Card] Provisioning transcoded stream:', {
            source: this.config.entity,
            transcodedStreamName,
            transcodeSource,
          });

          // Create the transcoded stream in go2rtc
          const success = await this.provisionGo2rtcStream(go2rtcUrl, transcodedStreamName, transcodeSource);

          if (success) {
            const mp4Url = `${go2rtcUrl}/api/stream.mp4?src=${transcodedStreamName}`;
            console.log('[Dashie Camera Card] Using dynamically provisioned transcoded stream:', mp4Url);
            return { url: mp4Url, playerType: 'mp4' };
          } else {
            console.log('[Dashie Camera Card] Failed to provision transcoded stream, falling back');
          }
        }
      } catch (error) {
        console.warn('[Dashie Camera Card] Dynamic provisioning failed:', error);
        // Fall through to use existing stream configuration
      }
    }

    // Fallback for tablets: Try go2rtc direct MP4, then HLS via HA API
    if (isDashieTablet && wantsAutoProtocol) {
      // First check if stream exists in go2rtc
      const streamExists = await this.streamExistsInGo2rtc(go2rtcUrl, streamName);

      if (streamExists) {
        // Use fMP4 streaming - stream is registered in go2rtc
        const mp4Url = `${go2rtcUrl}/api/stream.mp4?src=${streamName}`;
        console.log('[Dashie Camera Card] Using MP4 streaming for tablet (go2rtc):', mp4Url);
        return { url: mp4Url, playerType: 'mp4' };
      }

      // Stream not in go2rtc - fall back to HA's camera stream API (HLS)
      // This works because HA's stream component creates HLS from the camera's RTSP source
      if (this.config.entity) {
        console.log('[Dashie Camera Card] Stream not in go2rtc, falling back to HA HLS for tablet');
        return await this.getHaCameraStream();
      }
    }

    // Allow explicit MSE/MP4 protocol override (works on any platform)
    // This uses go2rtc's fragmented MP4 endpoint which plays in <video> tag
    if (this.config.protocol === 'mse') {
      const mp4Url = `${go2rtcUrl}/api/stream.mp4?src=${streamName}`;
      console.log('[Dashie Camera Card] Using MP4 streaming (explicitly requested):', mp4Url);
      return { url: mp4Url, playerType: 'mp4' };
    }

    // Native RTSP for Dashie WebView (only if explicitly requested via protocol: 'rtsp')
    // This uses ExoPlayer overlays - more complex but potentially lower latency
    const nativeRtspSupported = NativeRtspPlayer.isSupported();
    if (isDashieTablet && this.config.protocol === 'rtsp' && nativeRtspSupported) {
      const rtspUrl = `rtsp://${new URL(go2rtcUrl).hostname}:8554/${streamName}`;
      console.log('[Dashie Camera Card] Using native RTSP (explicitly requested):', rtspUrl);
      return { url: rtspUrl, playerType: 'native-rtsp' };
    }

    // For browsers, default to WebRTC (fastest for local network)
    // HLS is only used if EXPLICITLY requested via protocol: 'hls'
    const protocol = this.config.protocol === 'hls' ? 'hls' : 'webrtc';

    console.log('[Dashie Camera Card] Selected protocol:', protocol);

    if (protocol === 'hls') {
      return {
        url: `${go2rtcUrl}/api/stream.m3u8?src=${streamName}`,
        playerType: 'hls'
      };
    }

    // WebRTC for browsers (default)
    return {
      url: `${go2rtcUrl}/api/webrtc?src=${streamName}`,
      playerType: 'webrtc'
    };
  }

  /**
   * Query go2rtc API to get the camera's direct RTSP URL.
   * This allows Android to connect directly to the camera, bypassing go2rtc.
   */
  private async getDirectRtspUrl(go2rtcUrl: string, streamName: string): Promise<string | null> {
    try {
      console.log('[Dashie Camera Card] Querying go2rtc for direct RTSP URL...');
      const response = await fetch(`${go2rtcUrl}/api/streams?src=${streamName}`);

      if (!response.ok) {
        console.warn('[Dashie Camera Card] go2rtc streams API returned:', response.status);
        return null;
      }

      const data = await response.json();
      console.log('[Dashie Camera Card] go2rtc stream info:', data);

      // Look for RTSP source in producers
      // Format: { producers: [{ url: "rtsp://..." }] }
      if (data?.producers && Array.isArray(data.producers)) {
        for (const producer of data.producers) {
          if (producer.url && producer.url.startsWith('rtsp://')) {
            console.log('[Dashie Camera Card] Raw RTSP URL from go2rtc:', producer.url);
            // Decode only the password portion, keep username @ encoded
            // URL format: rtsp://user:pass@host:port/path
            const processedUrl = this.decodeRtspCredentials(producer.url);
            console.log('[Dashie Camera Card] Processed RTSP URL:', processedUrl);
            return processedUrl;
          }
        }
      }

      console.log('[Dashie Camera Card] No direct RTSP URL found in go2rtc');
      return null;
    } catch (error) {
      console.warn('[Dashie Camera Card] Failed to query go2rtc:', error);
      return null;
    }
  }

  /**
   * Decode RTSP URL credentials for ExoPlayer compatibility.
   * ExoPlayer requires fully decoded credentials - its URI parser handles
   * multiple @ symbols correctly by using the LAST @ as the authority separator.
   *
   * Example:
   * Input:  rtsp://user%40email.com:pass%21word@host:554/stream
   * Output: rtsp://user@email.com:pass!word@host:554/stream
   */
  private decodeRtspCredentials(url: string): string {
    try {
      // Match rtsp://credentials@host pattern
      // The LAST @ before the host separates credentials from host
      const match = url.match(/^(rtsp:\/\/)(.+)@([^@]+)$/);
      if (!match) {
        // No credentials or can't parse - return as-is
        return url;
      }

      const [, protocol, credentials, hostAndPath] = match;

      // Fully decode the credentials (both username and password)
      // ExoPlayer's URI parser handles multiple @ symbols correctly
      const decodedCredentials = decodeURIComponent(credentials);

      // Reconstruct URL with decoded credentials
      return `${protocol}${decodedCredentials}@${hostAndPath}`;
    } catch (e) {
      console.warn('[Dashie Camera Card] Failed to decode RTSP credentials:', e);
      return url;
    }
  }

  /**
   * Get camera stream URL via HA's WebSocket API.
   * This works through HA's authentication proxy, enabling remote access.
   */
  private async getHaCameraStream(): Promise<{ url: string; playerType: 'hls' }> {
    if (!this.config.entity) {
      throw new Error('Entity required for HA camera stream');
    }

    console.log('[Dashie Camera Card] Requesting HA camera stream for:', this.config.entity);

    try {
      // Use HA's WebSocket API to request a camera stream
      // This returns an HLS URL that works through HA's auth proxy
      const result = await this.hass.callWS<{ url: string }>({
        type: 'camera/stream',
        entity_id: this.config.entity,
        format: 'hls',
      });

      console.log('[Dashie Camera Card] HA camera stream result:', result);

      if (!result?.url) {
        throw new Error('No stream URL returned from HA');
      }

      return {
        url: result.url,
        playerType: 'hls',
      };
    } catch (error) {
      console.error('[Dashie Camera Card] Failed to get HA camera stream:', error);

      // Provide helpful error message
      const errorMsg = error instanceof Error ? error.message : 'Unknown error';
      if (errorMsg.includes('stream component not loaded')) {
        throw new Error('Enable "stream" integration in HA for remote camera access');
      }

      throw new Error(`HA camera stream failed: ${errorMsg}`);
    }
  }

  /**
   * Get the RTSP stream source URL for a camera entity from HA.
   * This uses the same mechanism as the WebRTC component to extract the camera's RTSP URL.
   */
  private async getCameraStreamSource(entityId: string): Promise<string | null> {
    try {
      console.log('[Dashie Camera Card] Getting stream source for entity:', entityId);

      // Try to get stream source via camera/stream WebSocket call
      // This triggers HA to return stream info including the source URL
      const result = await this.hass.callWS<{ url?: string }>({
        type: 'camera/stream',
        entity_id: entityId,
        format: 'hls', // Request HLS to get stream info
      });

      console.log('[Dashie Camera Card] Stream source result:', result);

      // The stream URL might contain the RTSP source or be an HLS endpoint
      // We need to check camera attributes for the actual RTSP source
      const entity = this.hass.states[entityId];
      if (entity?.attributes?.stream_source) {
        console.log('[Dashie Camera Card] Found stream_source in attributes:', entity.attributes.stream_source);
        return entity.attributes.stream_source;
      }

      // Try getting it via the camera proxy stream API path
      // Some cameras expose their RTSP URL in the entity attributes
      if (entity?.attributes?.rtsp_url) {
        console.log('[Dashie Camera Card] Found rtsp_url in attributes:', entity.attributes.rtsp_url);
        return entity.attributes.rtsp_url;
      }

      console.log('[Dashie Camera Card] No direct stream source found in entity attributes');
      return null;
    } catch (error) {
      console.warn('[Dashie Camera Card] Failed to get camera stream source:', error);
      return null;
    }
  }

  /**
   * Check if a stream already exists in go2rtc.
   * This prevents spawning duplicate FFmpeg processes on every page load.
   */
  private async streamExistsInGo2rtc(go2rtcUrl: string, streamName: string): Promise<boolean> {
    try {
      const response = await fetch(`${go2rtcUrl}/api/streams`);
      if (!response.ok) return false;

      const streams = await response.json();
      const exists = streamName in streams;
      console.log('[Dashie Camera Card] Stream exists check:', streamName, exists);
      return exists;
    } catch (error) {
      console.warn('[Dashie Camera Card] Failed to check stream existence:', error);
      return false;
    }
  }

  /**
   * Dynamically create or update a stream in go2rtc via its API.
   * This allows the card to provision transcoded streams on-demand without manual go2rtc.yaml config.
   *
   * IMPORTANT: This method now checks if the stream already exists to prevent
   * spawning duplicate FFmpeg processes, which can crash HA on resource-constrained devices.
   *
   * @param go2rtcUrl - Base URL of go2rtc server (e.g., http://192.168.1.1:1984)
   * @param streamName - Name for the stream (e.g., camera.family_room_transcoded)
   * @param sourceUrl - Source URL (RTSP, ffmpeg:, etc.)
   * @returns true if stream exists or was created successfully
   */
  private async provisionGo2rtcStream(
    go2rtcUrl: string,
    streamName: string,
    sourceUrl: string
  ): Promise<boolean> {
    try {
      // CRITICAL: Check if stream already exists to prevent duplicate FFmpeg processes
      // FFmpeg transcoding is CPU-intensive; spawning multiple instances crashes HA on Pi 5
      const alreadyExists = await this.streamExistsInGo2rtc(go2rtcUrl, streamName);
      if (alreadyExists) {
        console.log('[Dashie Camera Card] Stream already exists, reusing:', streamName);
        return true;
      }

      console.log('[Dashie Camera Card] Provisioning new go2rtc stream:', { streamName, sourceUrl });

      // Use PUT to create/update the stream
      // API: PUT /api/streams?src=<source_url>&name=<stream_name>
      const apiUrl = `${go2rtcUrl}/api/streams?name=${encodeURIComponent(streamName)}&src=${encodeURIComponent(sourceUrl)}`;

      const response = await fetch(apiUrl, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (!response.ok) {
        console.error('[Dashie Camera Card] go2rtc API error:', response.status, await response.text());
        return false;
      }

      console.log('[Dashie Camera Card] Stream provisioned successfully:', streamName);
      return true;
    } catch (error) {
      console.error('[Dashie Camera Card] Failed to provision go2rtc stream:', error);
      return false;
    }
  }

  /**
   * Build an FFmpeg transcoding source URL for go2rtc.
   * This creates a source string that tells go2rtc to transcode the input stream.
   *
   * Uses LOCAL stream reference within go2rtc (not external RTSP URL).
   * Format: ffmpeg:<stream_name>#video=h264#width=<w>#height=<h>
   *
   * This requires the base stream to already exist in go2rtc.yaml with a valid
   * RTSP source. The transcoding layer references the existing stream by name.
   *
   * @param inputStreamName - Name of the source stream (camera entity ID)
   * @param resolution - Target resolution ('720p', '480p', etc.)
   * @param profile - H.264 profile ('baseline', 'main', 'high')
   */
  private buildTranscodeSource(
    inputStreamName: string,
    resolution: string = '720p',
    profile: string = 'baseline'
  ): string {
    // Resolution mapping
    const resolutions: Record<string, { width: number; height: number }> = {
      '1080p': { width: 1920, height: 1080 },
      '720p': { width: 1280, height: 720 },
      '480p': { width: 854, height: 480 },
      '360p': { width: 640, height: 360 },
    };

    const res = resolutions[resolution] || resolutions['720p'];

    // Build FFmpeg source URL for go2rtc using LOCAL stream reference
    // This references the stream by name within the same go2rtc instance
    // The base stream must exist in go2rtc with a valid RTSP source
    // Format: ffmpeg:<stream_name>#video=h264#width=<w>#height=<h>
    return `ffmpeg:${inputStreamName}#video=h264#width=${res.width}#height=${res.height}`;
  }

  /**
   * Provision a transcoded stream for tablet compatibility.
   * Creates both the source stream and a transcoded variant in go2rtc.
   *
   * @returns The stream name to use for playback
   */
  private async provisionTranscodedStream(
    go2rtcUrl: string,
    entityId: string,
    rtspSourceUrl: string
  ): Promise<string> {
    const baseStreamName = entityId.replace('camera.', '');
    const sourceStreamName = `${baseStreamName}_source`;
    const transcodedStreamName = `${baseStreamName}_tablet`;

    // Get transcode settings from config
    const resolution = this.config.transcode?.resolution || '720p';
    const profile = this.config.transcode?.profile || 'baseline';

    console.log('[Dashie Camera Card] Provisioning transcoded stream:', {
      sourceStreamName,
      transcodedStreamName,
      resolution,
      profile,
    });

    // Step 1: Create the source stream with the RTSP URL
    const sourceCreated = await this.provisionGo2rtcStream(
      go2rtcUrl,
      sourceStreamName,
      rtspSourceUrl
    );

    if (!sourceCreated) {
      throw new Error('Failed to create source stream in go2rtc');
    }

    // Step 2: Create the transcoded stream referencing the source
    const transcodeSource = this.buildTranscodeSource(sourceStreamName, resolution, profile);
    const transcodedCreated = await this.provisionGo2rtcStream(
      go2rtcUrl,
      transcodedStreamName,
      transcodeSource
    );

    if (!transcodedCreated) {
      // Fall back to source stream if transcoding setup fails
      console.warn('[Dashie Camera Card] Failed to create transcoded stream, using source');
      return sourceStreamName;
    }

    return transcodedStreamName;
  }

  /**
   * Build a camgrid composite grid FFmpeg exec command.
   * Creates a single stream combining multiple cameras into a grid layout.
   *
   * @param cameras - Array of camera entity IDs
   * @param grid - Grid layout ('2x1', '2x2', etc.)
   * @param fps - Frames per second (default: 10)
   * @param quality - CRF quality value (default: 30)
   * @returns The exec:ffmpeg command string for go2rtc
   */
  private buildBirdseyeCommand(
    cameras: string[],
    grid: string = 'auto',
    fps: number = 10,
    quality: number = 30
  ): string {
    const numCameras = cameras.length;

    // Auto-detect grid if not specified
    let cols: number, rows: number;
    if (grid === 'auto') {
      if (numCameras <= 2) {
        cols = numCameras;
        rows = 1;
      } else if (numCameras <= 4) {
        cols = 2;
        rows = 2;
      } else if (numCameras <= 6) {
        cols = 3;
        rows = 2;
      } else {
        cols = 3;
        rows = Math.ceil(numCameras / 3);
      }
    } else {
      const [c, r] = grid.split('x').map(Number);
      cols = c;
      rows = r;
    }

    // Calculate cell dimensions for 480p-ish output
    // Total output: 854 x 480 (or proportional)
    const cellWidth = Math.floor(854 / cols);
    const cellHeight = Math.floor(480 / rows);
    const gopSize = fps * 2;

    // Build inputs and filter_complex
    const inputs: string[] = [];
    const scaleFilters: string[] = [];
    const labels: string[] = [];

    cameras.forEach((cam, i) => {
      inputs.push(`-thread_queue_size 64 -rtsp_transport tcp -i rtsp://127.0.0.1:8554/${cam}`);
      scaleFilters.push(`[${i}:v]fps=${fps},scale=${cellWidth}:${cellHeight},setpts=PTS-STARTPTS[v${i}]`);
      labels.push(`v${i}`);
    });

    // Pad with black if fewer cameras than grid slots
    const totalSlots = cols * rows;
    for (let i = cameras.length; i < totalSlots; i++) {
      // Add black filler - use nullsrc for empty slots
      scaleFilters.push(`nullsrc=s=${cellWidth}x${cellHeight}:d=1,loop=-1:1[v${i}]`);
      labels.push(`v${i}`);
    }

    // Build stack filters
    let stackFilters: string[] = [];

    if (rows === 1) {
      // Single row - just hstack
      const hstackInputs = labels.slice(0, cols).map(l => `[${l}]`).join('');
      stackFilters.push(`${hstackInputs}hstack=inputs=${cols}[v]`);
    } else {
      // Multiple rows - hstack each row, then vstack
      const rowOutputs: string[] = [];
      for (let r = 0; r < rows; r++) {
        const rowLabels = labels.slice(r * cols, (r + 1) * cols);
        const rowInputs = rowLabels.map(l => `[${l}]`).join('');
        const rowOutput = `row${r}`;
        stackFilters.push(`${rowInputs}hstack=inputs=${cols}[${rowOutput}]`);
        rowOutputs.push(rowOutput);
      }
      // vstack all rows
      const vstackInputs = rowOutputs.map(r => `[${r}]`).join('');
      stackFilters.push(`${vstackInputs}vstack=inputs=${rows}[v]`);
    }

    const filterComplex = [...scaleFilters, ...stackFilters].join(';');

    // Build full command
    const command = [
      'exec:ffmpeg -hide_banner -fflags nobuffer -flags low_delay',
      inputs.join(' '),
      `-filter_complex '${filterComplex}'`,
      "-map '[v]' -an",
      `-c:v libx264 -preset superfast -tune zerolatency -crf ${quality} -profile:v baseline`,
      `-r ${fps} -g ${gopSize} -f mpegts pipe:1`
    ].join(' ');

    console.log('[Dashie Camera Card] Built camgrid command:', command);
    return command;
  }

  /**
   * Provision a CamGrid composite stream via HA service.
   * Calls dashie.provision_camgrid which runs on localhost (trusted producer).
   *
   * @returns The stream name to use for playback
   */
  private async provisionCamGridStream(go2rtcUrl: string): Promise<string> {
    const cameras = this.config.camgrid!.cameras!;
    const grid = this.config.camgrid?.grid || 'auto';
    const fps = this.config.camgrid?.fps || 10;
    const quality = this.config.camgrid?.quality || 30;

    console.log('[Dashie Camera Card] Provisioning CamGrid stream via HA service:', {
      cameras,
      grid,
      fps,
      quality,
    });

    try {
      // Call HA service - runs from localhost so it's a trusted producer
      const result = await this.hass.callService('dashie', 'provision_camgrid', {
        cameras,
        grid,
        fps,
        quality,
        go2rtc_url: go2rtcUrl,
      }, undefined, true, true) as any;

      console.log('[Dashie Camera Card] HA service result:', result);

      if (result?.response?.success) {
        const streamName = result.response.stream_name;
        console.log('[Dashie Camera Card] CamGrid stream provisioned:', streamName);
        return streamName;
      } else {
        const error = result?.response?.error || 'Unknown error';
        throw new Error(`HA service failed: ${error}`);
      }
    } catch (error: any) {
      console.error('[Dashie Camera Card] Failed to provision CamGrid via HA:', error);

      // Provide helpful error message
      if (error.message?.includes('Service dashie.provision_camgrid not found')) {
        throw new Error(
          'Dashie integration not installed or outdated. ' +
          'Install/update the Dashie integration to use CamGrid.'
        );
      }

      throw new Error(`CamGrid provisioning failed: ${error.message}`);
    }
  }

  private async createPlayer(url: string, playerType: PlayerType): Promise<void> {
    // Destroy existing player
    this.destroyPlayer();

    const container = this.shadowRoot?.querySelector('.video-container') as HTMLElement;
    if (!container) {
      throw new Error('Video container not found');
    }

    console.log('[Dashie Camera Card] Creating player:', playerType);

    switch (playerType) {
      case 'mp4':
        // MP4 via go2rtc stream.mp4 endpoint (recommended for tablets)
        // Plays fragmented MP4 directly in <video> tag
        this.player = new MP4Player(container);
        break;
      case 'mse':
        // MSE via go2rtc WebSocket (alternative, more complex)
        this.player = new MSEPlayer(container);
        break;
      case 'native-rtsp':
        // Native RTSP via Android's ExoPlayer (Dashie WebView only)
        // Only used if explicitly requested via protocol: 'rtsp'
        this.player = new NativeRtspPlayer(container);
        break;
      case 'hls':
        // HLS via HLS.js (browser fallback)
        this.player = new HLSPlayer(container);
        break;
      case 'webrtc':
      default:
        // WebRTC for browsers (go2rtc signaling)
        this.player = new WebRTCPlayer(container);
        break;
    }

    // Add error listener
    container.addEventListener('player-error', this.handlePlayerError.bind(this));

    // Load stream
    await this.player.load(url);
  }

  private destroyPlayer(): void {
    if (this.player) {
      this.player.destroy();
      this.player = null;
    }
  }

  private handlePlayerError(event: CustomEvent): void {
    console.error('[Dashie Camera Card] Player error:', event.detail);
    this.error = event.detail.message || 'Playback error';
  }

  private handleTap(): void {
    const action = this.config.tap_action || 'maximize';

    console.log('[Dashie Camera Card] Tap action:', action);

    switch (action) {
      case 'maximize':
        this.openMaximized();
        break;
      case 'fullscreen':
        this.enterFullscreen();
        break;
      case 'more-info':
        this.showMoreInfo();
        break;
      case 'none':
        break;
    }
  }

  private openMaximized(): void {
    this.maximized = true;
  }

  private closeMaximized(): void {
    this.maximized = false;
  }

  private enterFullscreen(): void {
    const container = this.shadowRoot?.querySelector('.video-container');
    if (container && 'requestFullscreen' in container) {
      (container as HTMLElement).requestFullscreen();
    }
  }

  private showMoreInfo(): void {
    if (!this.config.entity) {
      console.warn('[Dashie Camera Card] Cannot show more-info: no entity configured');
      return;
    }
    const event = new CustomEvent('hass-more-info', {
      bubbles: true,
      composed: true,
      detail: { entityId: this.config.entity },
    });
    this.dispatchEvent(event);
  }

  render() {
    return html`
      ${this.config.title
        ? html`
            <div class="card-header">
              <div class="card-title">${this.config.title}</div>
              ${this.config.show_debug
                ? html`<div class="platform-badge">${this.platform}</div>`
                : ''}
            </div>
          `
        : ''}

      <div class="video-container" @click=${this.handleTap}>
        ${this.loading
          ? html`
              <div class="loading-overlay">
                <div class="spinner"></div>
                <div class="error-text">Loading stream...</div>
              </div>
            `
          : ''}
        ${this.error
          ? html`
              <div class="error-overlay">
                <div class="error-text">${this.error}</div>
              </div>
            `
          : ''}
      </div>

      ${this.maximized ? this.renderMaximized() : ''}
    `;
  }

  private renderMaximized() {
    return html`
      <div class="maximize-overlay">
        <div class="maximize-header">
          <div class="maximize-title">${this.config.title || 'Camera'}</div>
          <button class="close-button" @click=${this.closeMaximized}>✕</button>
        </div>
        <div class="maximize-video">
          <!-- Video will be re-rendered here -->
          <p style="color: white;">Maximized view (re-attach player here)</p>
        </div>
      </div>
    `;
  }

  static getConfigElement() {
    // Return card editor (optional)
    return document.createElement('dashie-camera-card-editor');
  }

  static getStubConfig() {
    return {
      type: 'custom:dashie-camera',
      entity: 'camera.example',
      title: 'Camera',
    };
  }

  getCardSize() {
    return 3;
  }
}

// Explicitly define the custom element with error handling
console.log('[Dashie Camera Card] Registering custom element...');
try {
  if (!customElements.get('dashie-camera-card')) {
    customElements.define('dashie-camera-card', DashieCameraCard);
    console.log('[Dashie Camera Card] Element defined successfully');

    // Verify registration worked
    const registered = customElements.get('dashie-camera-card');
    console.log('[Dashie Camera Card] Verification:', registered ? 'FOUND' : 'NOT FOUND');
  } else {
    console.log('[Dashie Camera Card] Element already registered');
  }
} catch (error) {
  console.error('[Dashie Camera Card] Failed to define element:', error);
}

// Register the card in HA's card picker
(window as any).customCards = (window as any).customCards || [];
(window as any).customCards.push({
  type: 'dashie-camera-card',
  name: 'Dashie Camera',
  description: 'Adaptive camera card with platform-aware streaming',
  preview: false,
});

console.log('[Dashie Camera Card] Card registration complete');
