/**
 * TypeScript type definitions for Dashie Camera Card
 */
export interface HomeAssistant {
    states: {
        [entity_id: string]: HassEntity;
    };
    callService: (domain: string, service: string, data?: any) => Promise<any>;
    callWS: <T = any>(msg: any) => Promise<T>;
    connection: any;
    language: string;
    config: any;
}
export interface HassEntity {
    entity_id: string;
    state: string;
    attributes: {
        [key: string]: any;
    };
    context: any;
    last_changed: string;
    last_updated: string;
}
export interface DashieCameraCardConfig {
    type: string;
    entity?: string;
    title?: string;
    quality?: 'auto' | 'high' | 'medium' | 'low' | 'mobile';
    protocol?: 'auto' | 'webrtc' | 'hls' | 'rtsp' | 'mse';
    prefer_codec?: 'auto' | 'h264' | 'h265';
    tap_action?: 'maximize' | 'fullscreen' | 'more-info' | 'none';
    hold_action?: 'frigate-events' | 'more-info' | 'none';
    double_tap_action?: 'fullscreen' | 'maximize' | 'none';
    frigate?: {
        enabled?: boolean;
        url?: string;
        show_events?: boolean;
        show_detections?: boolean;
        event_hours?: number;
    };
    go2rtc_url?: string;
    stream_name?: string;
    show_debug?: boolean;
    transcode?: {
        enabled?: boolean;
        resolution?: '1080p' | '720p' | '480p' | '360p';
        profile?: 'baseline' | 'main' | 'high';
    };
    camgrid?: {
        cameras?: string[];
        stream_name?: string;
        grid?: '2x1' | '2x2' | '3x1' | '1x2' | '1x3';
        fps?: number;
        quality?: number;
    };
}
export type Platform = 'dashie-tablet' | 'browser';
export interface DeviceMetrics {
    ramAvailable: number;
    ramTotal: number;
    cpuUsage: number;
    thermalState: 'normal' | 'warning' | 'critical';
    batteryPercent: number;
    isTablet: boolean;
    platform: 'android' | 'web';
    videoCodecs: string[];
    hardwareDecoderCount: number;
}
export interface StreamConfig {
    protocol: 'hls' | 'webrtc' | 'rtsp';
    url: string;
    codec?: 'h264' | 'h265' | 'vp8' | 'vp9';
    quality?: 'high' | 'medium' | 'low' | 'mobile';
    bitrate?: number;
    resolution?: string;
}
export interface IPlayer {
    load(url: string): Promise<void>;
    play(): Promise<void>;
    pause(): void;
    stop(): void;
    destroy(): void;
    getElement(): HTMLElement;
}
export interface FrigateEvent {
    id: string;
    label: string;
    score: number;
    start_time: number;
    end_time?: number;
    thumbnail: string;
    camera: string;
}
export interface FrigateDetection {
    label: string;
    box: [number, number, number, number];
    score: number;
    area: number;
}
export interface DashieDeviceBridge {
    getSystemMetrics: () => Promise<DeviceMetrics>;
    getVideoCodecSupport: () => Promise<string[]>;
    getHardwareDecoderCount: () => Promise<number>;
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
        Hls?: any;
    }
}
export interface LovelaceCardEditor extends HTMLElement {
    setConfig(config: DashieCameraCardConfig): void;
    hass?: HomeAssistant;
}
export type QualityTier = 'high' | 'medium' | 'low' | 'mobile';
export type Protocol = 'hls' | 'webrtc' | 'rtsp' | 'mse';
export type PlayerType = 'native-rtsp' | 'webrtc' | 'hls' | 'mse' | 'mp4';
export type TapAction = 'maximize' | 'fullscreen' | 'more-info' | 'none';
//# sourceMappingURL=types.d.ts.map