/**
 * WebRTC Player
 * Connects to go2rtc WebRTC endpoint for low-latency streaming
 *
 * Memory leak prevention:
 * - Explicitly stops all MediaStream tracks on cleanup
 * - Removes all event listeners from PeerConnection
 * - Clears video srcObject before removing element
 * - Handles cleanup during ICE gathering
 */

import type { IPlayer } from '../types';

export class WebRTCPlayer implements IPlayer {
  private video: HTMLVideoElement;
  private peerConnection: RTCPeerConnection | null = null;
  private container: HTMLElement;
  private mediaStream: MediaStream | null = null;
  private iceGatheringTimeout: ReturnType<typeof setTimeout> | null = null;
  private isDestroyed = false;

  constructor(container: HTMLElement) {
    this.container = container;

    this.video = document.createElement('video');
    this.video.autoplay = true;
    this.video.muted = true;
    this.video.playsInline = true;
    this.video.style.width = '100%';
    this.video.style.height = '100%';
    this.video.style.minHeight = '200px';
    this.video.style.objectFit = 'contain';
    this.video.style.display = 'block';
    this.video.style.background = '#000';

    container.appendChild(this.video);
  }

  async load(url: string): Promise<void> {
    console.log('[WebRTC Player] Loading stream:', url);
    console.log('[WebRTC Player] RTCPeerConnection supported:', 'RTCPeerConnection' in window);

    if (!('RTCPeerConnection' in window)) {
      throw new Error('WebRTC not supported on this device');
    }

    try {
      await this.connectToGo2RTC(url);
    } catch (error) {
      console.error('[WebRTC Player] Failed to connect:', error);
      throw error;
    }
  }

  private async connectToGo2RTC(url: string): Promise<void> {
    // Create peer connection with STUN server for NAT traversal
    this.peerConnection = new RTCPeerConnection({
      iceServers: [
        { urls: 'stun:stun.l.google.com:19302' },
      ],
    });

    // Handle incoming media tracks
    this.peerConnection.ontrack = (event) => {
      if (this.isDestroyed) return; // Guard against late callbacks
      console.log('[WebRTC Player] Received track:', event.track.kind);
      if (event.streams && event.streams[0]) {
        this.mediaStream = event.streams[0];
        this.video.srcObject = this.mediaStream;
        console.log('[WebRTC Player] Attached stream to video');

        // Explicitly play - some WebViews don't honor autoplay
        this.video.play().catch(err => {
          console.warn('[WebRTC Player] Autoplay blocked:', err.message);
        });
      }
    };

    // Log connection state changes
    this.peerConnection.onconnectionstatechange = () => {
      const state = this.peerConnection?.connectionState;
      console.log('[WebRTC Player] Connection state:', state);

      if (state === 'failed' || state === 'disconnected') {
        this.emitError('WebRTC connection ' + state);
      }
    };

    // Log ICE connection state
    this.peerConnection.oniceconnectionstatechange = () => {
      console.log('[WebRTC Player] ICE state:', this.peerConnection?.iceConnectionState);
    };

    // Add transceivers for receiving video and audio
    this.peerConnection.addTransceiver('video', { direction: 'recvonly' });
    this.peerConnection.addTransceiver('audio', { direction: 'recvonly' });

    // Create SDP offer
    const offer = await this.peerConnection.createOffer();
    await this.peerConnection.setLocalDescription(offer);

    // Wait for ICE gathering to complete (or timeout)
    await this.waitForIceGathering();

    // Get the complete offer with ICE candidates
    const localDescription = this.peerConnection.localDescription;
    if (!localDescription) {
      throw new Error('Failed to create local description');
    }

    console.log('[WebRTC Player] Sending offer to:', url);

    // Send offer to go2rtc and get answer
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/sdp',
      },
      body: localDescription.sdp,
    });

    if (!response.ok) {
      throw new Error(`go2rtc returned ${response.status}: ${response.statusText}`);
    }

    const answerSdp = await response.text();
    console.log('[WebRTC Player] Received answer from go2rtc');

    // Set the remote description (go2rtc's answer)
    const answer = new RTCSessionDescription({
      type: 'answer',
      sdp: answerSdp,
    });

    await this.peerConnection.setRemoteDescription(answer);
    console.log('[WebRTC Player] Remote description set, waiting for media...');
  }

  private waitForIceGathering(): Promise<void> {
    return new Promise((resolve) => {
      if (!this.peerConnection || this.isDestroyed) {
        resolve();
        return;
      }

      // If already complete, resolve immediately
      if (this.peerConnection.iceGatheringState === 'complete') {
        resolve();
        return;
      }

      // Wait for ICE gathering to complete (with timeout)
      this.iceGatheringTimeout = setTimeout(() => {
        console.log('[WebRTC Player] ICE gathering timeout, proceeding...');
        this.iceGatheringTimeout = null;
        resolve();
      }, 2000);

      this.peerConnection.onicegatheringstatechange = () => {
        if (this.peerConnection?.iceGatheringState === 'complete') {
          if (this.iceGatheringTimeout) {
            clearTimeout(this.iceGatheringTimeout);
            this.iceGatheringTimeout = null;
          }
          console.log('[WebRTC Player] ICE gathering complete');
          resolve();
        }
      };
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
      console.log('[WebRTC Player] Playback started');
    } catch (error) {
      console.error('[WebRTC Player] Play failed:', error);
      throw error;
    }
  }

  pause(): void {
    this.video.pause();
  }

  stop(): void {
    this.video.pause();

    // Stop all tracks to release decoders
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach(track => track.stop());
      this.mediaStream = null;
    }

    if (this.peerConnection) {
      this.peerConnection.close();
      this.peerConnection = null;
    }

    this.video.srcObject = null;
  }

  destroy(): void {
    console.log('[WebRTC Player] Destroying player');
    this.isDestroyed = true;

    // Clear any pending ICE gathering timeout
    if (this.iceGatheringTimeout) {
      clearTimeout(this.iceGatheringTimeout);
      this.iceGatheringTimeout = null;
    }

    // Stop all tracks in the media stream (prevents decoder memory leaks)
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach(track => {
        track.stop();
        console.log('[WebRTC Player] Stopped track:', track.kind);
      });
      this.mediaStream = null;
    }

    // Close peer connection and remove event listeners
    if (this.peerConnection) {
      // Remove event listeners to prevent memory leaks
      this.peerConnection.ontrack = null;
      this.peerConnection.onconnectionstatechange = null;
      this.peerConnection.oniceconnectionstatechange = null;
      this.peerConnection.onicegatheringstatechange = null;

      this.peerConnection.close();
      this.peerConnection = null;
    }

    // Clean up video element
    this.video.pause();
    this.video.srcObject = null;
    this.video.load(); // Forces release of any decoder resources

    if (this.video.parentNode) {
      this.video.parentNode.removeChild(this.video);
    }

    console.log('[WebRTC Player] Cleanup complete');
  }

  getElement(): HTMLElement {
    return this.video;
  }

  getConnectionState(): string {
    return this.peerConnection?.connectionState || 'disconnected';
  }
}
