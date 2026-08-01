# Implementation Plan: Pipeline Parallelization

## Overview

This plan implements parallelism in the `PipelineOrchestrator` at two levels: intra-frame (Engine Analyzer + OCR run concurrently via `asyncio.gather`) and inter-frame (multiple frames processed simultaneously with semaphore-based admission control and a reorder buffer for output ordering). CPU-bound work is offloaded to a `ThreadPoolExecutor`, and a T-0 detection gate controls when inter-frame parallelism activates.

## Tasks

- [x] 1. Create configuration and data model foundations
  - [x] 1.1 Create `ParallelPipelineConfig` dataclass and `FPSMeter` utility
    - Add `ParallelPipelineConfig` with fields: `executor_max_workers`, `concurrency_limit`, `stage_timeout_seconds`, `frame_timeout_seconds`, `max_buffer_size`, `stale_gap_timeout_seconds`, `shutdown_timeout_seconds`, `buffer_overflow_threshold`
    - Implement `validate()` method returning list of error strings for out-of-range values
    - Add `FPSMeter` dataclass with `record_broadcast(timestamp)` and `get_fps()` methods using a sliding window of 10 entries
    - _Requirements: 2.3, 3.3, 6.1, 6.4_

  - [x] 1.2 Write property test for configuration validation (Property 2)
    - **Property 2: Configuration Parameter Validation**
    - Generate integers across wide range, verify `executor_max_workers` accepted iff in [1, 8] and `concurrency_limit` accepted iff in [1, 10]
    - **Validates: Requirements 2.3, 3.3, 3.5, 7.3**

  - [x] 1.3 Write property test for FPS sliding window calculation (Property 11)
    - **Property 11: FPS Sliding Window Calculation**
    - Generate timestamp sequences, verify FPS equals `(count - 1) / (last - first)` over most recent 10 entries, rounded to 2 decimal places; 0.0 if fewer than 2 broadcasts
    - **Validates: Requirements 6.1, 6.4**

- [ ] 2. Implement ReorderBuffer component
  - [ ] 2.1 Create `ReorderBuffer` class with `BufferedResult` dataclass
    - Implement `insert(seq, result)` to store completed results keyed by sequence number
    - Implement `drain()` to return all consecutively available results starting from `next_expected` and advance the pointer
    - Implement `advance_past_gap(gap_seq)` to skip a stalled frame
    - Implement `discard_oldest_gap()` to remove oldest pending gap when buffer overflows
    - Add `size` and `next_expected` properties
    - Enforce `max_size` of 120 entries
    - _Requirements: 4.2, 4.3, 4.5, 4.6_

  - [ ] 2.2 Write property test for reorder buffer ordering (Property 7)
    - **Property 7: Reorder Buffer Preserves Capture Order**
    - Generate permutations of completion order, verify output is always in strictly ascending sequence number order
    - **Validates: Requirements 4.2, 4.3, 4.4**

  - [ ] 2.3 Write property test for buffer overflow handling (Property 8)
    - **Property 8: Buffer Overflow Triggers Discard and Drain**
    - Generate buffer states at/above capacity, verify discard of oldest gap and drain behavior
    - **Validates: Requirements 4.6, 8.2**

  - [ ] 2.4 Write property test for frame sequence contiguity (Property 6)
    - **Property 6: Frame Sequence Number Contiguity**
    - Generate N frames dispatched, verify assigned sequence numbers form contiguous [1..N]
    - **Validates: Requirements 4.1**

- [ ] 3. Implement ConcurrencyController component
  - [ ] 3.1 Create `ConcurrencyController` class
    - Implement `try_acquire()` as non-blocking slot acquisition returning bool
    - Implement `release()` to free a processing slot
    - Implement `set_limit(new_limit)` with validation for range [1, 10]
    - Add `in_flight` and `limit` properties
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ] 3.2 Write property test for admission control invariant (Property 4)
    - **Property 4: Admission Control Invariant**
    - Generate in-flight counts and limits, verify frame dispatched when in_flight < limit and discarded when in_flight == limit
    - **Validates: Requirements 3.1, 3.2**

  - [ ] 3.3 Write property test for in-flight count conservation (Property 5)
    - **Property 5: In-Flight Count Conservation**
    - Generate sequences of dispatch/complete/cancel events, verify count == dispatched - completed and never negative or exceeds limit
    - **Validates: Requirements 3.4**

