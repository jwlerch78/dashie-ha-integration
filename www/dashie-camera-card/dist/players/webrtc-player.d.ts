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
export declare class WebRTCPlayer implements IPlayer {
    private video;
    private peerConnection;
    private container;
    private mediaStream;
    private iceGatheringTimeout;
    private isDestroyed;
    constructor(container: HTMLElement);
    load(url: string): Promise<void>;
    private connectToGo2RTC;
    private waitForIceGathering;
    private emitError;
    play(): Promise<void>;
    pause(): void;
    stop(): void;
    destroy(): void;
    getElement(): HTMLElement;
    getConnectionState(): string;
}
//# sourceMappingURL=webrtc-player.d.ts.map