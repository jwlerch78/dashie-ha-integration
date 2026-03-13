/**
 * Native RTSP Player
 * Uses Android's native video decoding via JavaScript bridge.
 * Only available in Dashie Kiosk WebView.
 *
 * The native player creates an overlay ABOVE the WebView, positioned
 * to align with this card's position on screen.
 */
import type { IPlayer } from '../types';
export declare class NativeRtspPlayer implements IPlayer {
    private streamId;
    private placeholder;
    private resizeObserver;
    private isActive;
    constructor(container: HTMLElement);
    /**
     * Check if native RTSP playback is available.
     */
    static isSupported(): boolean;
    load(url: string): Promise<void>;
    private setupResizeObserver;
    private handleScroll;
    private updatePosition;
    play(): Promise<void>;
    pause(): void;
    stop(): void;
    destroy(): void;
    private cleanup;
    getElement(): HTMLElement;
    getConnectionState(): string;
    /**
     * Hide the native overlay (e.g., when showing a modal).
     */
    hide(): void;
    /**
     * Show the native overlay.
     */
    show(): void;
}
//# sourceMappingURL=native-rtsp-player.d.ts.map