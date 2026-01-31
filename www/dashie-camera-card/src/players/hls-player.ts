/**
 * HLS Player
 * Uses native HLS support (iOS, Android) or HLS.js (browsers)
 */

import type { IPlayer } from '../types';

export class HLSPlayer implements IPlayer {
  private video: HTMLVideoElement;
  private hls: any | null = null;
  private url: string | null = null;

  constructor(container: HTMLElement) {
    this.video = document.createElement('video');
    this.video.autoplay = true;
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.style.width = '100%';
    this.video.style.height = 'auto';
    this.video.style.display = 'block';
    this.video.style.background = '#000';

    container.appendChild(this.video);

    // Add error handler
    this.video.addEventListener('error', (e) => {
      console.error('[HLS Player] Video error:', e);
      this.handleError(e);
    });
  }

  async load(url: string): Promise<void> {
    this.url = url;
    console.log('[HLS Player] Loading stream:', url);

    // Try native HLS support first (iOS, Android)
    if (this.supportsNativeHLS()) {
      console.log('[HLS Player] Using native HLS support');
      this.video.src = url;
      await this.play();
      return;
    }

    // Try HLS.js
    if (window.Hls && window.Hls.isSupported()) {
      console.log('[HLS Player] Using HLS.js');
      this.loadWithHlsJs(url);
      return;
    }

    // Fallback: try direct playback anyway
    console.warn('[HLS Player] No HLS support detected, trying direct playback');
    this.video.src = url;
    await this.play();
  }

  private supportsNativeHLS(): boolean {
    return this.video.canPlayType('application/vnd.apple.mpegurl') !== '';
  }

  private loadWithHlsJs(url: string): void {
    if (this.hls) {
      this.hls.destroy();
    }

    this.hls = new window.Hls({
      enableWorker: true,
      lowLatencyMode: true,
      backBufferLength: 90,
    });

    this.hls.loadSource(url);
    this.hls.attachMedia(this.video);

    this.hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
      console.log('[HLS Player] Manifest parsed, starting playback');
      this.play().catch((e) => {
        console.error('[HLS Player] Autoplay failed:', e);
      });
    });

    this.hls.on(window.Hls.Events.ERROR, (event: string, data: any) => {
      console.error('[HLS Player] HLS.js error:', data);

      if (data.fatal) {
        switch (data.type) {
          case window.Hls.ErrorTypes.NETWORK_ERROR:
            console.error('[HLS Player] Fatal network error, trying to recover');
            this.hls.startLoad();
            break;
          case window.Hls.ErrorTypes.MEDIA_ERROR:
            console.error('[HLS Player] Fatal media error, trying to recover');
            this.hls.recoverMediaError();
            break;
          default:
            console.error('[HLS Player] Unrecoverable error, destroying HLS instance');
            this.destroy();
            break;
        }
      }
    });
  }

  async play(): Promise<void> {
    try {
      await this.video.play();
      console.log('[HLS Player] Playback started');
    } catch (error) {
      console.error('[HLS Player] Play failed:', error);
      throw error;
    }
  }

  pause(): void {
    this.video.pause();
  }

  stop(): void {
    this.video.pause();
    this.video.currentTime = 0;
  }

  destroy(): void {
    console.log('[HLS Player] Destroying player');

    if (this.hls) {
      this.hls.destroy();
      this.hls = null;
    }

    this.video.pause();
    this.video.src = '';
    this.video.load();

    if (this.video.parentNode) {
      this.video.parentNode.removeChild(this.video);
    }
  }

  getElement(): HTMLElement {
    return this.video;
  }

  /**
   * Reload the current stream
   */
  async reload(): Promise<void> {
    if (!this.url) {
      console.warn('[HLS Player] No URL to reload');
      return;
    }

    console.log('[HLS Player] Reloading stream');
    await this.load(this.url);
  }

  /**
   * Handle video errors
   */
  private handleError(event: Event): void {
    const video = event.target as HTMLVideoElement;
    const error = video.error;

    if (!error) return;

    let errorMessage = 'Unknown error';
    switch (error.code) {
      case MediaError.MEDIA_ERR_ABORTED:
        errorMessage = 'Playback aborted';
        break;
      case MediaError.MEDIA_ERR_NETWORK:
        errorMessage = 'Network error';
        break;
      case MediaError.MEDIA_ERR_DECODE:
        errorMessage = 'Decoding error (codec not supported?)';
        break;
      case MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED:
        errorMessage = 'Source not supported';
        break;
    }

    console.error('[HLS Player] Media error:', errorMessage, error);

    // Emit custom event for card to handle
    this.video.dispatchEvent(
      new CustomEvent('player-error', {
        bubbles: true,
        composed: true,
        detail: {
          code: error.code,
          message: errorMessage,
        },
      })
    );
  }

  /**
   * Get current playback state
   */
  getState(): {
    playing: boolean;
    currentTime: number;
    duration: number;
    buffered: number;
  } {
    const buffered =
      this.video.buffered.length > 0
        ? this.video.buffered.end(this.video.buffered.length - 1)
        : 0;

    return {
      playing: !this.video.paused,
      currentTime: this.video.currentTime,
      duration: this.video.duration,
      buffered,
    };
  }
}
