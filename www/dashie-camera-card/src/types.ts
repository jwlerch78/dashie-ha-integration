/**
 * TypeScript type definitions for Dashie Camera Card
 */

// Home Assistant types
export interface HomeAssistant {
  states: { [entity_id: string]: HassEntity };
  callService: (domain: string, service: string, data?: any) => Promise<any>;
  callWS: (msg: any) => Promise<any>;
  connection: any;
  language: string;
  config: any;
}

export interface HassEntity {
  entity_id: string;
  state: string;
  attributes: { [key: string]: any };
  context: any;
  last_changed: string;
  last_updated: string;
}

// Card configuration
export interface DashieCameraCardConfig {
  type: string;
  entity?: string;  // Optional if stream_name is provided
  title?: string;

  // Stream configuration
  quality?: 'auto' | 'high' | 'medium' | 'low' | 'mobile';
  protocol?: 'auto' | 'webrtc' | 'hls' | 'rtsp';
  prefer_codec?: 'auto' | 'h264' | 'h265';

  // Interaction
  tap_action?: 'maximize' | 'fullscreen' | 'more-info' | 'none';
  hold_action?: 'frigate-events' | 'more-info' | 'none';
  double_tap_action?: 'fullscreen' | 'maximize' | 'none';

  // Frigate integration
  frigate?: {
    enabled?: boolean;
    url?: string;
    show_events?: boolean;
    show_detections?: boolean;
    event_hours?: number;
  };

  // Advanced
  go2rtc_url?: string;
  stream_name?: string;
  show_debug?: boolean;
}

// Platform types
export type Platform = 'dashie-tablet' | 'browser';

export interface DeviceMetrics {
  ramAvailable: number;      // MB
  ramTotal: number;          // MB
  cpuUsage: number;          // 0-100%
  thermalState: 'normal' | 'warning' | 'critical';
  batteryPercent: number;
  isTablet: boolean;
  platform: 'android' | 'web';
  videoCodecs: string[];     // ['h264', 'h265', 'vp8', 'vp9']
  hardwareDecoderCount: number;
}

// Stream configuration
export interface StreamConfig {
  protocol: 'hls' | 'webrtc' | 'rtsp';
  url: string;
  codec?: 'h264' | 'h265' | 'vp8' | 'vp9';
  quality?: 'high' | 'medium' | 'low' | 'mobile';
  bitrate?: number;
  resolution?: string;
}

// Player interface
export interface IPlayer {
  load(url: string): Promise<void>;
  play(): Promise<void>;
  pause(): void;
  stop(): void;
  destroy(): void;
  getElement(): HTMLElement;
}

// Frigate types
export interface FrigateEvent {
  id: string;
  label: string;  // 'person', 'car', 'dog'
  score: number;  // 0.0 - 1.0
  start_time: number;
  end_time?: number;
  thumbnail: string;
  camera: string;
}

export interface FrigateDetection {
  label: string;
  box: [number, number, number, number];  // [x1, y1, x2, y2] normalized
  score: number;
  area: number;
}

// Dashie device bridge (injected by Android app)
export interface DashieDeviceBridge {
  getSystemMetrics: () => Promise<DeviceMetrics>;
  getVideoCodecSupport: () => Promise<string[]>;
  getHardwareDecoderCount: () => Promise<number>;

  // Native RTSP player methods (for Dashie Kiosk WebView)
  isNativeRtspSupported?: () => boolean;
  startRtspStream?: (id: string, rtspUrl: string, x: number, y: number, width: number, height: number) => void;
  updateRtspStreamPosition?: (id: string, x: number, y: number, width: number, height: number) => void;
  stopRtspStream?: (id: string) => void;
  stopAllRtspStreams?: () => void;
  hideRtspOverlays?: () => void;
  showRtspOverlays?: () => void;
  isRtspStreamPlaying?: (id: string) => boolean;
  getActiveRtspStreamCount?: () => number;
}

// Extend window interface
declare global {
  interface Window {
    dashieDevice?: DashieDeviceBridge;
    DashieNative?: DashieDeviceBridge;
    customCards?: Array<{
      type: string;
      name: string;
      description: string;
      preview?: boolean;
    }>;
    Hls?: any;  // HLS.js library
  }
}

// Card editor
export interface LovelaceCardEditor extends HTMLElement {
  setConfig(config: DashieCameraCardConfig): void;
  hass?: HomeAssistant;
}

// Helper types
export type QualityTier = 'high' | 'medium' | 'low' | 'mobile';
export type Protocol = 'hls' | 'webrtc' | 'rtsp';
export type TapAction = 'maximize' | 'fullscreen' | 'more-info' | 'none';
