# Requirements Document

## Introduction

The telemetry extraction pipeline currently processes frames sequentially at approximately 1 FPS. This feature introduces parallelism at multiple levels to improve throughput: running independent pipeline stages concurrently within a single frame, and processing multiple frames in parallel while preserving output ordering. The design must respect EasyOCR's PyTorch model thread-safety constraints and the existing asyncio-based architecture.

## Glossary

- **Pipeline_Orchestrator**: The coordinating component (`PipelineOrchestrator`) that manages the frame processing lifecycle and wires together all extraction stages.
- **Engine_Analyzer**: The OpenCV-based component that detects circular engine indicators and classifies their status via Hough Circle Detection and HSV color sampling.
- **OCR_Engine**: The EasyOCR-based component that extracts text values (time, speed, altitude, stage labels) from ROI regions using PyTorch inference.
- **Stage_Assigner**: The lightweight component that determines vehicle stage based on OCR results.
- **Record_Assembler**: The component that combines engine analysis, OCR, and stage assignment outputs into a single `TelemetryRecord` with a monotonic sequence number.
- **Frame**: A BGR numpy array (1920x1080) captured from the video source.
- **Frame_Sequence_Number**: A monotonically increasing integer assigned to each captured frame by the Frame Extractor.
- **Output_Ordering**: The requirement that broadcast telemetry records arrive at WebSocket clients in the same order as their source frames were captured.
- **Time_Gate**: The early-exit logic where if the "time" OCR field is unavailable, all remaining OCR fields are skipped.
- **Intra_Frame_Parallelism**: Concurrent execution of independent stages (Engine Analysis and OCR) within a single frame's processing.
- **Inter_Frame_Parallelism**: Concurrent processing of multiple frames through the pipeline simultaneously.
- **Concurrency_Limit**: The maximum number of frames being processed concurrently at any given time.
- **Thread_Pool_Executor**: A Python `concurrent.futures.ThreadPoolExecutor` used to offload CPU-bound work from the asyncio event loop.

## Requirements

### Requirement 1: Intra-Frame Concurrent Execution of Independent Stages

**User Story:** As a user watching live telemetry, I want engine analysis and OCR extraction to run concurrently for each frame, so that single-frame processing time is reduced.

#### Acceptance Criteria

1. WHEN a Frame is received for processing, THE Pipeline_Orchestrator SHALL execute Engine_Analyzer and OCR_Engine concurrently such that their execution timespans overlap, and the total wall-clock time for both stages is less than the sum of their individual execution times.
2. WHEN both Engine_Analyzer and OCR_Engine complete for a Frame, THE Pipeline_Orchestrator SHALL pass their combined results (EngineAnalysisResult and OCRResult) to Stage_Assigner and Record_Assembler in that order.
3. IF Engine_Analyzer raises an exception during concurrent execution, THEN THE Pipeline_Orchestrator SHALL log the error and produce a default EngineAnalysisResult with all engines marked UNDETECTED and detection_accuracy of 0.0, while still using the OCR_Engine result for downstream stages.
4. IF OCR_Engine raises an exception during concurrent execution, THEN THE Pipeline_Orchestrator SHALL log the error and produce a default OCRResult with all fields set to UNAVAILABLE status and null parsed values, while still using the Engine_Analyzer result for downstream stages.
5. IF both Engine_Analyzer and OCR_Engine raise exceptions for the same Frame, THEN THE Pipeline_Orchestrator SHALL log both errors and skip further processing for that Frame without crashing the pipeline.
6. IF either Engine_Analyzer or OCR_Engine has not completed within 30 seconds, THEN THE Pipeline_Orchestrator SHALL cancel the pending task, log a timeout error, and treat the timed-out stage as having raised an exception (applying criterion 3 or 4 respectively).

### Requirement 2: Offload CPU-Bound Work to Thread Pool

**User Story:** As a developer, I want CPU-bound OpenCV and PyTorch operations offloaded from the asyncio event loop, so that the event loop remains responsive for WebSocket communication and frame capture.

#### Acceptance Criteria

