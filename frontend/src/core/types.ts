/**
 * Telemetry data extracted from a single video frame.
 */
export interface TelemetryRecord {
  sequence_number: number;
  /** Absolute frame index in the source video (absent from older backends) */
  frame_number?: number | null;
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
 * Replay payload received over WebSocket: the current Run's telemetry so far,
 * sent in chunks right after connecting (see backend/src/api/replay.py), and
 * broadcast as an empty chunk 0 when a new Run starts
 * (backend/src/pipeline/history.py `empty_replay_message`). Chunk 0 resets
 * the client's live data.
 */
export interface ReplayPayload {
  /** Id of the Run the records belong to */
  run_id: string;
  /** Chunk index, from 0 */
  chunk: number;
  /** True on the last chunk of this replay */
  final: boolean;
  /** True when the server dropped the Run's oldest records */
  truncated: boolean;
  /** The `telemetry` payloads, oldest first */
  records: TelemetryRecord[];
}

/**
 * Status of one mission timeline event (backend/src/timeline/status.py).
 * `skipped` is only ever set by a person.
 */
export type TimelineStatusValue = 'not_yet' | 'pending' | 'success' | 'partial' | 'failure' | 'skipped';

/** One timeline event's status: decided automatically or set by a person. */
export interface TimelineEventStatus {
  /** The event's id in the mission's timeline */
  event_id: string;
  status: TimelineStatusValue;
  /** 'human' while a person's override stands; cleared overrides go back to 'auto' */
  source: 'auto' | 'human';
  /** Why, e.g. "HUD milestone MAX Q" or "Super Heavy MECO at T+00:02:41 (33 -> 3 engines)" */
  evidence: string | null;
  /** MET in seconds the status was decided at, when known */
  met: number | null;
  /** ISO 8601 UTC time of the last change */
  updated_at: string;
}

/**
 * The `timeline_status` payload: every event's status, sent whole on every
 * change and after the replay (backend/src/timeline/service.py). Also what
 * `GET /api/timeline/status` returns.
 */
export interface TimelineStatusPayload {
  /** The mission whose timeline is followed (PUT /api/timeline/active), or null */
  mission_id: string | null;
  /** The Run the statuses belong to (as in ReplayPayload) */
  run_id: string | null;
  /** Grows with every change, also across backend restarts: a lower one is stale */
  revision: number;
  /**
   * MET in seconds: the furthest seen this Run, or before T-0 the countdown
   * clock as it reads now (negative; sent about once a second while it runs)
   */
  met: number | null;
  /** Where that MET came from: the HUD clock in the records, or the countdown clock */
  clock_source: 'telemetry' | 'countdown' | null;
  /** The countdown clock is holding (always false once the telemetry clock runs); absent from older backends */
  hold?: boolean;
  /** Events waiting for a person */
  pending: number;
  /** In timeline order */
  events: TimelineEventStatus[];
}

/**
 * Message received over the WebSocket connection.
 */
export interface WebSocketMessage {
  type: 'telemetry' | 'status' | 'error' | 'validation_result' | 'frame' | 'replay' | 'timeline_status';
  payload:
    | TelemetryRecord
    | PipelineStatus
    | ErrorPayload
    | ValidationResult
    | FramePayload
    | ReplayPayload
    | TimelineStatusPayload;
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
  /** Full backend version (release.commit), see backend/src/core/version.py */
  version?: string;
}

/**
 * A change of engine statuses at row `i` of the columns: the full map of
 * each vehicle (ss Starship, sh Super Heavy) whose statuses differ from the
 * previous entry for that vehicle. Rows in between keep the last map.
 */
export interface EngineStatusChange {
  i: number;
  ss?: Record<string, 'active' | 'inactive' | 'undetected'>;
  sh?: Record<string, 'active' | 'inactive' | 'undetected'>;
}

/**
 * Columnar previous-flight payload from `/api/previous-flights/{name}`.
 * Every array has one entry per recorded frame; ss_* is Starship, sh_* Super
 * Heavy, met is the recorded MET string and t the frame clock in seconds.
 */
export interface PreviousFlightColumns {
  met: string[];
  t: number[];
  ss_speed: (number | null)[];
  sh_speed: (number | null)[];
  ss_alt: (number | null)[];
  sh_alt: (number | null)[];
  /** Engine status changes; absent from older backends, empty when the file has no statuses. */
  engines?: EngineStatusChange[];
  /** The mission timeline's final statuses, when the flight was saved with them. */
  timeline_status?: TimelineStatusPayload;
}

/**
 * Metadata about a previous flight available from the API.
 */
export interface PreviousFlightInfo {
  name: string;
  filename: string;
}

/**
 * Commands sent from the frontend to control the pipeline.
 */
export type ControlCommand =
  | { action: 'start'; source_url: string; skip_frames?: number; correlation_id?: string }
  | { action: 'stop'; correlation_id?: string }
  | { action: 'validate_url'; url: string; correlation_id?: string }
  | { action: 'set_interval'; skip_frames: number; correlation_id?: string };
