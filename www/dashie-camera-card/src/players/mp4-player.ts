/**
 * MP4 Player
 * Uses go2rtc's stream.mp4 endpoint directly in a <video> tag.
 * This is the simplest approach - no WebSocket, no MSE APIs needed.
 *
 * The stream.mp4 endpoint returns a fragmented MP4 stream via HTTP
 * chunked transfer encoding, which most browsers/WebViews can play natively.
 *
 * Endpoint: http://go2rtc:1984/api/stream.mp4?src={stream_name}
 */

import type { IPlayer } from '../types';

export class MP4Player implements IPlayer {
  private video: HTMLVideoElement;
  private container: HTMLElement;
  private streamUrl: string = '';
  private destroyed: boolean = false;

  constructor(container: HTMLElement) {
    this.container = container;

    this.video = document.createElement('video');
    this.video.autoplay = true;
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.style.width = '100%';
    this.video.style.height = 'auto';
    this.video.style.display = 'block';
    this.video.style.background = '#000';

    // Error handling - ignore errors during cleanup
    this.video.onerror = () => {
      if (this.destroyed) return; // Ignore errors during cleanup
      const error = this.video.error;
      console.error('[MP4 Player] Video error:', error?.code, error?.message);
      this.emitError(`Video error: ${error?.message || 'Unknown error'}`);
    };

    // Log playback events for debugging
    this.video.onloadedmetadata = () => {
      console.log('[MP4 Player] Metadata loaded:', {
        duration: this.video.duration,
        videoWidth: this.video.videoWidth,
        videoHeight: this.video.videoHeight,
      });
    };

    this.video.onplaying = () => {
      console.log('[MP4 Player] Playback started');
    };

    this.video.onwaiting = () => {
      console.log('[MP4 Player] Buffering...');
    };

    this.video.onstalled = () => {
      console.log('[MP4 Player] Stream stalled');
    };

    container.appendChild(this.video);
  }

  async load(url: string): Promise<void> {
    console.log('[MP4 Player] Loading stream:', url);

    this.streamUrl = url;

    // Set the source directly - go2rtc's stream.mp4 endpoint uses
    // HTTP chunked transfer encoding which browsers can play natively
    this.video.src = url;

    // Wait for the video to be ready to play
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error('Timeout waiting for video to load'));
      }, 15000); // 15 second timeout

      const onCanPlay = () => {
        clearTimeout(timeout);
        this.video.removeEventListener('canplay', onCanPlay);
        this.video.removeEventListener('error', onError);
        console.log('[MP4 Player] Video ready to play');
        resolve();
      };

      const onError = () => {
        clearTimeout(timeout);
        this.video.removeEventListener('canplay', onCanPlay);
        this.video.removeEventListener('error', onError);
        const error = this.video.error;
        reject(new Error(`Failed to load video: ${error?.message || 'Unknown error'}`));
      };

      this.video.addEventListener('canplay', onCanPlay);
      this.video.addEventListener('error', onError);

      // Start loading
      this.video.load();
    });
  }

  private emitError(message: string): void {
    this.container.dispatchEvent(
      new CustomEvent('player-error', {
        bubbles: true,
        composed: true,
        detail: { message },
      })
    );
  }

  async play(): Promise<void> {
    try {
      await this.video.play();
      console.log('[MP4 Player] Play called');
    } catch (error) {
      console.error('[MP4 Player] Play failed:', error);
      throw error;
    }
  }

  pause(): void {
    this.video.pause();
  }

  stop(): void {
    this.video.pause();
    // Remove the source to stop buffering - use removeAttribute to avoid empty src error
    this.video.removeAttribute('src');
    this.video.load();
  }

  destroy(): void {
    console.log('[MP4 Player] Destroying player');
    this.destroyed = true; // Mark as destroyed to ignore cleanup errors
    this.video.onerror = null; // Remove error handler
    this.stop();

    if (this.video.parentNode) {
      this.video.parentNode.removeChild(this.video);
    }
  }

  getElement(): HTMLElement {
    return this.video;
  }

  getConnectionState(): string {
    if (!this.video.src) return 'disconnected';
    if (this.video.readyState >= 2) return 'connected';
    return 'connecting';
  }

  /**
   * Check if the browser/WebView supports fMP4 streaming.
   * Most modern browsers and Android WebViews support this.
   */
  static isSupported(): boolean {
    const video = document.createElement('video');
    // Check for MP4 support (fMP4 uses the same container)
    const supported = video.canPlayType('video/mp4; codecs="avc1.42E01E"') !== '';
    console.log('[MP4 Player] isSupported:', supported);
    return supported;
  }
}