1. THE Pipeline_Orchestrator SHALL execute Engine_Analyzer calls via asyncio `loop.run_in_executor` using the pipeline's Thread_Pool_Executor, so that the calling coroutine yields control to the event loop during execution.
2. THE Pipeline_Orchestrator SHALL execute OCR_Engine calls via asyncio `loop.run_in_executor` using the pipeline's Thread_Pool_Executor, so that the calling coroutine yields control to the event loop during execution.
3. WHEN the pipeline is started, THE Pipeline_Orchestrator SHALL create a Thread_Pool_Executor with a configurable number of worker threads defaulting to 4, accepting values between 1 and 8 inclusive.
4. WHEN the pipeline is stopped, THE Pipeline_Orchestrator SHALL call `shutdown(wait=True)` on the Thread_Pool_Executor, blocking until all previously submitted tasks complete or until a maximum of 10 seconds elapse, and then release the executor reference.
5. IF an Engine_Analyzer or OCR_Engine call raises an exception inside the Thread_Pool_Executor, THEN THE Pipeline_Orchestrator SHALL propagate the exception to the calling coroutine and log the error without terminating the pipeline.

### Requirement 3: Inter-Frame Pipeline Parallelism

**User Story:** As a user watching live telemetry, I want multiple frames to be processed concurrently, so that overall pipeline throughput increases beyond what single-frame optimization alone can achieve.

#### Acceptance Criteria

1. WHEN a new Frame arrives from the Frame Extractor, THE Pipeline_Orchestrator SHALL dispatch the Frame for concurrent processing without waiting for previous frames to complete, provided the number of in-flight frames is below the Concurrency_Limit.
2. WHILE the number of in-flight frames equals the Concurrency_Limit, THE Pipeline_Orchestrator SHALL discard newly arriving frames and log a warning that includes the Frame_Sequence_Number of the discarded frame.
3. THE Pipeline_Orchestrator SHALL provide a configurable Concurrency_Limit with a default value of 3, accepting integer values from 1 to 10 inclusive.
4. WHEN a dispatched Frame's processing task completes or is cancelled, THE Pipeline_Orchestrator SHALL decrement the in-flight frame count, making capacity available for the next arriving Frame.
5. IF the Concurrency_Limit is set to a value outside the range of 1 to 10, THEN THE Pipeline_Orchestrator SHALL reject the configuration and retain the previous Concurrency_Limit value.

### Requirement 4: Output Ordering Preservation

**User Story:** As a user watching live telemetry, I want telemetry records to be broadcast to WebSocket clients in the same order as the source frames were captured, so that the data stream is coherent and monotonically progresses in time.

#### Acceptance Criteria

1. THE Pipeline_Orchestrator SHALL assign each Frame a Frame_Sequence_Number starting at 1 and incrementing by 1 before dispatching it for concurrent processing.
2. WHEN a Frame completes processing out of order, THE Pipeline_Orchestrator SHALL buffer the result until all preceding frames have been broadcast, up to a maximum of 120 buffered results.
3. WHEN the next expected Frame_Sequence_Number result becomes available in the buffer, THE Pipeline_Orchestrator SHALL broadcast the result within 50 milliseconds and continue draining any consecutively buffered results in sequence.
4. THE Record_Assembler SHALL assign monotonically increasing sequence numbers to TelemetryRecords in broadcast order rather than completion order, starting at 1 and incrementing by 1.
5. IF a buffered Frame result is not broadcast within 5 seconds because a preceding frame has not completed processing, THEN THE Pipeline_Orchestrator SHALL discard the stalled preceding frame, advance the expected sequence number past the gap, and resume broadcasting from the next available buffered result.
6. IF the reorder buffer reaches its maximum capacity of 120 entries, THEN THE Pipeline_Orchestrator SHALL discard the oldest pending gap, advance the expected sequence number, and drain all consecutively available results to free buffer space.

### Requirement 5: Time Gate Optimization Across Parallel Frames

**User Story:** As a developer, I want the T-0 detection and time gate logic to remain correct under concurrent frame processing, so that frames arriving before T-0 are still efficiently skipped.

#### Acceptance Criteria

