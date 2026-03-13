/**
 * Platform Detection
 * Detects whether running on Dashie tablet or browser
 */
import type { Platform, DeviceMetrics } from './types';
export declare class PlatformDetector {
    private static platform;
    private static metrics;
    /**
     * Detect the current platform
     */
    static detectPlatform(): Platform;
    /**
     * Get device metrics (only available on Dashie tablets)
     */
    static getDeviceMetrics(): Promise<DeviceMetrics | null>;
    /**
     * Check if device supports a specific codec
     */
    static supportsCodec(codec: string): Promise<boolean>;
    /**
     * Get supported video codecs
     */
    static getSupportedCodecs(): Promise<string[]>;
    /**
     * Check if running on Dashie tablet
     */
    static isDashieTablet(): boolean;
    /**
     * Check if running in browser
     */
    static isBrowser(): boolean;
    /**
     * Reset cached platform detection (for testing)
     */
    static reset(): void;
    /**
     * Get platform-specific debug info
     */
    static getDebugInfo(): string;
}
//# sourceMappingURL=platform-detector.d.ts.map