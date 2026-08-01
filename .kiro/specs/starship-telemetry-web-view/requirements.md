# Requirements Document

## Introduction

This system extracts live telemetry data from SpaceX Starship livestream video frames using an SVG-based Region of Interest (ROI) template and displays the extracted data in a styled web dashboard. The SVG template defines bounding regions for telemetry elements (time, speed, altitude, engine status, stage labels) on a 1920×1080 frame. The system performs OCR and image analysis on each frame to read values from those regions, then presents the telemetry in real time via a web interface. The architecture supports multiple templates for different rocket configurations.

## Glossary

- **ROI_Template**: An SVG file (1920×1080 viewBox) that defines named Regions of Interest as rectangles and circles overlaid on a reference frame layout. Each named group or element corresponds to a telemetry data field.
- **Frame_Extractor**: The component that captures individual video frames from a livestream source at a configured interval.
- **OCR_Engine**: The component that performs Optical Character Recognition on cropped frame regions to extract text values (time, speed, altitude, stage labels).
- **Engine_Analyzer**: The component that analyzes engine indicator circles within a frame region to determine engine status based on pixel color.
- **Telemetry_Record**: A structured data object containing all extracted telemetry values from a single frame (timestamp, speeds, altitudes, engine statuses, stage labels, separation text).
- **Dashboard**: The web-based user interface that displays telemetry data in real time using the specified design tokens (JetBrains Mono, #262626 background, #FEFEFE text, #FF8014 accent).
- **Template_Registry**: The component that manages available ROI templates and allows selection of the active template for extraction.
- **Stage_Assignment**: The logic that determines which vehicle (Super Heavy or Starship) each set of telemetry values belongs to, based on the stage separation state and stage label ROI content.
- **Engine_Status**: The state of a single engine indicator, derived from circle detection and color analysis. Possible values: active (high brightness/saturation), inactive (low brightness), or undetected (no circle found at expected position).

## Requirements

### Requirement 1: SVG Template Parsing

**User Story:** As a developer, I want the system to parse an SVG ROI template file, so that the extraction pipeline knows which frame regions to analyze.

#### Acceptance Criteria

1. WHEN an SVG ROI template file is loaded, THE ROI_Template parser SHALL extract all named regions (groups and rectangles) with their bounding box coordinates (x, y, width, height) relative to a 1920×1080 coordinate space.
2. WHEN an SVG ROI template contains circle elements within an engine group, THE ROI_Template parser SHALL extract each circle's center coordinates (cx, cy) and radius (r).
3. IF the SVG file is malformed or missing required named regions, THEN THE ROI_Template parser SHALL return a descriptive error indicating which regions are missing or invalid.
4. THE ROI_Template parser SHALL produce a structured ROI configuration object from a valid SVG file.
5. FOR ALL valid ROI configuration objects, parsing the SVG then serializing then parsing the result SHALL produce an equivalent configuration object (round-trip property).

### Requirement 2: Frame Extraction from Video Source

**User Story:** As a user, I want the system to capture frames from a livestream video source, so that telemetry can be extracted continuously.

#### Acceptance Criteria

1. WHEN the user provides a video source URL via the Dashboard, THE Frame_Extractor SHALL validate that the livestream is active and reachable.
2. IF the livestream is not active or unreachable, THEN THE Frame_Extractor SHALL inform the user via the Dashboard with a descriptive status message.
3. WHEN the livestream is validated as active, THE Frame_Extractor SHALL remain in a stopped state until the user explicitly starts the extraction pipeline via the Dashboard.
4. WHEN the user starts the extraction pipeline, THE Frame_Extractor SHALL capture frames at a configurable interval (default: 1 frame per second).
5. WHILE the extraction pipeline is running and the video source is active, THE Frame_Extractor SHALL continuously produce frames for processing.
6. WHEN the user stops the extraction pipeline via the Dashboard, THE Frame_Extractor SHALL cease frame capture immediately.
7. IF the video source becomes unavailable while the pipeline is running, THEN THE Frame_Extractor SHALL retry connection with exponential backoff and report the disconnection status to the Dashboard.
8. THE Frame_Extractor SHALL produce frames with a resolution of 1920×1080 pixels regardless of the source resolution.

### Requirement 3: Engine Status Detection

**User Story:** As a user, I want the system to determine the ignition status of each engine from the frame, so that I can monitor engine health during flight.

#### Acceptance Criteria

1. WHEN a frame is captured, THE Engine_Analyzer SHALL run before the OCR_Engine to establish which regions contain visible engine indicators.
2. THE Engine_Analyzer SHALL crop the frame to the engine group bounding box as defined by the ROI_Template.
3. THE Engine_Analyzer SHALL use Hough Circle Detection on the cropped region to find circular engine indicators, with radius parameters tailored to the engine type (small radius for Super Heavy engines, both small and large radius for Starship engines).
4. THE Engine_Analyzer SHALL match detected circles against expected SVG coordinate positions using a configurable distance tolerance (default: 5 pixels).
5. WHEN a detected circle matches an expected engine position, THE Engine_Analyzer SHALL sample the color within that detected circle to determine Engine_Status.
6. WHEN a sampled engine color has high brightness and saturation, THE Engine_Analyzer SHALL classify the Engine_Status as "active".
7. WHEN a sampled engine color has low brightness, THE Engine_Analyzer SHALL classify the Engine_Status as "inactive".
8. IF no circle is detected within tolerance of an expected engine position, THEN THE Engine_Analyzer SHALL classify that engine's Engine_Status as "undetected".
9. THE Engine_Analyzer SHALL produce a status map containing Engine_Status for all Starship engines (3 atmospheric, 3 vacuum) and all Super Heavy engines (3 inner, 10 middle, 20 outer).
10. THE Engine_Analyzer SHALL report a detection accuracy metric (matched engines / total expected engines) for each frame.

### Requirement 4: OCR-Based Text Extraction

**User Story:** As a user, I want the system to read numeric and text values from specific frame regions, so that telemetry values like speed, altitude, and time are available as structured data.

#### Acceptance Criteria

1. WHEN the Engine_Analyzer detects engine circles within an engine bounding box, THE OCR_Engine SHALL skip OCR for any text ROI whose bounding box intersects with that engine bounding box.
2. WHEN a text ROI does not intersect with any engine bounding box that has detected engines, THE OCR_Engine SHALL crop the frame to that ROI region and extract the text content.
3. THE OCR_Engine SHALL process the following text ROI regions when not skipped: time, altitude_R value, altitude_R unit, speed_R value, speed_R unit, altitude_L value, altitude_L unit, speed_L value, speed_L unit, stage_R, stage_L, and stage_sep_text.
4. WHEN the OCR_Engine extracts a numeric value from a speed or altitude region, THE OCR_Engine SHALL parse the result as a floating-point number.
5. WHEN the OCR_Engine extracts the time region, THE OCR_Engine SHALL parse the result into a mission elapsed time format (T+HH:MM:SS or T-HH:MM:SS).
6. IF the OCR_Engine cannot confidently extract text from a region, THEN THE OCR_Engine SHALL mark that field as "unavailable" in the Telemetry_Record rather than guessing.
7. WHEN a text ROI is skipped due to engine detection in the overlapping area, THE OCR_Engine SHALL mark that field as "occluded_by_engines" in the Telemetry_Record.

### Requirement 5: Stage Assignment Logic

**User Story:** As a user, I want telemetry data correctly attributed to the right stage (Super Heavy or Starship), so that displayed values reflect which vehicle they belong to.

#### Acceptance Criteria

1. WHILE the stage_sep_text region has never contained the text "STAGE SEP" during the current session, THE system SHALL assign all left-side and right-side OCR data (speed, altitude) to the Super Heavy stage.
2. WHEN the stage_sep_text region reads "STAGE SEP" and the stage_L and stage_R regions contain stage name text, THE system SHALL assign left-side data to the stage identified by stage_L and right-side data to the stage identified by stage_R.
3. WHEN the stage_sep_text region has previously read "STAGE SEP" but stage_L and stage_R are empty or unavailable, THE system SHALL assign all left-side and right-side OCR data to the Starship stage.
4. THE system SHALL track the stage separation state as a session-level flag that transitions from "pre-separation" to "post-separation" and does not revert.
5. THE Telemetry_Record SHALL include a stage_assignment field indicating which stage each set of telemetry values belongs to (super_heavy, starship, or as labeled by stage_L/stage_R).

### Requirement 6: Telemetry Record Assembly

**User Story:** As a developer, I want extracted values assembled into a single structured record per frame, so that downstream consumers receive consistent data.

#### Acceptance Criteria

1. WHEN OCR, engine analysis, and stage assignment complete for a frame, THE system SHALL assemble a Telemetry_Record containing: mission_elapsed_time, speed_left, altitude_left, speed_right, altitude_right, stage_left_label, stage_right_label, stage_separation_text, stage_assignment_left, stage_assignment_right, starship_engines (6 entries), and superheavy_engines (33 entries).
2. THE Telemetry_Record SHALL include a monotonically increasing sequence number for ordering.
3. THE Telemetry_Record SHALL include units for speed and altitude fields as extracted from the unit ROI regions.

### Requirement 7: Web Dashboard Display

**User Story:** As a user, I want to view live telemetry data in a web dashboard and control the extraction pipeline, so that I can monitor the flight in real time.

#### Acceptance Criteria

1. THE Dashboard SHALL render using JetBrains Mono font family, #262626 background color, #FEFEFE text color, and #FF8014 accent color.
2. THE Dashboard SHALL provide an input field for the user to paste a livestream URL.
3. WHEN the user submits a livestream URL, THE Dashboard SHALL display the stream validation status (active, unreachable, or checking).
4. WHEN the livestream is validated as active, THE Dashboard SHALL display a "Start" button to begin the extraction pipeline and the pipeline SHALL remain stopped until the user clicks it.
5. WHILE the extraction pipeline is running, THE Dashboard SHALL display a "Stop" button to halt frame capture and processing.
6. THE Dashboard SHALL display the current mission elapsed time prominently.
7. THE Dashboard SHALL display speed and altitude values with their respective units for both Super Heavy and Starship vehicles.
8. THE Dashboard SHALL display interactive time-series graphs for speed and altitude per vehicle (Super Heavy and Starship) that update as new Telemetry_Records arrive, with mission elapsed time on the x-axis.
9. THE Dashboard SHALL allow the user to zoom into a specific time range on the speed and altitude graphs using mouse drag or scroll interaction.
10. THE Dashboard SHALL provide a reset button on each graph to restore the full time scale after zooming.
11. THE Dashboard SHALL display stage labels for both left and right stages.
12. THE Dashboard SHALL display engine status visualizations using the spatial layouts defined in superheavy_engine_diagram.svg (3 rings: inner/middle/outer) and starship_engine_diagram.svg (atmo and vacuum groups), scaled to a readable size for users rather than rendered at the native SVG viewport dimensions.
13. THE Dashboard SHALL render each engine circle with its engine identifier (e.g., "E1", "E2") displayed inside the circle in all-caps text.
14. THE Dashboard SHALL illuminate each engine circle based on Engine_Status: active engines with a bright filled color, inactive engines with a dimmed or dark fill, and undetected engines with a neutral/grey indicator.
15. WHEN a new Telemetry_Record is available, THE Dashboard SHALL update displayed values within 500 milliseconds.
16. WHEN a telemetry field is marked "unavailable" or "occluded_by_engines", THE Dashboard SHALL display a placeholder indicator (e.g., "--") instead of stale data.
17. THE Dashboard SHALL display the current pipeline status (stopped, running, disconnected) to the user.

### Requirement 8: Template Registry and Multi-Template Support

**User Story:** As a developer, I want to register multiple ROI templates, so that the system can support different rocket configurations in the future.

#### Acceptance Criteria

1. THE Template_Registry SHALL store one or more ROI templates identified by a unique template name.
2. WHEN a template is selected from the Template_Registry, THE system SHALL use that template's ROI definitions for all subsequent frame extraction.
3. THE Template_Registry SHALL include a default template (starship_rois) for SpaceX Starship IFT livestreams.
4. IF a requested template name does not exist in the Template_Registry, THEN THE Template_Registry SHALL return an error indicating the template is not found.

### Requirement 9: Telemetry Record Serialization

**User Story:** As a developer, I want to serialize and deserialize Telemetry Records, so that they can be transmitted between the extraction backend and the web dashboard.

#### Acceptance Criteria

1. THE system SHALL serialize Telemetry_Record objects to JSON format for transmission.
2. THE system SHALL deserialize JSON payloads into Telemetry_Record objects on the Dashboard client.
3. FOR ALL valid Telemetry_Record objects, serializing to JSON then deserializing SHALL produce an equivalent Telemetry_Record (round-trip property).
4. IF a received JSON payload is missing required fields, THEN THE system SHALL return a descriptive validation error.
