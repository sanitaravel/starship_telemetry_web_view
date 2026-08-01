"""SVG Template Parser for ROI configuration extraction.

Parses SVG ROI template files into structured ROIConfiguration objects,
and serializes ROIConfiguration objects back to SVG format for round-trip support.
"""

import xml.etree.ElementTree as ET
from typing import Union

from src.models import (
    EngineGroup,
    EngineSubgroup,
    ParseError,
    ROICircle,
    ROIConfiguration,
    ROIRect,
)

# SVG namespace
SVG_NS = "http://www.w3.org/2000/svg"
NS_MAP = {"svg": SVG_NS}

# Required top-level text regions that must exist as direct rects
REQUIRED_TOP_LEVEL_RECTS = ["time", "stage_r", "stage_l", "stage_sep_text"]

# Required text region groups (contain value and unit rects)
REQUIRED_TEXT_GROUPS = ["altitude_r", "speed_r", "altitude_l", "speed_l"]

# Required engine groups (must start with "engines_")
REQUIRED_ENGINE_GROUPS = ["engines_starship", "engines_superheavy"]


def _strip_ns(tag: str) -> str:
    """Strip SVG namespace prefix from a tag name."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _get_id(element: ET.Element) -> str | None:
    """Get the id attribute of an element."""
    return element.get("id")


def _parse_float(value: str | None, attr_name: str, element_id: str) -> float | None:
    """Parse a float attribute value, returning None if missing or invalid."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_rect(element: ET.Element) -> ROIRect | None:
    """Parse a rect element into an ROIRect, returning None if malformed."""
    elem_id = _get_id(element)
    if elem_id is None:
        return None

    x = _parse_float(element.get("x"), "x", elem_id)
    y = _parse_float(element.get("y"), "y", elem_id)
    width = _parse_float(element.get("width"), "width", elem_id)
    height = _parse_float(element.get("height"), "height", elem_id)

    if any(v is None for v in (x, y, width, height)):
        return None

    return ROIRect(id=elem_id, x=x, y=y, width=width, height=height)


def _parse_circle(element: ET.Element) -> ROICircle | None:
    """Parse a circle element into an ROICircle, returning None if malformed."""
    elem_id = _get_id(element)
    if elem_id is None:
        return None

    cx = _parse_float(element.get("cx"), "cx", elem_id)
    cy = _parse_float(element.get("cy"), "cy", elem_id)
    r = _parse_float(element.get("r"), "r", elem_id)

    if any(v is None for v in (cx, cy, r)):
        return None

    return ROICircle(id=elem_id, cx=cx, cy=cy, r=r)


def _compute_bounding_box(group_id: str, subgroups: list[EngineSubgroup]) -> ROIRect:
    """Compute bounding box from min/max of all circles across subgroups, accounting for radius."""
    all_circles: list[ROICircle] = []
    for sg in subgroups:
        all_circles.extend(sg.circles)

    if not all_circles:
        return ROIRect(id=group_id, x=0, y=0, width=0, height=0)

    min_x = min(c.cx - c.r for c in all_circles)
    min_y = min(c.cy - c.r for c in all_circles)
    max_x = max(c.cx + c.r for c in all_circles)
    max_y = max(c.cy + c.r for c in all_circles)

    return ROIRect(
        id=group_id,
        x=min_x,
        y=min_y,
        width=max_x - min_x,
        height=max_y - min_y,
    )


def _parse_engine_group(group_element: ET.Element) -> EngineGroup | tuple[str, list[str]]:
    """
    Parse an engine group element containing subgroups of circles.
    Returns an EngineGroup on success, or a tuple of (group_id, malformed_ids) on partial failure.
    """
    group_id = _get_id(group_element)
    subgroups: list[EngineSubgroup] = []
    malformed: list[str] = []

    for child in group_element:
        tag = _strip_ns(child.tag)
        if tag == "g":
            subgroup_name = _get_id(child)
            if subgroup_name is None:
                continue
            circles: list[ROICircle] = []
            for circle_elem in child:
                circle_tag = _strip_ns(circle_elem.tag)
                if circle_tag == "circle":
                    circle = _parse_circle(circle_elem)
                    if circle is not None:
                        circles.append(circle)
                    else:
                        cid = _get_id(circle_elem) or "unknown_circle"
                        malformed.append(cid)
            subgroups.append(EngineSubgroup(name=subgroup_name, circles=circles))

    if malformed:
        return (group_id, malformed)

    bounding_box = _compute_bounding_box(group_id, subgroups)
    return EngineGroup(group_id=group_id, bounding_box=bounding_box, subgroups=subgroups)