- [ ] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Add thread-safety to EasyOCREngine for T-0 detection
  - [ ] 5.1 Modify `EasyOCREngine` with `threading.Lock` for T-0 state
    - Add `self._t_zero_lock = threading.Lock()` to `__init__`
    - Wrap T-0 state reads and writes in `extract_text` with double-check locking pattern
    - Ensure exactly-once state transition from not-detected to detected under concurrent access
    - _Requirements: 5.3, 5.4_

  - [ ]* 5.2 Write property test for T-0 exactly-once transition (Property 10)
    - **Property 10: T-0 Exactly-Once State Transition**
    - Generate concurrent thread counts (2-8), use `threading.Barrier` to synchronize, verify state transition occurs exactly once
    - **Validates: Requirements 5.3, 5.4**

- [ ] 6. Implement intra-frame parallel execution in PipelineOrchestrator
  - [ ] 6.1 Add ThreadPoolExecutor lifecycle to `start()` and `stop()`
    - Create `ThreadPoolExecutor(max_workers=config.executor_max_workers)` in `start()`
    - Call `shutdown(wait=True, cancel_futures=True)` in `stop()` with 10-second timeout
    - Store executor reference and release on stop
    - _Requirements: 2.3, 2.4_

  - [ ] 6.2 Implement `_run_engine_analysis`, `_run_ocr_extraction`, and `_run_jpeg_encoding` methods
    - Each method wraps the CPU-bound call in `loop.run_in_executor(self._executor, ...)`
    - Add `asyncio.wait_for` with `stage_timeout_seconds` (30s default) for engine and OCR
    - Handle `asyncio.TimeoutError` by logging and returning default results
    - _Requirements: 2.1, 2.2, 1.6, 7.1, 7.4_

  - [ ] 6.3 Implement `_process_frame_parallel` with `asyncio.gather` for intra-frame concurrency
    - Use `asyncio.gather(_run_engine_analysis(frame), _run_ocr_extraction(frame), return_exceptions=True)` 
    - Handle exception results: produce default `EngineAnalysisResult` or default `OCRResult` as needed
    - If both stages fail, skip frame without crashing
    - Pass combined results to `StageAssigner` then `RecordAssembler`
    - Execute JPEG encoding concurrently via `_run_jpeg_encoding`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ] 6.4 Write property test for fault isolation (Property 1)
    - **Property 1: Fault Isolation Between Parallel Stages**
    - Mock one stage to raise, verify other stage's result preserved unchanged in final record
    - **Validates: Requirements 1.3, 1.4**

  - [ ] 6.5 Write property test for exception resilience (Property 3)
    - **Property 3: Exception Resilience in Thread Pool**
    - Generate exception types, verify pipeline continues processing subsequent frames without crash
    - **Validates: Requirements 2.5, 7.5**

- [ ] 7. Implement inter-frame parallelism with dispatch and T-0 gate
  - [ ] 7.1 Implement `_on_frame` dispatch logic with T-0 gate
    - Assign `Frame_Sequence_Number` starting at 1, incrementing by 1
    - If T-0 not detected: call `_process_frame_sequential`
    - If T-0 detected and `ConcurrencyController.try_acquire()` succeeds: spawn `_process_frame_parallel` as async task
    - If at capacity: discard frame and log warning with sequence number
    - On T-0 detection: set `_t_zero_detected = True`, enable parallel mode
    - _Requirements: 3.1, 3.2, 4.1, 5.1, 5.2_

  - [ ] 7.2 Implement `_process_frame_sequential` for pre-T-0 processing
    - Process frames one at a time in capture order
    - Use same intra-frame parallelism (engine + OCR concurrent) but no inter-frame overlap
    - Submit results directly to reorder buffer
    - Release concurrency slot on completion
    - _Requirements: 5.1, 1.1_

  - [ ] 7.3 Write property test for sequential processing before T-0 (Property 9)
    - **Property 9: Sequential Processing Before T-0**
    - Generate frame arrival sequences, verify no two frames have overlapping processing timespans before T-0
    - **Validates: Requirements 5.1**

