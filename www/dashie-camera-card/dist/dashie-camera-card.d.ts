/**
 * Dashie Camera Card
 * Adaptive camera card for Home Assistant
 */
import { LitElement } from 'lit';
import type { HomeAssistant, DashieCameraCardConfig } from './types';
export declare class DashieCameraCard extends LitElement {
    hass: HomeAssistant;
    private config;
    private platform;
    private player;
    private loading;
    private error;
    private maximized;
    static styles: import("lit").CSSResult;
    setConfig(config: DashieCameraCardConfig): void;
    private intersectionObserver;
    private isVisible;
    private wasPlaying;
    private hasInitialized;
    connectedCallback(): void;
    disconnectedCallback(): void;
    /**
     * Stop streams when card scrolls out of view to save memory and bandwidth.
     */
    private setupVisibilityTracking;
    /**
     * Handle page visibility changes (tab switching, screen off, etc.)
     */
    private handlePageVisibility;
    protected firstUpdated(): void;
    private initializeStream;
    /**
     * Get the stream URL based on platform and protocol.
     * Returns { url, playerType } to indicate which player to use.
     *
     * Platform Strategy (2026-02-02):
     * - Browsers: WebRTC (fast, firewall-friendly via go2rtc)
     * - Dashie Tablets: MSE via go2rtc stream.mp4 (plays in WebView, no native overlay complexity)
     * - Fallback: HLS (universal compatibility)
     */
    private getStreamConfig;
    /**
     * Query go2rtc API to get the camera's direct RTSP URL.
     * This allows Android to connect directly to the camera, bypassing go2rtc.
     */
    private getDirectRtspUrl;
    /**
     * Decode RTSP URL credentials for ExoPlayer compatibility.
     * ExoPlayer requires fully decoded credentials - its URI parser handles
     * multiple @ symbols correctly by using the LAST @ as the authority separator.
     *
     * Example:
     * Input:  rtsp://user%40email.com:pass%21word@host:554/stream
     * Output: rtsp://user@email.com:pass!word@host:554/stream
     */
    private decodeRtspCredentials;
    /**
     * Get camera stream URL via HA's WebSocket API.
     * This works through HA's authentication proxy, enabling remote access.
     */
    private getHaCameraStream;
    /**
     * Get the RTSP stream source URL for a camera entity from HA.
     * This uses the same mechanism as the WebRTC component to extract the camera's RTSP URL.
     */
    private getCameraStreamSource;
    /**
     * Check if a stream already exists in go2rtc.
     * This prevents spawning duplicate FFmpeg processes on every page load.
     */
    private streamExistsInGo2rtc;
    /**
     * Dynamically create or update a stream in go2rtc via its API.
     * This allows the card to provision transcoded streams on-demand without manual go2rtc.yaml config.
     *
     * IMPORTANT: This method now checks if the stream already exists to prevent
     * spawning duplicate FFmpeg processes, which can crash HA on resource-constrained devices.
     *
     * @param go2rtcUrl - Base URL of go2rtc server (e.g., http://192.168.1.1:1984)
     * @param streamName - Name for the stream (e.g., camera.family_room_transcoded)
     * @param sourceUrl - Source URL (RTSP, ffmpeg:, etc.)
     * @returns true if stream exists or was created successfully
     */
    private provisionGo2rtcStream;
    /**
     * Build an FFmpeg transcoding source URL for go2rtc.
     * This creates a source string that tells go2rtc to transcode the input stream.
     *
     * Uses LOCAL stream reference within go2rtc (not external RTSP URL).
     * Format: ffmpeg:<stream_name>#video=h264#width=<w>#height=<h>
     *
     * This requires the base stream to already exist in go2rtc.yaml with a valid
     * RTSP source. The transcoding layer references the existing stream by name.
     *
     * @param inputStreamName - Name of the source stream (camera entity ID)
     * @param resolution - Target resolution ('720p', '480p', etc.)
     * @param profile - H.264 profile ('baseline', 'main', 'high')
     */
    private buildTranscodeSource;
    /**
     * Provision a transcoded stream for tablet compatibility.
     * Creates both the source stream and a transcoded variant in go2rtc.
     *
     * @returns The stream name to use for playback
     */
    private provisionTranscodedStream;
    /**
     * Build a camgrid composite grid FFmpeg exec command.
     * Creates a single stream combining multiple cameras into a grid layout.
     *
     * @param cameras - Array of camera entity IDs
     * @param grid - Grid layout ('2x1', '2x2', etc.)
     * @param fps - Frames per second (default: 10)
     * @param quality - CRF quality value (default: 30)
     * @returns The exec:ffmpeg command string for go2rtc
     */
    private buildBirdseyeCommand;
    /**
     * Provision a CamGrid composite stream via HA service.
     * Calls dashie.provision_camgrid which runs on localhost (trusted producer).
     *
     * @returns The stream name to use for playback
     */
    private provisionCamGridStream;
    private createPlayer;
    private destroyPlayer;
    private handlePlayerError;
    private handleTap;
    private openMaximized;
    private closeMaximized;
    private enterFullscreen;
    private showMoreInfo;
    render(): import("lit-html").TemplateResult<1>;
    private renderMaximized;
    static getConfigElement(): HTMLElement;
    static getStubConfig(): {
        type: string;
        entity: string;
        title: string;
    };
    getCardSize(): number;
}
//# sourceMappingURL=dashie-camera-card.d.ts.map