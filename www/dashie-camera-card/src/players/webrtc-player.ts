/**
 * WebRTC Player
 * Connects to go2rtc WebRTC endpoint for low-latency streaming
 */

import type { IPlayer } from '../types';

export class WebRTCPlayer implements IPlayer {
  private video: HTMLVideoElement;
  private peerConnection: RTCPeerConnection | null = null;
  private container: HTMLElement;

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
      console.log('[WebRTC Player] Received track:', event.track.kind);
      if (event.streams && event.streams[0]) {
        this.video.srcObject = event.streams[0];
        console.log('[WebRTC Player] Attached stream to video');
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
      if (!this.peerConnection) {
        resolve();
        return;
      }

      // If already complete, resolve immediately
      if (this.peerConnection.iceGatheringState === 'complete') {
        resolve();
        return;
      }

      // Wait for ICE gathering to complete (with timeout)
      const timeout = setTimeout(() => {
        console.log('[WebRTC Player] ICE gathering timeout, proceeding...');
        resolve();
      }, 2000);

      this.peerConnection.onicegatheringstatechange = () => {
        if (this.peerConnection?.iceGatheringState === 'complete') {
          clearTimeout(timeout);
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
    if (this.peerConnection) {
      this.peerConnection.close();
      this.peerConnection = null;
    }
  }

  destroy(): void {
    console.log('[WebRTC Player] Destroying player');

    if (this.peerConnection) {
      this.peerConnection.close();
      this.peerConnection = null;
    }

    this.video.pause();
    this.video.srcObject = null;

    if (this.video.parentNode) {
      this.video.parentNode.removeChild(this.video);
    }
  }

  getElement(): HTMLElement {
    return this.video;
  }

  getConnectionState(): string {
    return this.peerConnection?.connectionState || 'disconnected';
  }
}