- [ ] 8. Wire reorder buffer and broadcast ordering
  - [ ] 8.1 Integrate `ReorderBuffer` into `PipelineOrchestrator`
    - Implement `_submit_to_reorder_buffer(seq, record)` to insert and trigger drain
    - Implement `_drain_reorder_buffer()` to broadcast consecutive results via WebSocket
    - Assign monotonic broadcast sequence numbers during drain (not at assembly time)
    - Record broadcast timestamps in `FPSMeter`
    - _Requirements: 4.2, 4.3, 4.4, 6.1, 6.2_

  - [ ] 8.2 Implement stale gap handling and buffer overflow logic
    - If a gap is pending for >5 seconds, advance past it and drain
    - If buffer exceeds max capacity (120) or 2× concurrency_limit, discard oldest gaps until within limits
    - Log warnings for each discarded frame
    - _Requirements: 4.5, 4.6, 8.2_

  - [ ] 8.3 Write property test for timeout advances expected sequence (Property 12)
    - **Property 12: Timeout Advances Expected Sequence**
    - Generate timeout scenarios, verify task cancelled and expected sequence advanced past timed-out frame
    - **Validates: Requirements 8.1**

- [ ] 9. Update PipelineState and WebSocket status payload
  - [ ] 9.1 Add parallel processing fields to `PipelineState` and status payload
    - Add `in_flight_frames`, `t_zero_detected`, `parallel_mode_active`, `frames_discarded`, `buffer_size` to `PipelineState`
    - Update `build_status_payload()` to include `in_flight_frames`, `parallel_mode_active`, `frames_discarded`
    - Compute and include `processing_fps` from `FPSMeter.get_fps()`
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [ ] 10. Implement graceful shutdown and degradation
  - [ ] 10.1 Implement graceful stop with task cancellation and timeout
    - On `stop()`: cancel all in-flight frame tasks, clear reorder buffer
    - Wait up to 5 seconds for tasks to terminate
    - If tasks not terminated in 5 seconds, abandon and log error with count of un-terminated tasks
    - Shutdown executor with 10-second timeout
    - _Requirements: 2.4, 8.3, 8.4_

  - [ ] 10.2 Implement frame-level timeout handling (10s)
    - Wrap each frame processing task in `asyncio.wait_for(timeout=frame_timeout_seconds)`
    - On timeout: cancel task, advance expected sequence past that frame, release concurrency slot
    - Log timeout warning
    - _Requirements: 8.1_

- [ ] 11. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 12. Integration wiring and final validation
  - [ ] 12.1 Wire all components into `PipelineOrchestrator.__init__` and `start()`
    - Instantiate `ParallelPipelineConfig`, `ConcurrencyController`, `ReorderBuffer`, `FPSMeter`
    - Connect frame extractor callback to `_on_frame`
    - Ensure skip_frames logic still applies before dispatching to parallel pipeline
    - Replace sequential `_process_frame` with new dispatch logic
    - _Requirements: 1.1, 2.3, 3.1, 3.3_

  - [ ] 12.2 Write integration tests for concurrent execution
    - Verify wall-clock overlap of engine analysis and OCR execution
    - Test stale gap timeout with mocked time
    - Test graceful shutdown under load (cancel in-flight tasks)
    - Verify event loop responsiveness during parallel processing
    - _Requirements: 1.1, 7.2, 8.3_

- [ ] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The project uses Python with asyncio, Hypothesis for property-based testing, and pytest as the test runner
- CPU-bound stages (OpenCV, PyTorch, JPEG encoding) run in ThreadPoolExecutor; all other logic remains on the asyncio event loop

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.2", "2.3", "2.4", "3.2", "3.3", "5.1"] },
    { "id": 2, "tasks": ["5.2", "6.1", "6.2"] },
    { "id": 3, "tasks": ["6.3", "6.4", "6.5"] },
    { "id": 4, "tasks": ["7.1", "7.2"] },
    { "id": 5, "tasks": ["7.3", "8.1", "8.2"] },
    { "id": 6, "tasks": ["8.3", "9.1", "10.1", "10.2"] },
    { "id": 7, "tasks": ["12.1"] },
    { "id": 8, "tasks": ["12.2"] }
  ]
}
```
