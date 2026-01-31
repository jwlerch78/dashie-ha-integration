/**
 * Platform Detection
 * Detects whether running on Dashie tablet or browser
 */

import type { Platform, DeviceMetrics } from './types';

export class PlatformDetector {
  private static platform: Platform | null = null;
  private static metrics: DeviceMetrics | null = null;

  /**
   * Detect the current platform
   */
  static detectPlatform(): Platform {
    if (this.platform) {
      return this.platform;
    }

    // Check for Dashie device bridge
    if (window.dashieDevice || window.DashieNative) {
      console.log('[Dashie Camera] Detected Dashie tablet via device bridge');
      this.platform = 'dashie-tablet';
      return this.platform;
    }

    // Check user agent for Dashie app
    if (navigator.userAgent.includes('DashieApp')) {
      console.log('[Dashie Camera] Detected Dashie tablet via user agent');
      this.platform = 'dashie-tablet';
      return this.platform;
    }

    // Check for Android WebView indicators
    const isWebView = navigator.userAgent.includes('wv');
    const isAndroid = navigator.userAgent.includes('Android');

    if (isWebView && isAndroid) {
      console.log('[Dashie Camera] Detected Android WebView (might be Dashie)');
      // Still treat as browser if no device bridge
      this.platform = 'browser';
      return this.platform;
    }

    console.log('[Dashie Camera] Detected browser platform');
    this.platform = 'browser';
    return this.platform;
  }

  /**
   * Get device metrics (only available on Dashie tablets)
   */
  static async getDeviceMetrics(): Promise<DeviceMetrics | null> {
    if (this.metrics) {
      return this.metrics;
    }

    const bridge = window.dashieDevice || window.DashieNative;
    if (!bridge) {
      console.log('[Dashie Camera] No device bridge available');
      return null;
    }

    try {
      this.metrics = await bridge.getSystemMetrics();
      console.log('[Dashie Camera] Device metrics:', this.metrics);
      return this.metrics;
    } catch (error) {
      console.error('[Dashie Camera] Failed to get device metrics:', error);
      return null;
    }
  }

  /**
   * Check if device supports a specific codec
   */
  static async supportsCodec(codec: string): Promise<boolean> {
    const metrics = await this.getDeviceMetrics();
    if (!metrics) {
      // On browsers, assume H.264 support
      return codec === 'h264' || codec === 'vp8';
    }

    return metrics.videoCodecs.includes(codec);
  }

  /**
   * Get supported video codecs
   */
  static async getSupportedCodecs(): Promise<string[]> {
    const metrics = await this.getDeviceMetrics();
    if (!metrics) {
      // Browser defaults
      return ['h264', 'vp8', 'vp9'];
    }

    return metrics.videoCodecs;
  }

  /**
   * Check if running on Dashie tablet
   */
  static isDashieTablet(): boolean {
    return this.detectPlatform() === 'dashie-tablet';
  }

  /**
   * Check if running in browser
   */
  static isBrowser(): boolean {
    return this.detectPlatform() === 'browser';
  }

  /**
   * Reset cached platform detection (for testing)
   */
  static reset(): void {
    this.platform = null;
    this.metrics = null;
  }

  /**
   * Get platform-specific debug info
   */
  static getDebugInfo(): string {
    const platform = this.detectPlatform();
    const userAgent = navigator.userAgent;
    const hasBridge = !!(window.dashieDevice || window.DashieNative);

    return `
Platform: ${platform}
Has Device Bridge: ${hasBridge}
User Agent: ${userAgent}
    `.trim();
  }
}
