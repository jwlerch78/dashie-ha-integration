/**
 * Native RTSP Player
 * Uses Android's native video decoding via JavaScript bridge.
 * Only available in Dashie Kiosk WebView.
 *
 * The native player creates an overlay ABOVE the WebView, positioned
 * to align with this card's position on screen.
 */

import type { IPlayer } from '../types';

export class NativeRtspPlayer implements IPlayer {
  private streamId: string;
  private placeholder: HTMLDivElement;
  private resizeObserver: ResizeObserver | null = null;
  private isActive = false;

  constructor(container: HTMLElement) {
    // Generate unique stream ID based on container
    this.streamId = `cam_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

    // Create a placeholder div that the native overlay will cover
    this.placeholder = document.createElement('div');
    this.placeholder.style.width = '100%';
    this.placeholder.style.height = '100%';
    this.placeholder.style.minHeight = '200px';
    this.placeholder.style.background = '#000';
    this.placeholder.style.display = 'flex';
    this.placeholder.style.alignItems = 'center';
    this.placeholder.style.justifyContent = 'center';
    this.placeholder.style.color = '#666';
    this.placeholder.style.fontSize = '12px';
    this.placeholder.innerHTML = '<span>Loading native stream...</span>';

    container.appendChild(this.placeholder);

    console.log('[Native RTSP] Created player with ID:', this.streamId);
  }

  /**
   * Check if native RTSP playback is available.
   */
  static isSupported(): boolean {
    const supported = window.dashieDevice?.isNativeRtspSupported?.() === true;
    console.log('[Native RTSP] isSupported:', supported);
    return supported;
  }

  async load(url: string): Promise<void> {
    console.log('[Native RTSP] Loading stream:', url);

    if (!window.dashieDevice) {
      throw new Error('Native RTSP not available - not running in Dashie WebView');
    }

    // Get the position of the placeholder in screen coordinates
    const rect = this.placeholder.getBoundingClientRect();

    console.log('[Native RTSP] Container position:', {
      x: Math.round(rect.left),
      y: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    });

    // Start the native RTSP stream
    window.dashieDevice.startRtspStream?.(
      this.streamId,
      url,
      Math.round(rect.left),
      Math.round(rect.top),
      Math.round(rect.width),
      Math.round(rect.height)
    );

    this.isActive = true;

    // Update placeholder to show stream is active
    this.placeholder.innerHTML = '';
    this.placeholder.style.background = 'transparent';

    // Set up resize observer to update overlay position
    this.setupResizeObserver();
  }

  private setupResizeObserver(): void {
    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
    }

    this.resizeObserver = new ResizeObserver(() => {
      this.updatePosition();
    });

    this.resizeObserver.observe(this.placeholder);

    // Also listen for scroll events on parent
    window.addEventListener('scroll', this.handleScroll, true);
  }

  private handleScroll = (): void => {
    this.updatePosition();
  };

  private updatePosition(): void {
    if (!this.isActive || !window.dashieDevice) return;

    const rect = this.placeholder.getBoundingClientRect();

    window.dashieDevice.updateRtspStreamPosition?.(
      this.streamId,
      Math.round(rect.left),
      Math.round(rect.top),
      Math.round(rect.width),
      Math.round(rect.height)
    );
  }

  async play(): Promise<void> {
    // Native player auto-plays on load
    console.log('[Native RTSP] Play called (native player auto-plays)');
  }

  pause(): void {
    // Native RTSP doesn't support pause - it's a live stream
    console.log('[Native RTSP] Pause not supported for RTSP streams');
  }

  stop(): void {
    console.log('[Native RTSP] Stopping stream:', this.streamId);
    this.cleanup();
  }

  destroy(): void {
    console.log('[Native RTSP] Destroying player:', this.streamId);
    this.cleanup();

    if (this.placeholder.parentNode) {
      this.placeholder.parentNode.removeChild(this.placeholder);
    }
  }

  private cleanup(): void {
    this.isActive = false;

    if (this.resizeObserver) {
      this.resizeObserver.disconnect();
      this.resizeObserver = null;
    }

    window.removeEventListener('scroll', this.handleScroll, true);

    window.dashieDevice?.stopRtspStream?.(this.streamId);
  }

  getElement(): HTMLElement {
    return this.placeholder;
  }

  getConnectionState(): string {
    if (!window.dashieDevice?.isRtspStreamPlaying) return 'disconnected';
    return window.dashieDevice.isRtspStreamPlaying(this.streamId) ? 'connected' : 'disconnected';
  }

  /**
   * Hide the native overlay (e.g., when showing a modal).
   */
  hide(): void {
    window.dashieDevice?.hideRtspOverlays();
  }

  /**
   * Show the native overlay.
   */
  show(): void {
    window.dashieDevice?.showRtspOverlays();
  }
}
