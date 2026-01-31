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

@customElement('dashie-camera-card')
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
    if (!config.entity) {
      throw new Error('You need to define an entity');
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

  connectedCallback(): void {
    super.connectedCallback();
    this.platform = PlatformDetector.detectPlatform();
    console.log('[Dashie Camera Card] Platform detected:', this.platform);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.destroyPlayer();
  }

  protected firstUpdated(): void {
    this.initializeStream();
  }

  private async initializeStream(): Promise<void> {
    try {
      this.loading = true;
      this.error = null;

      const streamUrl = await this.getStreamUrl();
      console.log('[Dashie Camera Card] Stream URL:', streamUrl);

      await this.createPlayer(streamUrl);

      this.loading = false;
    } catch (error) {
      console.error('[Dashie Camera Card] Failed to initialize stream:', error);
      this.error = error instanceof Error ? error.message : 'Failed to load stream';
      this.loading = false;
    }
  }

  private async getStreamUrl(): Promise<string> {
    const entity = this.hass.states[this.config.entity];
    if (!entity) {
      throw new Error(`Entity not found: ${this.config.entity}`);
    }

    // Check if it's a Frigate camera
    const frigateUrl = this.config.frigate?.url || entity.attributes.frigate_url;

    // Determine protocol based on platform
    const protocol = this.config.protocol !== 'auto'
      ? this.config.protocol
      : this.platform === 'dashie-tablet'
      ? 'hls'
      : 'webrtc';

    console.log('[Dashie Camera Card] Selected protocol:', protocol);

    // Get stream name (defaults to entity ID without domain)
    const streamName = this.config.stream_name || this.config.entity.replace('camera.', '');

    // Build stream URL
    if (protocol === 'hls') {
      // HLS via go2rtc
      const go2rtcUrl = this.config.go2rtc_url || frigateUrl?.replace(':5000', ':1984') || 'http://localhost:1984';
      return `${go2rtcUrl}/api/stream.m3u8?src=${streamName}`;
    }

    if (protocol === 'webrtc') {
      // WebRTC via go2rtc or Frigate
      const baseUrl = frigateUrl || this.config.go2rtc_url || 'http://localhost:1984';
      return `${baseUrl}/api/webrtc?src=${streamName}`;
    }

    // RTSP (direct)
    const rtspUrl = entity.attributes.rtsp_url;
    if (rtspUrl) {
      return rtspUrl;
    }

    throw new Error('Could not determine stream URL');
  }

  private async createPlayer(url: string): Promise<void> {
    // Destroy existing player
    this.destroyPlayer();

    const container = this.shadowRoot?.querySelector('.video-container') as HTMLElement;
    if (!container) {
      throw new Error('Video container not found');
    }

    // Determine player type
    const protocol = url.includes('.m3u8') ? 'hls' : 'webrtc';

    console.log('[Dashie Camera Card] Creating player:', protocol);

    if (protocol === 'hls') {
      this.player = new HLSPlayer(container);
    } else {
      this.player = new WebRTCPlayer(container, this.hass);
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
        this.requestFullscreen();
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

  private requestFullscreen(): void {
    const container = this.shadowRoot?.querySelector('.video-container');
    if (container && 'requestFullscreen' in container) {
      (container as any).requestFullscreen();
    }
  }

  private showMoreInfo(): void {
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

// Register the card
(window as any).customCards = (window as any).customCards || [];
(window as any).customCards.push({
  type: 'dashie-camera',
  name: 'Dashie Camera',
  description: 'Adaptive camera card with platform-aware streaming',
  preview: false,
});

console.log('[Dashie Camera Card] Custom card registered');
