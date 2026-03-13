/**
 * HLS Player
 * Uses HLS.js (preferred) or native HLS support (iOS/Safari fallback)
 */
import type { IPlayer } from '../types';
export declare class HLSPlayer implements IPlayer {
    private video;
    private hls;
    private url;
    constructor(container: HTMLElement);
    load(url: string): Promise<void>;
    private supportsNativeHLS;
    private loadWithHlsJs;
    play(): Promise<void>;
    pause(): void;
    stop(): void;
    destroy(): void;
    getElement(): HTMLElement;
    /**
     * Reload the current stream
     */
    reload(): Promise<void>;
    /**
     * Handle video errors
     */
    private handleError;
    /**
     * Get current playback state
     */
    getState(): {
        playing: boolean;
        currentTime: number;
        duration: number;
        buffered: number;
    };
}
//# sourceMappingURL=hls-player.d.ts.map