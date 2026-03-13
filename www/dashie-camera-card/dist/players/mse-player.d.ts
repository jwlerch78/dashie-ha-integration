/**
 * MSE Player
 * Uses go2rtc's WebSocket endpoint to stream H.264 directly via MediaSource Extensions
 * No WebRTC handshaking - just a simple WebSocket connection
 */
import type { IPlayer } from '../types';
export declare class MSEPlayer implements IPlayer {
    private video;
    private container;
    private ws;
    private mediaSource;
    private sourceBuffer;
    private bufferQueue;
    private isBufferUpdating;
    private hasInitSegment;
    constructor(container: HTMLElement);
    load(url: string): Promise<void>;
    private buildWebSocketUrl;
    private connectWebSocket;
    private handleMessage;
    private initSourceBuffer;
    private appendBuffer;
    private processBufferQueue;
    private trimBuffer;
    private emitError;
    play(): Promise<void>;
    pause(): void;
    stop(): void;
    destroy(): void;
    private cleanup;
    getElement(): HTMLElement;
    getConnectionState(): string;
}
//# sourceMappingURL=mse-player.d.ts.map