/**
 * go2rtc Auto-Detection
 * Finds go2rtc endpoints from various sources (standalone, Frigate, etc.)
 */

export interface Go2rtcEndpoint {
  url: string;
  source: 'standalone' | 'frigate' | 'addon' | 'custom';
  available: boolean;
}

export class Go2rtcDetector {
  private static cachedEndpoint: Go2rtcEndpoint | null = null;
  private static lastCheck: number = 0;
  private static readonly CACHE_TTL = 60000; // 1 minute

  /**
   * Common go2rtc endpoints to check
   */
  private static readonly ENDPOINTS = [
    // User-specified (from card config)
    { url: '', source: 'custom' as const },

    // Standalone go2rtc addon
    { url: 'http://localhost:1984', source: 'standalone' as const },

    // go2rtc addon (Docker hostname)
    { url: 'http://ccab4aaf-go2rtc:1984', source: 'addon' as const },

    // Frigate's built-in go2rtc
    { url: 'http://ccab4aaf-frigate:1984', source: 'frigate' as const },
    { url: 'http://frigate:1984', source: 'frigate' as const },

    // Common custom setups
    { url: 'http://192.168.1.1:1984', source: 'custom' as const },
  ];

  /**
   * Detect go2rtc endpoint
   */
  static async detect(customUrl?: string): Promise<Go2rtcEndpoint | null> {
    // Check cache
    if (this.cachedEndpoint && Date.now() - this.lastCheck < this.CACHE_TTL) {
      return this.cachedEndpoint;
    }

    // Build endpoint list with custom URL first
    const endpoints = customUrl
      ? [{ url: customUrl, source: 'custom' as const }, ...this.ENDPOINTS]
      : this.ENDPOINTS;

    // Try each endpoint
    for (const endpoint of endpoints) {
      if (!endpoint.url) continue;

      try {
        const available = await this.checkEndpoint(endpoint.url);
        if (available) {
          this.cachedEndpoint = { ...endpoint, available: true };
          this.lastCheck = Date.now();
          console.log('[go2rtc Detector] Found endpoint:', this.cachedEndpoint);
          return this.cachedEndpoint;
        }
      } catch (error) {
        // Continue to next endpoint
      }
    }

    console.warn('[go2rtc Detector] No go2rtc endpoint found');
    return null;
  }

  /**
   * Check if an endpoint is available
   */
  private static async checkEndpoint(url: string): Promise<boolean> {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 2000);

      const response = await fetch(`${url}/api`, {
        method: 'GET',
        signal: controller.signal,
      });

      clearTimeout(timeout);
      return response.ok;
    } catch (error) {
      return false;
    }
  }

  /**
   * Get HLS stream URL
   */
  static getHlsUrl(endpoint: Go2rtcEndpoint, streamName: string): string {
    return `${endpoint.url}/api/stream.m3u8?src=${streamName}`;
  }

  /**
   * Get WebRTC URL
   */
  static getWebrtcUrl(endpoint: Go2rtcEndpoint, streamName: string): string {
    return `${endpoint.url}/api/webrtc?src=${streamName}`;
  }

  /**
   * Get list of available streams
   */
  static async getStreams(endpoint: Go2rtcEndpoint): Promise<string[]> {
    try {
      const response = await fetch(`${endpoint.url}/api/streams`);
      if (!response.ok) return [];

      const data = await response.json();
      return Object.keys(data);
    } catch (error) {
      console.error('[go2rtc Detector] Failed to get streams:', error);
      return [];
    }
  }

  /**
   * Check if a specific stream exists
   */
  static async hasStream(endpoint: Go2rtcEndpoint, streamName: string): Promise<boolean> {
    const streams = await this.getStreams(endpoint);
    return streams.includes(streamName);
  }

  /**
   * Clear cached endpoint
   */
  static clearCache(): void {
    this.cachedEndpoint = null;
    this.lastCheck = 0;
  }
}

/**
 * Fallback stream sources when go2rtc is not available
 */
export class FallbackStreamProvider {
  /**
   * Get Home Assistant's MJPEG proxy URL
   * Works without go2rtc but lower quality
   */
  static getMjpegUrl(entityId: string): string {
    return `/api/camera_proxy_stream/${entityId}`;
  }

  /**
   * Get Home Assistant's snapshot URL
   * Static image, refreshed periodically
   */
  static getSnapshotUrl(entityId: string): string {
    return `/api/camera_proxy/${entityId}?token=${Date.now()}`;
  }

  /**
   * Try to use HA's native WebRTC (if available)
   */
  static async tryNativeWebrtc(hass: any, entityId: string): Promise<string | null> {
    try {
      // Check if entity supports WebRTC
      const entity = hass.states[entityId];
      if (!entity) return null;

      // Some camera integrations expose WebRTC via attributes
      if (entity.attributes.frontend_stream_type === 'webrtc') {
        // Use HA's WebRTC signaling
        return `/api/camera/webrtc/${entityId}`;
      }

      return null;
    } catch (error) {
      return null;
    }
  }
}
