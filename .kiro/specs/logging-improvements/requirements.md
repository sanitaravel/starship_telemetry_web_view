# Requirements Document

## Introduction

This feature introduces a comprehensive, structured logging system across both the backend (Python/FastAPI) and frontend (TypeScript/Vite) layers of the Starship Telemetry Web View application. The goal is to provide centralized logging configuration, structured output, correlation between frontend and backend operations, configurable log levels, and performance-relevant instrumentation for telemetry extraction pipeline diagnostics.

## Glossary

- **Backend_Logger**: The centralized Python logging configuration module that initializes structured logging for all backend modules.
- **Frontend_Logger**: The TypeScript logging utility class that provides leveled, contextual logging for all frontend modules.
- **Structured_Log_Entry**: A JSON-formatted log record containing timestamp, level, module, message, and optional contextual fields (correlation_id, duration_ms, etc.).
- **Correlation_ID**: A unique identifier generated per WebSocket session or user action that links frontend-initiated requests to backend processing operations.
- **Log_Level**: A severity classification for log entries. Backend uses Python standard levels (DEBUG, INFO, WARNING, ERROR, CRITICAL). Frontend uses (DEBUG, INFO, WARN, ERROR).
- **Pipeline_Orchestrator**: The backend component that coordinates frame extraction, OCR, engine analysis, and telemetry broadcast.
- **Dashboard**: The frontend single-page application that displays real-time telemetry data.

## Requirements

### Requirement 1: Backend Centralized Logging Configuration

**User Story:** As a developer, I want a single place to configure backend logging, so that all modules use consistent formatting and output settings without scattered configuration.

#### Acceptance Criteria

1. THE Backend_Logger SHALL provide a single configuration function that, when called, configures the root Python logger and all descendant loggers in the application to use the same formatter and output handler.
2. WHEN the backend application starts, THE Backend_Logger SHALL initialize logging before any HTTP or WebSocket request is processed.
3. THE Backend_Logger SHALL output each Structured_Log_Entry as a single-line JSON object to stdout, with one JSON object per line (newline-delimited).
4. WHEN the LOG_LEVEL environment variable is set to a valid Python log level (DEBUG, INFO, WARNING, ERROR, or CRITICAL, case-insensitive), THE Backend_Logger SHALL configure the root logger to the specified level.
5. IF the LOG_LEVEL environment variable is not set, THEN THE Backend_Logger SHALL default to INFO level.
6. THE Backend_Logger SHALL include the following fields in every Structured_Log_Entry: timestamp (ISO 8601 in UTC with timezone designator), level, logger_name, message.
7. IF the LOG_LEVEL environment variable is set to a value that is not one of DEBUG, INFO, WARNING, ERROR, or CRITICAL, THEN THE Backend_Logger SHALL fall back to INFO level and emit a WARNING-level log entry indicating the invalid value was ignored.

### Requirement 2: Backend Structured Log Fields

**User Story:** As a developer, I want structured log entries with contextual fields, so that I can filter and search logs efficiently during debugging and monitoring.

#### Acceptance Criteria

1. WHEN a log entry is produced during WebSocket message processing and a Correlation_ID is available, THE Backend_Logger SHALL include the Correlation_ID field in the Structured_Log_Entry. IF no Correlation_ID is available, THEN the field SHALL be omitted from the entry.
2. WHEN a log entry is produced during frame processing, THE Backend_Logger SHALL include the frame sequence number as an integer field named "frame_seq" in the Structured_Log_Entry.
3. THE Backend_Logger SHALL include a "module" field in every Structured_Log_Entry whose value is the Python logger name (e.g., "src.pipeline", "src.frame_extractor").
4. WHEN an exception is logged, THE Backend_Logger SHALL include the exception type as a string field named "exc_type" and the full traceback as a string field named "exc_traceback" in the Structured_Log_Entry.
5. WHEN frame processing occurs within a WebSocket session context, THE Backend_Logger SHALL include both the Correlation_ID and the frame_seq fields in the same Structured_Log_Entry.

### Requirement 3: Backend Performance Logging

**User Story:** As a developer, I want timing information logged for key pipeline stages, so that I can identify bottlenecks in telemetry extraction.

#### Acceptance Criteria

1. WHEN a frame completes OCR extraction without timeout, THE Pipeline_Orchestrator SHALL log the frame sequence number and the OCR stage duration as an integer number of milliseconds at DEBUG level.
2. WHEN a frame completes engine analysis without timeout, THE Pipeline_Orchestrator SHALL log the frame sequence number and the engine analysis duration as an integer number of milliseconds at DEBUG level.
3. WHEN a frame completes full pipeline processing (all stages and reorder buffer submission), THE Pipeline_Orchestrator SHALL log the frame sequence number and the total processing duration as an integer number of milliseconds at DEBUG level.
4. WHEN the pipeline processing FPS changes by more than 10 percent from the previously logged FPS value, THE Pipeline_Orchestrator SHALL log the new FPS value at INFO level. The first FPS value computed after pipeline start SHALL always be logged.
5. WHEN a frame-level processing operation exceeds frame_timeout_seconds, THE Pipeline_Orchestrator SHALL log the timeout event including the frame sequence number and the configured frame_timeout_seconds value at WARNING level.
6. WHEN an individual stage (OCR extraction or engine analysis) exceeds stage_timeout_seconds, THE Pipeline_Orchestrator SHALL log the timeout event including the stage name and the configured stage_timeout_seconds value at WARNING level.

### Requirement 4: Backend Correlation ID Propagation

**User Story:** As a developer, I want each WebSocket session to carry a correlation ID, so that I can trace a client action through all backend processing steps.