def parse_roi_template(
    svg_content: str, template_name: str = "starship_rois"
) -> Union[ROIConfiguration, ParseError]:
    """
    Parse an SVG ROI template into a structured configuration.

    Args:
        svg_content: The SVG file content as a string.
        template_name: Name to assign to the template (default: "starship_rois").

    Returns:
        ROIConfiguration on success, or ParseError with descriptive messaging on failure.
    """
    # Try to parse the XML
    try:
        root = ET.fromstring(svg_content)
    except ET.ParseError as e:
        return ParseError(message=f"Malformed SVG XML: {e}")

    # Validate root element is svg
    root_tag = _strip_ns(root.tag)
    if root_tag != "svg":
        return ParseError(message=f"Root element is '{root_tag}', expected 'svg'")

    # Validate viewBox
    viewbox = root.get("viewBox")
    width_attr = root.get("width")
    height_attr = root.get("height")

    if viewbox:
        parts = viewbox.split()
        if len(parts) != 4:
            return ParseError(message=f"Invalid viewBox format: '{viewbox}'")
        try:
            vb_x, vb_y, vb_w, vb_h = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError:
            return ParseError(message=f"Non-numeric viewBox values: '{viewbox}'")
        if vb_w != 1920 or vb_h != 1080:
            return ParseError(
                message=f"ViewBox dimensions must be 1920×1080, got {vb_w}×{vb_h}"
            )
    elif width_attr and height_attr:
        try:
            w = float(width_attr)
            h = float(height_attr)
        except ValueError:
            return ParseError(message="Non-numeric width/height attributes")
        if w != 1920 or h != 1080:
            return ParseError(
                message=f"SVG dimensions must be 1920×1080, got {w}×{h}"
            )
    else:
        return ParseError(message="SVG missing viewBox and width/height attributes")

    # Find the main content group (Frame group or root children)
    # The SVG has a top-level <g id="Frame 113"> containing all elements
    main_group = None
    for child in root:
        if _strip_ns(child.tag) == "g":
            main_group = child
            break

    # If no group wrapper, use root as main container
    if main_group is None:
        main_group = root

    # Collect all elements for parsing
    text_regions: dict[str, ROIRect] = {}
    engine_groups: list[EngineGroup] = []
    missing_regions: list[str] = []
    malformed_elements: list[str] = []

    # Build a map of direct children by id
    children_by_id: dict[str, ET.Element] = {}
    for child in main_group:
        cid = _get_id(child)
        if cid:
            children_by_id[cid] = child

    # Process top-level rects (text regions like time, stage_R, stage_L, stage_sep_text)
    for rect_id in REQUIRED_TOP_LEVEL_RECTS:
        if rect_id in children_by_id:
            elem = children_by_id[rect_id]
            if _strip_ns(elem.tag) == "rect":
                roi = _parse_rect(elem)
                if roi is not None:
                    text_regions[rect_id] = roi
                else:
                    malformed_elements.append(rect_id)
            else:
                missing_regions.append(rect_id)
        else:
            missing_regions.append(rect_id)

    # Process text region groups (altitude_r, speed_r, altitude_l, speed_l)
    for group_id in REQUIRED_TEXT_GROUPS:
        if group_id in children_by_id:
            group_elem = children_by_id[group_id]
            if _strip_ns(group_elem.tag) == "g":
                # Extract rects within this group with qualified names
                for rect_elem in group_elem:
                    if _strip_ns(rect_elem.tag) == "rect":
                        rect = _parse_rect(rect_elem)
                        if rect is not None:
                            # Qualify the name: "value" → group_id, "unit" → "group_id_unit"
                            local_id = rect.id
                            # Strip numeric suffixes from Figma IDs for clean naming
                            base_name = local_id.rstrip("_0123456789")
                            if not base_name:
                                base_name = local_id
                            # Remove trailing underscore if present
                            base_name = base_name.rstrip("_")
                            # "value" rect gets the group name directly (e.g., speed_l)
                            # "unit" rect gets group_unit (e.g., speed_l_unit)
                            if base_name == "value":
                                qualified_name = group_id
                            else:
                                qualified_name = f"{group_id}_{base_name}"
                            text_regions[qualified_name] = ROIRect(
                                id=qualified_name,
                                x=rect.x,
                                y=rect.y,
                                width=rect.width,
                                height=rect.height,
                            )
                        else:
                            rid = _get_id(rect_elem) or "unknown_rect"
                            malformed_elements.append(f"{group_id}/{rid}")
            else:
                missing_regions.append(group_id)
        else:
            missing_regions.append(group_id)

    # Process engine groups
    for engine_group_id in REQUIRED_ENGINE_GROUPS:
        if engine_group_id in children_by_id:
            group_elem = children_by_id[engine_group_id]
            if _strip_ns(group_elem.tag) == "g":
                result = _parse_engine_group(group_elem)
                if isinstance(result, EngineGroup):
                    engine_groups.append(result)
                else:
                    # Partial failure: group_id and malformed circle ids
                    _, malformed_ids = result
                    malformed_elements.extend(
                        f"{engine_group_id}/{mid}" for mid in malformed_ids
                    )
            else:
                missing_regions.append(engine_group_id)
        else:
            missing_regions.append(engine_group_id)

    # Report errors if any
    if missing_regions or malformed_elements:
        return ParseError(
            message=f"SVG template parsing failed: {len(missing_regions)} missing region(s), "
            f"{len(malformed_elements)} malformed element(s)",
            missing_regions=missing_regions,
            malformed_elements=malformed_elements,
        )

    return ROIConfiguration(
        template_name=template_name,
        view_box=(1920, 1080),
        text_regions=text_regions,
        engine_groups=engine_groups,
    )


