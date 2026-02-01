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
} from './types';
import { PlatformDetector } from './platform-detector';
import { HLSPlayer } from './players/hls-player';
import { WebRTCPlayer } from './players/webrtc-player';
import { NativeRtspPlayer } from './players/native-rtsp-player';

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
    // Either entity or stream_name is required
    if (!config.entity && !config.stream_name) {
      throw new Error('You need to define either an entity or stream_name');
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

  connectedCallback(): void {
    super.connectedCallback();
    this.platform = PlatformDetector.detectPlatform();
    console.log('[Dashie Camera Card] Platform detected:', this.platform);

    // Set up visibility tracking - stop streams when card scrolls out of view
    this.setupVisibilityTracking();

    // Handle page visibility changes (tab switching, etc.)
    document.addEventListener('visibilitychange', this.handlePageVisibility);
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
   */
  private async getStreamConfig(): Promise<{ url: string; playerType: 'native-rtsp' | 'webrtc' | 'hls' }> {
    // Entity is optional if stream_name is provided
    const entity = this.config.entity ? this.hass.states[this.config.entity] : null;

    // If entity is specified but not found, that's an error
    if (this.config.entity && !entity) {
      throw new Error(`Entity not found: ${this.config.entity}`);
    }

    // Check if it's a Frigate camera (only if entity exists)
    const frigateUrl = this.config.frigate?.url || entity?.attributes?.frigate_url;

    // Get stream name (from config, or from entity ID without domain)
    const streamName = this.config.stream_name ||
      (this.config.entity ? this.config.entity.replace('camera.', '') : null);

    if (!streamName) {
      throw new Error('Could not determine stream name');
    }

    // Check if native RTSP is supported (Dashie Kiosk WebView)
    const nativeRtspSupported = NativeRtspPlayer.isSupported();
    console.log('[Dashie Camera Card] Native RTSP supported:', nativeRtspSupported);
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
    const go2rtcUrl = this.config.go2rtc_url || frigateUrl?.replace(':5000', ':1984') || 'http://192.168.86.46:1984';
    console.log('[Dashie Camera Card] Using direct go2rtc:', go2rtcUrl);

    // Native RTSP for Dashie WebView (no WebRTC handshaking overhead)
    // Only use if explicitly supported AND protocol is not forced to something else
    if (nativeRtspSupported && this.config.protocol !== 'webrtc' && this.config.protocol !== 'hls') {
      // Use go2rtc's RTSP output (standard port 8554)
      const rtspUrl = `rtsp://${new URL(go2rtcUrl).hostname}:8554/${streamName}`;
      console.log('[Dashie Camera Card] Using native RTSP:', rtspUrl);
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


  private async createPlayer(url: string, playerType: 'native-rtsp' | 'webrtc' | 'hls'): Promise<void> {
    // Destroy existing player
    this.destroyPlayer();

    const container = this.shadowRoot?.querySelector('.video-container') as HTMLElement;
    if (!container) {
      throw new Error('Video container not found');
    }

    console.log('[Dashie Camera Card] Creating player:', playerType);

    switch (playerType) {
      case 'native-rtsp':
        // Native RTSP via Android's ExoPlayer (Dashie WebView only)
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