#### Acceptance Criteria

1. WHEN a new WebSocket connection is established, THE Backend_Logger SHALL generate a UUID v4 Correlation_ID and associate it with that session.
2. WHILE a WebSocket session is active, THE Backend_Logger SHALL attach the session Correlation_ID to all log entries produced by request handlers and pipeline operations initiated by that session.
3. WHEN a control command is received from a WebSocket client, THE Backend_Logger SHALL log the command type and Correlation_ID at INFO level.
4. IF a client sends a Correlation_ID in the command payload and the value is a valid UUID v4 string, THEN THE Backend_Logger SHALL use the client-provided Correlation_ID instead of the server-generated one for subsequent log entries within that command's processing.
5. IF a client sends a Correlation_ID in the command payload that is not a valid UUID v4 string, THEN THE Backend_Logger SHALL ignore the invalid value, retain the server-generated Correlation_ID, and log a WARNING indicating the rejected client Correlation_ID.
6. WHEN a WebSocket session disconnects, THE Backend_Logger SHALL log the session Correlation_ID and the connection duration in milliseconds at INFO level.

### Requirement 5: Frontend Logging Utility

**User Story:** As a frontend developer, I want a logging utility with configurable levels and module context, so that I can add diagnostic logging without polluting the console in production.

#### Acceptance Criteria

1. THE Frontend_Logger SHALL provide methods for each Log_Level: debug, info, warn, error.
2. THE Frontend_Logger SHALL prefix each log message with a timestamp in ISO 8601 format and the module name (1 to 64 characters) that created the logger instance.
3. WHEN the configured log level is higher than the message level according to the severity order DEBUG < INFO < WARN < ERROR, THE Frontend_Logger SHALL suppress the message by not writing it to the browser console.
4. THE Frontend_Logger SHALL read the initial log level from localStorage key "LOG_LEVEL" first; IF that key is not present, THEN THE Frontend_Logger SHALL read from the build-time environment variable; the first source found with a valid value SHALL be used.
5. IF no log level configuration is found in any source, THEN THE Frontend_Logger SHALL default to WARN level.
6. THE Frontend_Logger SHALL allow runtime log level changes via a global function exposed on the window object (window.__setLogLevel) that accepts a single string argument.
7. IF an invalid log level value is provided (not one of "DEBUG", "INFO", "WARN", "ERROR" case-insensitive), THEN THE Frontend_Logger SHALL ignore the invalid value, retain the current log level, and log a warning message indicating the rejected value.

### Requirement 6: Frontend WebSocket Logging

**User Story:** As a frontend developer, I want WebSocket connection events and message flow logged, so that I can diagnose connectivity issues.

#### Acceptance Criteria

1. WHEN the WebSocket connection state changes, THE Frontend_Logger SHALL log the previous state and the new state at INFO level.
2. WHEN a WebSocket message is successfully parsed, THE Frontend_Logger SHALL log the message type at DEBUG level.
3. WHEN a control command is sent to the backend, THE Frontend_Logger SHALL log the command action and Correlation_ID at DEBUG level.
4. IF a WebSocket message cannot be parsed as valid JSON, THEN THE Frontend_Logger SHALL log the parse error description and the first 200 characters of the raw message at WARN level.
5. IF a WebSocket message is valid JSON but does not conform to the expected message structure, THEN THE Frontend_Logger SHALL log a validation failure description and the message type field (or "unknown" if absent) at WARN level.
6. WHEN the WebSocket reconnection mechanism initiates a retry attempt, THE Frontend_Logger SHALL log the sequential attempt number starting from 1 at WARN level.
7. IF a control command cannot be sent because the WebSocket connection state is not "connected", THEN THE Frontend_Logger SHALL log the command action and current connection state at WARN level.

### Requirement 7: Frontend Correlation ID Generation

**User Story:** As a developer, I want the frontend to generate and attach correlation IDs to commands, so that I can trace user actions from browser to backend.

#### Acceptance Criteria

1. WHEN the Dashboard sends a control command, THE Dashboard SHALL generate a Correlation_ID and include it in the command payload.
2. THE Dashboard SHALL generate Correlation_IDs as UUID v4 strings in RFC 4122 lowercase hyphenated format (xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx).
3. THE Frontend_Logger SHALL include the active Correlation_ID in all log entries produced between command send and command response or timeout.
4. Each control command SHALL receive a unique Correlation_ID; previously generated IDs SHALL NOT be reused for subsequent commands.
5. IF Correlation_ID generation fails (e.g., crypto.randomUUID unavailable), THEN THE Dashboard SHALL fall back to a timestamp-based identifier and THE Frontend_Logger SHALL log the fallback at WARN level.

### Requirement 8: Frontend Component Lifecycle Logging

**User Story:** As a frontend developer, I want component initialization and key state changes logged, so that I can trace application behavior during debugging.

#### Acceptance Criteria

1. WHEN a UI component initializes, THE Frontend_Logger SHALL log the component name and a message indicating initialization at DEBUG level.
2. WHEN the pipeline status changes to any of the defined PipelineStatus values (running, stopped, disconnected, reconnecting), THE Frontend_Logger SHALL log the previous status and the new status at INFO level.
3. WHEN a telemetry update is received and applied to the display, THE Frontend_Logger SHALL log the telemetry record sequence_number at DEBUG level.
4. IF an error occurs during component rendering, THEN THE Frontend_Logger SHALL log the component name, the error message string, and the error stack trace (if available) at ERROR level.
5. WHEN a WebSocket error payload is received, THE Frontend_Logger SHALL log the error code and error message from the payload at ERROR level.
