# Implementation Plan: Logging Improvements

## Overview

This implementation introduces structured, centralized logging across the backend (Python/FastAPI) and frontend (TypeScript/Vite) of the Starship Telemetry Web View application. The plan progresses from backend logging infrastructure, through correlation ID propagation and performance instrumentation, to frontend logging utilities and WebSocket/lifecycle integration.

## Tasks

- [x] 1. Backend logging infrastructure
  - [x] 1.1 Create `src/logging_context.py` with context variables
    - Define `correlation_id_var` and `frame_seq_var` using `contextvars.ContextVar`
    - `correlation_id_var` defaults to `None`, type `str | None`
    - `frame_seq_var` defaults to `None`, type `int | None`
    - _Requirements: 2.1, 2.2, 2.5_

  - [x] 1.2 Create `src/logging_config.py` with JSONFormatter and `configure_logging()`
    - Implement `JSONFormatter(logging.Formatter)` that outputs single-line JSON per record
    - Include fields: `timestamp` (ISO 8601 UTC), `level`, `logger_name`, `module`, `message`
    - Conditionally include `correlation_id`, `frame_seq` from context vars (omit if None)
    - Conditionally include `exc_type` and `exc_traceback` when exception info is present
    - Conditionally include `duration_ms` and `fps` from `record.__dict__` extras
    - Handle non-serializable extras by converting to string
    - Implement `configure_logging()`: read `LOG_LEVEL` env var, validate against (DEBUG, INFO, WARNING, ERROR, CRITICAL case-insensitive), default to INFO, emit WARNING on invalid value
    - Attach `StreamHandler(sys.stdout)` with `JSONFormatter` to root logger, remove pre-existing handlers
    - Ensure idempotent behavior on repeated calls
    - _Requirements: 1.1, 1.3, 1.4, 1.5, 1.6, 1.7, 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ]* 1.3 Write property tests for JSONFormatter and configure_logging
    - **Property 1: Structured log output is valid single-line JSON with required fields**
    - **Property 2: Log level configuration respects valid values and rejects invalid ones**
    - **Property 3: Contextual fields appear in log output only when set**
    - **Property 4: Exception logging includes type and traceback**
    - **Validates: Requirements 1.3, 1.4, 1.6, 1.7, 2.1, 2.2, 2.4, 2.5**

  - [x] 1.4 Integrate `configure_logging()` in application startup
    - Call `configure_logging()` in `src/main.py` (or application entry point) before any HTTP/WebSocket processing begins
    - _Requirements: 1.2_

- [x] 2. Backend correlation ID propagation
  - [x] 2.1 Modify `src/server.py` for WebSocket correlation ID lifecycle
    - On WebSocket connect: generate UUID v4, store in `correlation_id_var`, log session start at INFO
    - On control command received: log command type and correlation_id at INFO
    - On control command: check for client-provided `correlation_id` in payload, validate as UUID v4 (lowercase hyphenated format), replace context var if valid, reject with WARNING if invalid
    - On disconnect: log correlation_id and connection duration in milliseconds at INFO
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 2.2 Write property test for correlation ID validation
    - **Property 6: Correlation ID validation accepts valid UUID v4 and rejects others**
    - **Validates: Requirements 4.4, 4.5**

- [x] 3. Backend performance logging
  - [x] 3.1 Add timing instrumentation to `src/pipeline.py`
    - Set `frame_seq_var` before processing each frame
    - Wrap OCR extraction with timing, log frame_seq and duration_ms at DEBUG on completion
    - Wrap engine analysis with timing, log frame_seq and duration_ms at DEBUG on completion
    - Log total frame processing duration (all stages + reorder buffer) at DEBUG
    - Track FPS: log first computed value at INFO, log subsequent values at INFO only when delta exceeds 10% from previously logged value
    - Log timeout events at WARNING with frame_seq and configured timeout value
    - Log stage timeout events at WARNING with stage name and configured stage_timeout_seconds
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 2.2, 2.5_

  - [ ]* 3.2 Write property test for FPS change logging threshold
    - **Property 5: FPS logging triggers on >10% change**
    - **Validates: Requirements 3.4**

