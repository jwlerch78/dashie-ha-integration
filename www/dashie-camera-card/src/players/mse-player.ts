/**
 * MSE Player
 * Uses go2rtc's WebSocket endpoint to stream H.264 directly via MediaSource Extensions
 * No WebRTC handshaking - just a simple WebSocket connection
 */

import type { IPlayer } from '../types';

export class MSEPlayer implements IPlayer {
  private video: HTMLVideoElement;
  private container: HTMLElement;
  private ws: WebSocket | null = null;
  private mediaSource: MediaSource | null = null;
  private sourceBuffer: SourceBuffer | null = null;
  private bufferQueue: ArrayBuffer[] = [];
  private isBufferUpdating = false;

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

    container.appendChild(this.video);
  }

  async load(url: string): Promise<void> {
    console.log('[MSE Player] Loading stream:', url);
    console.log('[MSE Player] MSE supported:', 'MediaSource' in window);

    if (!('MediaSource' in window)) {
      throw new Error('MediaSource Extensions not supported on this device');
    }

    // Convert HTTP URL to WebSocket URL for go2rtc MSE endpoint
    // Input: http://host:1985/api/stream.mp4?src=camera
    // Output: ws://host:1985/api/ws?src=camera
    const wsUrl = this.buildWebSocketUrl(url);
    console.log('[MSE Player] WebSocket URL:', wsUrl);

    await this.connectWebSocket(wsUrl);
  }

  private buildWebSocketUrl(httpUrl: string): string {
    // Parse the HTTP URL
    const urlObj = new URL(httpUrl);

    // Convert protocol
    const wsProtocol = urlObj.protocol === 'https:' ? 'wss:' : 'ws:';

    // Build WebSocket URL for go2rtc MSE endpoint
    // go2rtc expects: /api/ws?src=camera_name
    const streamName = urlObj.searchParams.get('src') || 'camera';

    return `${wsProtocol}//${urlObj.host}/api/ws?src=${streamName}`;
  }

  private async connectWebSocket(wsUrl: string): Promise<void> {
    return new Promise((resolve, reject) => {
      // Create MediaSource
      this.mediaSource = new MediaSource();
      this.video.src = URL.createObjectURL(this.mediaSource);

      this.mediaSource.addEventListener('sourceopen', () => {
        console.log('[MSE Player] MediaSource opened');

        // Connect WebSocket after MediaSource is ready
        this.ws = new WebSocket(wsUrl);
        this.ws.binaryType = 'arraybuffer';

        this.ws.onopen = () => {
          console.log('[MSE Player] WebSocket connected');

          // Send initial message to request video stream
          // go2rtc expects specific format for MSE mode
          this.ws?.send(JSON.stringify({
            type: 'mse',
          }));
        };

        this.ws.onmessage = (event) => {
          this.handleMessage(event);
        };

        this.ws.onerror = (error) => {
          console.error('[MSE Player] WebSocket error:', error);
          this.emitError('WebSocket connection error');
          reject(new Error('WebSocket connection failed'));
        };

        this.ws.onclose = (event) => {
          console.log('[MSE Player] WebSocket closed:', event.code, event.reason);
          if (event.code !== 1000) {
            this.emitError('Stream connection closed unexpectedly');
          }
        };

        resolve();
      });

      this.mediaSource.addEventListener('error', (e) => {
        console.error('[MSE Player] MediaSource error:', e);
        reject(new Error('MediaSource error'));
      });
    });
  }

  private handleMessage(event: MessageEvent): void {
    const data = event.data;

    // Handle text messages (codec info, etc.)
    if (typeof data === 'string') {
      console.log('[MSE Player] Received message:', data);
      try {
        const msg = JSON.parse(data);
        if (msg.type === 'mse' && msg.codecs) {
          console.log('[MSE Player] Codecs:', msg.codecs);
          this.initSourceBuffer(msg.codecs);
        }
      } catch (e) {
        // Not JSON, ignore
      }
      return;
    }

    // Handle binary data (video frames)
    if (data instanceof ArrayBuffer) {
      this.appendBuffer(data);
    }
  }

  private initSourceBuffer(codecs: string): void {
    if (!this.mediaSource || this.sourceBuffer) {
      return;
    }

    console.log('[MSE Player] Initializing SourceBuffer with codecs:', codecs);

    try {
      // go2rtc sends codec string like "avc1.640028" or "avc1.640028,mp4a.40.2"
      const mimeType = `video/mp4; codecs="${codecs}"`;
      console.log('[MSE Player] MIME type:', mimeType);

      if (!MediaSource.isTypeSupported(mimeType)) {
        console.error('[MSE Player] MIME type not supported:', mimeType);
        this.emitError(`Codec not supported: ${codecs}`);
        return;
      }

      this.sourceBuffer = this.mediaSource.addSourceBuffer(mimeType);
      this.sourceBuffer.mode = 'segments';

      this.sourceBuffer.addEventListener('updateend', () => {
        this.isBufferUpdating = false;
        this.processBufferQueue();
      });

      this.sourceBuffer.addEventListener('error', (e) => {
        console.error('[MSE Player] SourceBuffer error:', e);
      });

      // Process any queued data
      this.processBufferQueue();
    } catch (error) {
      console.error('[MSE Player] Failed to create SourceBuffer:', error);
      this.emitError('Failed to initialize video decoder');
    }
  }

  private appendBuffer(data: ArrayBuffer): void {
    this.bufferQueue.push(data);
    this.processBufferQueue();
  }

  private processBufferQueue(): void {
    if (!this.sourceBuffer || this.isBufferUpdating || this.bufferQueue.length === 0) {
      return;
    }

    if (this.mediaSource?.readyState !== 'open') {
      return;
    }

    try {
      const data = this.bufferQueue.shift();
      if (data) {
        this.isBufferUpdating = true;
        this.sourceBuffer.appendBuffer(data);
      }
    } catch (error) {
      console.error('[MSE Player] Error appending buffer:', error);
      // If buffer is full, remove old data
      if (error instanceof DOMException && error.name === 'QuotaExceededError') {
        this.trimBuffer();
      }
    }
  }

  private trimBuffer(): void {
    if (!this.sourceBuffer || this.isBufferUpdating) {
      return;
    }

    try {
      const currentTime = this.video.currentTime;
      if (currentTime > 10) {
        this.isBufferUpdating = true;
        this.sourceBuffer.remove(0, currentTime - 5);
      }
    } catch (error) {
      console.error('[MSE Player] Error trimming buffer:', error);
    }
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
      console.log('[MSE Player] Playback started');
    } catch (error) {
      console.error('[MSE Player] Play failed:', error);
      throw error;
    }
  }

  pause(): void {
    this.video.pause();
  }

  stop(): void {
    this.video.pause();
    this.cleanup();
  }

  destroy(): void {
    console.log('[MSE Player] Destroying player');
    this.cleanup();

    if (this.video.parentNode) {
      this.video.parentNode.removeChild(this.video);
    }
  }

  private cleanup(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }

    if (this.sourceBuffer && this.mediaSource?.readyState === 'open') {
      try {
        this.mediaSource.removeSourceBuffer(this.sourceBuffer);
      } catch (e) {
        // Ignore
      }
    }

    if (this.mediaSource && this.mediaSource.readyState === 'open') {
      try {
        this.mediaSource.endOfStream();
      } catch (e) {
        // Ignore
      }
    }

    this.sourceBuffer = null;
    this.mediaSource = null;
    this.bufferQueue = [];
  }

  getElement(): HTMLElement {
    return this.video;
  }

  getConnectionState(): string {
    if (!this.ws) return 'disconnected';
    switch (this.ws.readyState) {
      case WebSocket.CONNECTING:
        return 'connecting';
      case WebSocket.OPEN:
        return 'connected';
      case WebSocket.CLOSING:
        return 'disconnecting';
      case WebSocket.CLOSED:
        return 'disconnected';
      default:
        return 'unknown';
    }
  }
}
