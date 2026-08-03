/**
 * Telemetry data extracted from a single video frame.
 */
export interface TelemetryRecord {
  sequence_number: number;
  mission_elapsed_time: string | null;
  mission_elapsed_time_raw: string | null;
  speed_left: { value: number | null; unit: string | null; status: string };
  speed_right: { value: number | null; unit: string | null; status: string };
  altitude_left: { value: number | null; unit: string | null; status: string };
  altitude_right: { value: number | null; unit: string | null; status: string };
  stage_left_label: string | null;
  stage_right_label: string | null;
  stage_separation_text: string | null;
  stage_assignment_left: string;
  stage_assignment_right: string;
  separation_state: 'pre_separation' | 'post_separation';
  t_zero_found: boolean;
  stage_sep_found: boolean;
  starship_engines: Record<string, 'active' | 'inactive' | 'undetected'>;
  superheavy_engines: Record<string, 'active' | 'inactive' | 'undetected'>;
  detection_accuracy: { starship: number; superheavy: number };
  timestamp: number;
}

/**
 * Error payload received over WebSocket.
 */
export interface ErrorPayload {
  code: string;
  message: string;
}

/**
 * Validation result payload received over WebSocket.
 */
export interface ValidationResult {
  valid: boolean;
  message: string;
  url: string;
}

/**
 * Frame preview payload received over WebSocket.
 */
export interface FramePayload {
  image: string;  // base64-encoded JPEG
  sequence: number;
  processing_fps?: number;
}

/**
 * Message received over the WebSocket connection.
 */
export interface WebSocketMessage {
  type: 'telemetry' | 'status' | 'error' | 'validation_result' | 'frame';
  payload: TelemetryRecord | PipelineStatus | ErrorPayload | ValidationResult | FramePayload;
}

/**
 * Current state of the backend pipeline.
 */
export interface PipelineStatus {
  status: 'stopped' | 'running' | 'disconnected' | 'reconnecting';
  gpu: { available: boolean; device_name: string | null };
  skip_frames: number;
  current_sequence: number;
  processing_fps?: number;
}

/**
 * Commands sent from the frontend to control the pipeline.
 */
export type ControlCommand =
  | { action: 'start'; source_url: string; skip_frames?: number; correlation_id?: string }
  | { action: 'stop'; correlation_id?: string }
  | { action: 'validate_url'; url: string; correlation_id?: string }
  | { action: 'set_interval'; skip_frames: number; correlation_id?: string };