- [ ] 4. Checkpoint - Backend verification
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Frontend logging utility
  - [-] 5.1 Create `src/logger.ts` with Logger class and log level management
    - Export `LogLevel` type: `'DEBUG' | 'INFO' | 'WARN' | 'ERROR'`
    - Implement `Logger` class with constructor taking module name (truncated to 64 chars)
    - Implement `debug()`, `info()`, `warn()`, `error()` methods wrapping `console.*`
    - Format output: `[ISO8601] [LEVEL] [module] message` with optional context
    - Include active correlation_id in output when set: `[cid:xxx]`
    - Read initial level: `localStorage.getItem('LOG_LEVEL')` → `import.meta.env.VITE_LOG_LEVEL` → default `WARN`
    - Handle localStorage errors (private browsing) gracefully, fall through to next source
    - Suppress messages below configured threshold per severity order DEBUG < INFO < WARN < ERROR
    - Expose `window.__setLogLevel(level: string)` for runtime changes
    - Reject invalid level values: retain current level, log warning
    - No-op silently if console methods unavailable (SSR)
    - Export `createLogger(module: string): Logger`, `setLogLevel(level: string): void`, `getLogLevel(): LogLevel`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [ ]* 5.2 Write property tests for Frontend Logger
    - **Property 7: Frontend log level filtering suppresses lower-severity messages**
    - **Property 8: Frontend log level resolution follows priority order**
    - **Property 9: Invalid frontend log level change is rejected**
    - **Validates: Requirements 5.3, 5.4, 5.5, 5.7**

- [x] 6. Frontend correlation ID generation
  - [-] 6.1 Create `src/correlation.ts` with correlation ID generation
    - Implement `generateCorrelationId(): string` using `crypto.randomUUID()`
    - Fall back to timestamp-based ID (`ts-{Date.now()}-{random4hex}`) if `crypto.randomUUID` unavailable
    - Log fallback usage at WARN level via logger
    - Ensure each call produces a unique value
    - _Requirements: 7.1, 7.2, 7.4, 7.5_

  - [ ]* 6.2 Write property tests for correlation ID generation
    - **Property 11: Generated correlation IDs are unique UUID v4**
    - **Validates: Requirements 7.1, 7.2, 7.4**

- [ ] 7. Frontend WebSocket logging integration
  - [ ] 7.1 Modify `src/websocket.ts` for WebSocket event logging
    - Create logger instance via `createLogger('websocket')`
    - Log connection state transitions (previous → new) at INFO
    - Log parsed message types at DEBUG
    - Log parse errors with error description and first 200 chars of raw message at WARN
    - Log invalid message structure (validation failure + type or "unknown") at WARN
    - Log reconnection retry attempts (sequential number from 1) at WARN
    - Log command send failures (command action + current connection state) at WARN
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [ ]* 7.2 Write property test for message parse failure logging
    - **Property 10: WebSocket message parse failure logs truncated raw content**
    - **Validates: Requirements 6.4**

- [ ] 8. Frontend command correlation and lifecycle logging
  - [ ] 8.1 Modify `src/pipeline-controls.ts` to attach correlation IDs to commands
    - Import `generateCorrelationId` from `src/correlation.ts`
    - Generate and attach `correlation_id` to every control command payload before sending
    - Log command action and correlation_id at DEBUG
    - Set active correlation_id in logger context between send and response/timeout
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 6.3_

  - [ ] 8.2 Modify `src/state.ts` for component lifecycle logging
    - Create logger instance via `createLogger('state')`
    - Log component initialization at DEBUG with component name
    - Log pipeline status changes (previous → new) at INFO
    - Log telemetry record sequence_number at DEBUG on update applied
    - Log rendering errors at ERROR with component name, error message, and stack trace
    - Log WebSocket error payloads (error code + message) at ERROR
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [ ]* 8.3 Write property test for pipeline status transition logging
    - **Property 12: Pipeline status transitions are logged with both states**
    - **Validates: Requirements 8.2**

- [ ] 9. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- Backend uses Python's `logging` + `contextvars` (stdlib, no new dependencies)
- Frontend uses `console.*` wrappers and `crypto.randomUUID()` (platform APIs, no new dependencies)
- Both `hypothesis` (backend) and `fast-check` (frontend) are already in dev dependencies

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "5.1"] },
    { "id": 1, "tasks": ["1.2", "6.1"] },
    { "id": 2, "tasks": ["1.3", "1.4", "5.2", "6.2"] },
    { "id": 3, "tasks": ["2.1", "3.1", "7.1"] },
    { "id": 4, "tasks": ["2.2", "3.2", "7.2", "8.1"] },
    { "id": 5, "tasks": ["8.2"] },
    { "id": 6, "tasks": ["8.3"] }
  ]
}
```