def serialize_roi_configuration(config: ROIConfiguration) -> str:
    """
    Serialize an ROIConfiguration back to SVG format.

    Produces a valid SVG string with the same structure that can be parsed
    back into an equivalent ROIConfiguration (round-trip support).

    Args:
        config: The ROI configuration to serialize.

    Returns:
        SVG content as a string.
    """
    lines: list[str] = []
    lines.append(
        '<svg width="1920" height="1080" viewBox="0 0 1920 1080" '
        'fill="none" xmlns="http://www.w3.org/2000/svg">'
    )
    lines.append('<g id="Frame">')

    # Serialize top-level text rects
    # Identify which keys belong to text groups (either group_id itself or group_id_unit)
    grouped_keys = set(REQUIRED_TEXT_GROUPS)
    for group_id in REQUIRED_TEXT_GROUPS:
        grouped_keys.add(f"{group_id}_unit")

    for region_id, rect in config.text_regions.items():
        # Skip keys that belong to a text group
        if region_id in grouped_keys:
            continue
        lines.append(
            f'<rect id="{region_id}" x="{rect.x}" y="{rect.y}" '
            f'width="{rect.width}" height="{rect.height}" '
            f'fill="#D9D9D9" fill-opacity="0.25" stroke="white"/>'
        )

    # Serialize engine groups
    for engine_group in config.engine_groups:
        lines.append(f'<g id="{engine_group.group_id}">')
        for subgroup in engine_group.subgroups:
            lines.append(f'<g id="{subgroup.name}">')
            for circle in subgroup.circles:
                lines.append(
                    f'<circle id="{circle.id}" cx="{circle.cx}" cy="{circle.cy}" '
                    f'r="{circle.r}" fill="#D9D9D9" fill-opacity="0.25" stroke="white"/>'
                )
            lines.append("</g>")
        lines.append("</g>")

    # Serialize text region groups
    for group_id in REQUIRED_TEXT_GROUPS:
        # The value rect uses the group_id directly, unit uses group_id_unit
        value_key = group_id
        unit_key = f"{group_id}_unit"
        value_rect = config.text_regions.get(value_key)
        unit_rect = config.text_regions.get(unit_key)
        if value_rect or unit_rect:
            lines.append(f'<g id="{group_id}">')
            if value_rect:
                lines.append(
                    f'<rect id="value" x="{value_rect.x}" y="{value_rect.y}" '
                    f'width="{value_rect.width}" height="{value_rect.height}" '
                    f'fill="#D9D9D9" fill-opacity="0.25" stroke="white"/>'
                )
            if unit_rect:
                lines.append(
                    f'<rect id="unit" x="{unit_rect.x}" y="{unit_rect.y}" '
                    f'width="{unit_rect.width}" height="{unit_rect.height}" '
                    f'fill="#D9D9D9" fill-opacity="0.25" stroke="white"/>'
                )
            lines.append("</g>")

    lines.append("</g>")
    lines.append("</svg>")

    return "\n".join(lines)
