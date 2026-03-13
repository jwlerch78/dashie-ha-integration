/**
 * go2rtc Auto-Detection
 * Finds go2rtc endpoints from various sources (standalone, Frigate, etc.)
 */
export interface Go2rtcEndpoint {
    url: string;
    source: 'standalone' | 'frigate' | 'addon' | 'custom';
    available: boolean;
}
export declare class Go2rtcDetector {
    private static cachedEndpoint;
    private static lastCheck;
    private static readonly CACHE_TTL;
    /**
     * Common go2rtc endpoints to check
     */
    private static readonly ENDPOINTS;
    /**
     * Detect go2rtc endpoint
     */
    static detect(customUrl?: string): Promise<Go2rtcEndpoint | null>;
    /**
     * Check if an endpoint is available
     */
    private static checkEndpoint;
    /**
     * Get HLS stream URL
     */
    static getHlsUrl(endpoint: Go2rtcEndpoint, streamName: string): string;
    /**
     * Get WebRTC URL
     */
    static getWebrtcUrl(endpoint: Go2rtcEndpoint, streamName: string): string;
    /**
     * Get list of available streams
     */
    static getStreams(endpoint: Go2rtcEndpoint): Promise<string[]>;
    /**
     * Check if a specific stream exists
     */
    static hasStream(endpoint: Go2rtcEndpoint, streamName: string): Promise<boolean>;
    /**
     * Clear cached endpoint
     */
    static clearCache(): void;
}
/**
 * Fallback stream sources when go2rtc is not available
 */
export declare class FallbackStreamProvider {
    /**
     * Get Home Assistant's MJPEG proxy URL
     * Works without go2rtc but lower quality
     */
    static getMjpegUrl(entityId: string): string;
    /**
     * Get Home Assistant's snapshot URL
     * Static image, refreshed periodically
     */
    static getSnapshotUrl(entityId: string): string;
    /**
     * Try to use HA's native WebRTC (if available)
     */
    static tryNativeWebrtc(hass: any, entityId: string): Promise<string | null>;
}
//# sourceMappingURL=go2rtc-detector.d.ts.map