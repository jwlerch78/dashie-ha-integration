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
export declare class MP4Player implements IPlayer {
    private video;
    private container;
    private streamUrl;
    private destroyed;
    constructor(container: HTMLElement);
    load(url: string): Promise<void>;
    private emitError;
    play(): Promise<void>;
    pause(): void;
    stop(): void;
    destroy(): void;
    getElement(): HTMLElement;
    getConnectionState(): string;
    /**
     * Check if the browser/WebView supports fMP4 streaming.
     * Most modern browsers and Android WebViews support this.
     */
    static isSupported(): boolean;
}
//# sourceMappingURL=mp4-player.d.ts.map