/**
 * WebRTC Player
 * Connects to Home Assistant WebRTC endpoint or go2rtc
 */

import type { IPlayer, HomeAssistant } from '../types';

export class WebRTCPlayer implements IPlayer {
  private video: HTMLVideoElement;
  private peerConnection: RTCPeerConnection | null = null;
  private url: string | null = null;
  private hass: HomeAssistant | null = null;

  constructor(container: HTMLElement, hass?: HomeAssistant) {
    this.hass = hass || null;

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
    this.url = url;
    console.log('[WebRTC Player] Loading stream:', url);

    // For MVP, we'll use a simple WebRTC implementation
    // Full implementation would:
    // 1. Connect to go2rtc WebRTC endpoint
    // 2. Handle ICE candidates
    // 3. Set up peer connection
    // 4. Attach media stream to video element

    try {
      await this.connectWebRTC(url);
    } catch (error) {
      console.error('[WebRTC Player] Failed to connect:', error);
      throw error;
    }
  }

  private async connectWebRTC(url: string): Promise<void> {
    // Create peer connection
    this.peerConnection = new RTCPeerConnection({
      iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
    });

    // Handle incoming tracks
    this.peerConnection.ontrack = (event) => {
      console.log('[WebRTC Player] Received track:', event.track.kind);
      if (this.video.srcObject !== event.streams[0]) {
        this.video.srcObject = event.streams[0];
        console.log('[WebRTC Player] Attached stream to video');
      }
    };

    // Handle ICE candidates
    this.peerConnection.onicecandidate = (event) => {
      if (event.candidate) {
        console.log('[WebRTC Player] New ICE candidate');
      }
    };

    // Handle connection state changes
    this.peerConnection.onconnectionstatechange = () => {
      console.log('[WebRTC Player] Connection state:', this.peerConnection?.connectionState);
    };

    // Create offer
    const offer = await this.peerConnection.createOffer({
      offerToReceiveVideo: true,
      offerToReceiveAudio: true,
    });

    await this.peerConnection.setLocalDescription(offer);

    // Send offer to go2rtc or Home Assistant WebRTC endpoint
    // This is simplified - real implementation would use proper signaling
    console.log('[WebRTC Player] Created offer, would send to:', url);

    // TODO: Implement proper WebRTC signaling with go2rtc/HA
    console.warn('[WebRTC Player] Full WebRTC implementation pending');
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

  /**
   * Get current connection state
   */
  getConnectionState(): string {
    return this.peerConnection?.connectionState || 'disconnected';
  }
}
