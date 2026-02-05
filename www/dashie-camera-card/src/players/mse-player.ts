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
  private hasInitSegment = false;

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
      const blobUrl = URL.createObjectURL(this.mediaSource);
      this.video.src = blobUrl;
      console.log('[MSE Player] MediaSource blob URL:', blobUrl);

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
        if (msg.type === 'mse') {
          // go2rtc sends codecs in different formats:
          // Option 1: { type: 'mse', codecs: 'avc1.640029' }
          // Option 2: { type: 'mse', value: 'video/mp4; codecs="avc1.640029"' }
          let codecs = msg.codecs;

          if (!codecs && msg.value) {
            // Extract codecs from MIME type string
            const match = msg.value.match(/codecs="([^"]+)"/);
            if (match) {
              codecs = match[1];
            }
          }

          if (codecs) {
            console.log('[MSE Player] Codecs:', codecs);
            this.initSourceBuffer(codecs);
          } else {
            console.error('[MSE Player] No codecs found in message:', msg);
          }
        }
      } catch (e) {
        // Not JSON, ignore
        console.error('[MSE Player] Failed to parse message:', e);
      }
      return;
    }

    // Handle binary data (video frames)
    if (data instanceof ArrayBuffer) {
      console.log('[MSE Player] Binary data received:', data.byteLength, 'bytes');
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
      console.log('[MSE Player] MediaSource.isTypeSupported:', MediaSource.isTypeSupported(mimeType));

      // Check alternative codec profiles that might be better supported
      const alternativeCodecs = [
        'video/mp4; codecs="avc1.42E01E"',  // Baseline Profile Level 3.0
        'video/mp4; codecs="avc1.4D401F"',  // Main Profile Level 3.1
        'video/mp4; codecs="avc1.640028"',  // High Profile Level 4.0
      ];
      console.log('[MSE Player] Alternative codec support:');
      alternativeCodecs.forEach(codec => {
        console.log(`  ${codec}: ${MediaSource.isTypeSupported(codec)}`);
      });

      if (!MediaSource.isTypeSupported(mimeType)) {
        console.error('[MSE Player] MIME type not supported:', mimeType);
        this.emitError(`Codec not supported: ${codecs}`);
        return;
      }

      this.sourceBuffer = this.mediaSource.addSourceBuffer(mimeType);
      this.sourceBuffer.mode = 'segments';

      let playbackStarted = false;

      this.sourceBuffer.addEventListener('updateend', () => {
        console.log('[MSE Player] updateend fired - playbackStarted:', playbackStarted,
                    'hasInitSegment:', this.hasInitSegment, 'video.paused:', this.video.paused,
                    'bufferQueue.length:', this.bufferQueue.length);
        this.isBufferUpdating = false;

        // Only start playback after initialization segment is loaded
        if (!playbackStarted && this.hasInitSegment && this.video.paused) {
          playbackStarted = true;
          console.log('[MSE Player] Initialization segment loaded, starting playback');
          this.video.play().catch(err => {
            console.error('[MSE Player] Autoplay failed:', err);
          });
        } else {
          console.log('[MSE Player] Skipping playback start - playbackStarted:', playbackStarted,
                      'hasInitSegment:', this.hasInitSegment, 'paused:', this.video.paused);
        }

        this.processBufferQueue();
      });

      this.sourceBuffer.addEventListener('error', (e) => {
        console.error('[MSE Player] SourceBuffer error:', e);
        console.error('[MSE Player] SourceBuffer error details - readyState:', this.mediaSource?.readyState,
                      'updating:', this.sourceBuffer?.updating, 'buffered:', this.sourceBuffer?.buffered.length);

        // Log video element state
        console.error('[MSE Player] Video element - readyState:', this.video.readyState,
                      'networkState:', this.video.networkState, 'error:', this.video.error);
      });

      // Process any queued data
      this.processBufferQueue();
    } catch (error) {
      console.error('[MSE Player] Failed to create SourceBuffer:', error);
      this.emitError('Failed to initialize video decoder');
    }
  }

  private appendBuffer(data: ArrayBuffer): void {
    // Detect initialization segment (fMP4 init is typically >10KB and contains ftyp+moov)
    // Media segments are smaller and contain moof+mdat
    const isLikelyInitSegment = data.byteLength > 10000;

    if (isLikelyInitSegment && !this.hasInitSegment) {
      console.log('[MSE Player] Detected initialization segment:', data.byteLength, 'bytes');
      this.hasInitSegment = true;
      // Insert init segment at the FRONT of the queue (it must be first)
      this.bufferQueue.unshift(data);
    } else {
      // Media segments go at the end
      this.bufferQueue.push(data);
    }

    // IMPORTANT: Only process queue if we have the init segment
    // Appending media segments before init segment causes MediaSource to close
    if (this.hasInitSegment) {
      this.processBufferQueue();
    } else {
      console.log('[MSE Player] Queuing data, waiting for init segment. Queue size:', this.bufferQueue.length);
    }
  }

  private processBufferQueue(): void {
    if (!this.sourceBuffer || this.isBufferUpdating || this.bufferQueue.length === 0) {
      console.log('[MSE Player] processBufferQueue - sourceBuffer:', !!this.sourceBuffer,
                  'isBufferUpdating:', this.isBufferUpdating, 'queueLength:', this.bufferQueue.length);
      return;
    }

    if (this.mediaSource?.readyState !== 'open') {
      console.log('[MSE Player] MediaSource not open, readyState:', this.mediaSource?.readyState);
      return;
    }

    try {
      const data = this.bufferQueue.shift();
      if (data) {
        console.log('[MSE Player] Appending buffer:', data.byteLength, 'bytes, queue remaining:', this.bufferQueue.length);
        console.log('[MSE Player] Before append - MediaSource readyState:', this.mediaSource?.readyState,
                    'SourceBuffer updating:', this.sourceBuffer.updating);
        this.isBufferUpdating = true;
        this.sourceBuffer.appendBuffer(data);
        console.log('[MSE Player] appendBuffer() called successfully');
      }
    } catch (error) {
      console.error('[MSE Player] Error appending buffer:', error);
      console.error('[MSE Player] Error details:', {
        name: error instanceof DOMException ? error.name : 'Unknown',
        message: error instanceof Error ? error.message : String(error),
        mediaSourceState: this.mediaSource?.readyState,
        sourceBufferUpdating: this.sourceBuffer?.updating
      });

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
    this.hasInitSegment = false;
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