1. WHILE T-0 has not been detected, THE Pipeline_Orchestrator SHALL process frames through the OCR_Engine sequentially, one at a time, in capture-sequence order, so that T-0 detection reflects the true first occurrence of "00:00:00" in the video stream.
2. WHEN T-0 is detected on a frame, THE Pipeline_Orchestrator SHALL enable inter-frame parallelism for all subsequently captured frames, allowing up to Concurrency_Limit frames to be processed concurrently through the OCR_Engine.
3. THE OCR_Engine SHALL guarantee that the T-0 detection state transition (from not-detected to detected) occurs exactly once, even when extract_text is invoked concurrently from multiple threads.
4. IF two or more threads read the T-0 detection state as not-detected before any thread sets it to detected, THEN THE OCR_Engine SHALL ensure that only one thread performs the state transition and subsequent threads observe the updated state before completing their own extract_text call.
5. WHEN T-0 is detected and frames that were captured before the T-0 frame are still queued for processing, THE Pipeline_Orchestrator SHALL skip those pre-T-0 queued frames without sending them through OCR extraction.

### Requirement 6: Processing Metrics and Observability

**User Story:** As a user, I want accurate FPS reporting that reflects actual parallel throughput, so that I can see the improvement from parallelization.

#### Acceptance Criteria

1. THE Pipeline_Orchestrator SHALL measure processing FPS as the number of successfully broadcast telemetry records divided by the elapsed wall-clock time over a sliding window of the most recent 10 broadcast events, rounded to 2 decimal places.
2. WHEN inter-frame parallelism is enabled (after T-0 detection), THE Pipeline_Orchestrator SHALL compute FPS from the broadcast output rate of ordered records rather than from individual frame processing duration.
3. THE Pipeline_Orchestrator SHALL include the current number of in-flight frames (integer, 0 to the configured Concurrency_Limit) in the status payload broadcast to WebSocket clients.
4. IF no telemetry records have been broadcast since the pipeline started, THEN THE Pipeline_Orchestrator SHALL report processing FPS as 0.0.

### Requirement 7: Non-Interference with Event Loop Services

**User Story:** As a user, I want the parallel processing pipeline to coexist with the WebSocket server, frame capture, and frontend serving without degrading their responsiveness, so that the UI remains interactive and frames continue arriving smoothly.

#### Acceptance Criteria

1. THE Pipeline_Orchestrator SHALL execute all CPU-bound work (OpenCV operations, PyTorch inference, JPEG encoding) exclusively in the Thread_Pool_Executor via `run_in_executor`, such that no individual call blocks the asyncio event loop for more than 10 milliseconds.
2. WHILE parallel frame processing is active, THE Pipeline_Orchestrator SHALL ensure that WebSocket broadcast calls, frame capture reads, and HTTP request handling execute on the asyncio event loop with no single event loop iteration delayed by more than 50 milliseconds due to processing tasks.
3. THE Thread_Pool_Executor SHALL use a configurable number of worker threads with a default of 4 and an allowable range of 1 to 8, to limit CPU contention with the frame capture thread and OS-level network I/O.
4. WHEN JPEG encoding is performed for the frame preview broadcast, THE Pipeline_Orchestrator SHALL execute the encoding in the Thread_Pool_Executor rather than on the asyncio event loop.
5. IF a CPU-bound task submitted to the Thread_Pool_Executor raises an exception, THEN THE Pipeline_Orchestrator SHALL catch the exception on the asyncio event loop, log the error, and continue processing subsequent frames without crashing the event loop.

### Requirement 8: Graceful Degradation Under Resource Pressure

**User Story:** As a user running on limited hardware, I want the pipeline to degrade gracefully rather than crash when system resources are constrained.

#### Acceptance Criteria

1. IF a thread pool task exceeds 10 seconds without completing, THEN THE Pipeline_Orchestrator SHALL cancel the task, log a timeout warning, advance the expected sequence number past that frame's Frame_Sequence_Number, and continue processing subsequent frames.
2. IF the result buffer for output ordering grows beyond 2 times the Concurrency_Limit, THEN THE Pipeline_Orchestrator SHALL repeatedly discard the oldest buffered frame and advance the expected sequence number until the buffer size is at most equal to the Concurrency_Limit, logging a warning for each discarded frame.
3. WHEN the pipeline is stopped, THE Pipeline_Orchestrator SHALL cancel all in-flight frame processing tasks and clear the result buffer within 5 seconds.
4. IF in-flight frame processing tasks have not terminated within 5 seconds of a stop request, THEN THE Pipeline_Orchestrator SHALL abandon the remaining tasks, release the Thread_Pool_Executor, and log an error indicating the count of un-terminated tasks.
