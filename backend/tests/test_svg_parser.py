"""Unit tests for the SVG Template Parser."""

import pytest
from pathlib import Path

from src.svg_parser import parse_roi_template, serialize_roi_configuration
from src.models import (
    EngineGroup,
    EngineSubgroup,
    ParseError,
    ROICircle,
    ROIConfiguration,
    ROIRect,
)


# Path to the actual SVG template
SVG_TEMPLATE_PATH = Path(__file__).parent.parent.parent / "diagrams" / "starship_rois.svg"


@pytest.fixture
def svg_content() -> str:
    """Load the actual SVG template content."""
    return SVG_TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture
def parsed_config(svg_content: str) -> ROIConfiguration:
    """Parse the actual SVG template and return the configuration."""
    result = parse_roi_template(svg_content)
    assert isinstance(result, ROIConfiguration), f"Parse failed: {result}"
    return result


class TestParseROITemplate:
    """Tests for parse_roi_template()."""

    def test_parses_valid_svg_returns_configuration(self, svg_content: str):
        """Valid SVG should return an ROIConfiguration, not a ParseError."""
        result = parse_roi_template(svg_content)
        assert isinstance(result, ROIConfiguration)

    def test_view_box_is_1920x1080(self, parsed_config: ROIConfiguration):
        """ViewBox should be (1920, 1080)."""
        assert parsed_config.view_box == (1920, 1080)

    def test_template_name_default(self, parsed_config: ROIConfiguration):
        """Template name defaults to 'starship_rois'."""
        assert parsed_config.template_name == "starship_rois"

    def test_custom_template_name(self, svg_content: str):
        """Custom template name is passed through."""
        result = parse_roi_template(svg_content, template_name="custom_name")
        assert isinstance(result, ROIConfiguration)
        assert result.template_name == "custom_name"

    def test_extracts_top_level_text_rects(self, parsed_config: ROIConfiguration):
        """Top-level rects (time, stage_r, stage_l, stage_sep_text) should be extracted."""
        assert "time" in parsed_config.text_regions
        assert "stage_r" in parsed_config.text_regions
        assert "stage_l" in parsed_config.text_regions
        assert "stage_sep_text" in parsed_config.text_regions

    def test_time_rect_coordinates(self, parsed_config: ROIConfiguration):
        """The time rect should have correct coordinates from the SVG."""
        time_rect = parsed_config.text_regions["time"]
        assert time_rect.x == 828.5
        assert time_rect.y == 968.5
        assert time_rect.width == 257
        assert time_rect.height == 39

    def test_extracts_text_group_regions(self, parsed_config: ROIConfiguration):
        """Text region groups (altitude_r, speed_r, etc.) should produce qualified rects."""
        # altitude_r should have value and unit rects
        assert "altitude_r" in parsed_config.text_regions
        assert "altitude_r_unit" in parsed_config.text_regions
        # speed_r should have value and unit rects
        assert "speed_r" in parsed_config.text_regions
        assert "speed_r_unit" in parsed_config.text_regions
        # altitude_l
        assert "altitude_l" in parsed_config.text_regions
        assert "altitude_l_unit" in parsed_config.text_regions
        # speed_l
        assert "speed_l" in parsed_config.text_regions
        assert "speed_l_unit" in parsed_config.text_regions

    def test_altitude_r_value_coordinates(self, parsed_config: ROIConfiguration):
        """altitude_r value rect should have correct coordinates."""
        rect = parsed_config.text_regions["altitude_r"]
        assert rect.x == 1707.5
        assert rect.y == 968.5
        assert rect.width == 112
        assert rect.height == 39

    def test_extracts_engine_groups(self, parsed_config: ROIConfiguration):
        """Should extract both engine groups (starship and superheavy)."""
        assert len(parsed_config.engine_groups) == 2
        group_ids = [g.group_id for g in parsed_config.engine_groups]
        assert "engines_starship" in group_ids
        assert "engines_superheavy" in group_ids

    def test_starship_engine_subgroups(self, parsed_config: ROIConfiguration):
        """Starship engine group should have atmo and vacuum subgroups."""
        starship_group = next(
            g for g in parsed_config.engine_groups if g.group_id == "engines_starship"
        )
        subgroup_names = [sg.name for sg in starship_group.subgroups]
        assert "atmo" in subgroup_names
        assert "vacuum" in subgroup_names

    def test_starship_atmo_circles(self, parsed_config: ROIConfiguration):
        """Starship atmo subgroup should have 3 circles: e1, e2, e3."""
        starship_group = next(
            g for g in parsed_config.engine_groups if g.group_id == "engines_starship"
        )
        atmo = next(sg for sg in starship_group.subgroups if sg.name == "atmo")
        assert len(atmo.circles) == 3
        circle_ids = [c.id for c in atmo.circles]
        assert "ss_e1" in circle_ids
        assert "ss_e2" in circle_ids
        assert "ss_e3" in circle_ids

    def test_starship_vacuum_circles(self, parsed_config: ROIConfiguration):
        """Starship vacuum subgroup should have 3 circles: ss_e4, ss_e5, ss_e6."""
        starship_group = next(
            g for g in parsed_config.engine_groups if g.group_id == "engines_starship"
        )
        vacuum = next(sg for sg in starship_group.subgroups if sg.name == "vacuum")
        assert len(vacuum.circles) == 3
        circle_ids = [c.id for c in vacuum.circles]
        assert "ss_e4" in circle_ids
        assert "ss_e5" in circle_ids
        assert "ss_e6" in circle_ids

    def test_superheavy_engine_subgroups(self, parsed_config: ROIConfiguration):
        """Super Heavy engine group should have outer, middle, inner subgroups."""
        sh_group = next(
            g for g in parsed_config.engine_groups if g.group_id == "engines_superheavy"
        )
        subgroup_names = [sg.name for sg in sh_group.subgroups]
        assert "engines_outer" in subgroup_names
        assert "engines_middle" in subgroup_names
        assert "engines_inner" in subgroup_names

    def test_superheavy_outer_has_20_circles(self, parsed_config: ROIConfiguration):
        """Super Heavy outer ring should have 20 engine circles."""
        sh_group = next(
            g for g in parsed_config.engine_groups if g.group_id == "engines_superheavy"
        )
        outer = next(sg for sg in sh_group.subgroups if sg.name == "engines_outer")
        assert len(outer.circles) == 20

    def test_superheavy_middle_has_10_circles(self, parsed_config: ROIConfiguration):
        """Super Heavy middle ring should have 10 engine circles."""
        sh_group = next(
            g for g in parsed_config.engine_groups if g.group_id == "engines_superheavy"
        )
        middle = next(sg for sg in sh_group.subgroups if sg.name == "engines_middle")
        assert len(middle.circles) == 10

    def test_superheavy_inner_has_3_circles(self, parsed_config: ROIConfiguration):
        """Super Heavy inner ring should have 3 engine circles."""
        sh_group = next(
            g for g in parsed_config.engine_groups if g.group_id == "engines_superheavy"
        )
        inner = next(sg for sg in sh_group.subgroups if sg.name == "engines_inner")
        assert len(inner.circles) == 3

    def test_circle_coordinates_extracted(self, parsed_config: ROIConfiguration):
        """Circle coordinates should be correctly extracted."""
        starship_group = next(
            g for g in parsed_config.engine_groups if g.group_id == "engines_starship"
        )
        atmo = next(sg for sg in starship_group.subgroups if sg.name == "atmo")
        e1 = next(c for c in atmo.circles if c.id == "ss_e1")
        assert e1.cx == 1765
        assert e1.cy == 981
        assert e1.r == 5.5

    def test_engine_group_bounding_box(self, parsed_config: ROIConfiguration):
        """Engine group bounding box should be computed from min/max circles + radius."""
        starship_group = next(
            g for g in parsed_config.engine_groups if g.group_id == "engines_starship"
        )
        bb = starship_group.bounding_box
        # Starship engines range: cx from 1738 to 1792, cy from 975 to 1021
        # Min radii: vacuum=14.5, so min_x = 1738-14.5=1723.5, min_y = 975-14.5=960.5
        # Max: cx=1792+14.5=1806.5, cy=1021+14.5=1035.5
        assert bb.x == 1723.5
        assert bb.y == 960.5
        assert bb.width == pytest.approx(1806.5 - 1723.5)
        assert bb.height == pytest.approx(1035.5 - 960.5)


class TestParseErrors:
    """Tests for error handling in parse_roi_template()."""

    def test_malformed_xml_returns_parse_error(self):
        """Completely invalid XML should return a ParseError."""
        result = parse_roi_template("<not valid xml>>>")
        assert isinstance(result, ParseError)
        assert "Malformed SVG XML" in result.message

    def test_non_svg_root_returns_error(self):
        """Non-SVG root element should return error."""
        result = parse_roi_template('<html xmlns="http://www.w3.org/1999/xhtml"></html>')
        assert isinstance(result, ParseError)
        assert "expected 'svg'" in result.message

    def test_wrong_viewbox_returns_error(self):
        """ViewBox not 1920×1080 should return error."""
        svg = '<svg width="800" height="600" viewBox="0 0 800 600" xmlns="http://www.w3.org/2000/svg"></svg>'
        result = parse_roi_template(svg)
        assert isinstance(result, ParseError)
        assert "1920×1080" in result.message

    def test_missing_viewbox_and_dimensions_returns_error(self):
        """SVG without viewBox and without width/height should return error."""
        svg = '<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        result = parse_roi_template(svg)
        assert isinstance(result, ParseError)
        assert "missing" in result.message.lower()

    def test_missing_required_regions_reported(self):
        """Missing required text regions should be listed in ParseError."""
        svg = (
            '<svg width="1920" height="1080" viewBox="0 0 1920 1080" '
            'xmlns="http://www.w3.org/2000/svg">'
            '<g id="Frame">'
            '<rect id="time" x="0" y="0" width="100" height="50"/>'
            "</g>"
            "</svg>"
        )
        result = parse_roi_template(svg)
        assert isinstance(result, ParseError)
        assert len(result.missing_regions) > 0
        # Should be missing stage_r, stage_l, stage_sep_text, and all groups
        assert "stage_r" in result.missing_regions
        assert "stage_l" in result.missing_regions
        assert "stage_sep_text" in result.missing_regions

    def test_missing_engine_groups_reported(self):
        """Missing engine groups should be listed in ParseError."""
        svg = (
            '<svg width="1920" height="1080" viewBox="0 0 1920 1080" '
            'xmlns="http://www.w3.org/2000/svg">'
            '<g id="Frame">'
            '<rect id="time" x="0" y="0" width="100" height="50"/>'
            '<rect id="stage_r" x="0" y="0" width="100" height="50"/>'
            '<rect id="stage_l" x="0" y="0" width="100" height="50"/>'
            '<rect id="stage_sep_text" x="0" y="0" width="100" height="50"/>'
            '<g id="altitude_r"><rect id="value" x="0" y="0" width="50" height="20"/>'
            '<rect id="unit" x="0" y="0" width="50" height="20"/></g>'
            '<g id="speed_r"><rect id="value" x="0" y="0" width="50" height="20"/>'
            '<rect id="unit" x="0" y="0" width="50" height="20"/></g>'
            '<g id="altitude_l"><rect id="value" x="0" y="0" width="50" height="20"/>'
            '<rect id="unit" x="0" y="0" width="50" height="20"/></g>'
            '<g id="speed_l"><rect id="value" x="0" y="0" width="50" height="20"/>'
            '<rect id="unit" x="0" y="0" width="50" height="20"/></g>'
            "</g>"
            "</svg>"
        )
        result = parse_roi_template(svg)
        assert isinstance(result, ParseError)
        assert "engines_starship" in result.missing_regions
        assert "engines_superheavy" in result.missing_regions


class TestSerializeROIConfiguration:
    """Tests for serialize_roi_configuration()."""

    def test_serializes_to_valid_svg(self, parsed_config: ROIConfiguration):
        """Serialized output should be parseable XML."""
        svg_str = serialize_roi_configuration(parsed_config)
        # Should not raise
        root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(svg_str)
        assert root.tag == "{http://www.w3.org/2000/svg}svg" or root.tag == "svg"

    def test_round_trip_preserves_text_regions(self, parsed_config: ROIConfiguration):
        """Parse → serialize → parse should preserve text regions."""
        svg_str = serialize_roi_configuration(parsed_config)
        result = parse_roi_template(svg_str, template_name=parsed_config.template_name)
        assert isinstance(result, ROIConfiguration)
        assert set(result.text_regions.keys()) == set(parsed_config.text_regions.keys())
        for key in parsed_config.text_regions:
            orig = parsed_config.text_regions[key]
            roundtripped = result.text_regions[key]
            assert roundtripped.x == pytest.approx(orig.x)
            assert roundtripped.y == pytest.approx(orig.y)
            assert roundtripped.width == pytest.approx(orig.width)
            assert roundtripped.height == pytest.approx(orig.height)

    def test_round_trip_preserves_engine_groups(self, parsed_config: ROIConfiguration):
        """Parse → serialize → parse should preserve engine groups."""
        svg_str = serialize_roi_configuration(parsed_config)
        result = parse_roi_template(svg_str, template_name=parsed_config.template_name)
        assert isinstance(result, ROIConfiguration)
        assert len(result.engine_groups) == len(parsed_config.engine_groups)

        for orig_group in parsed_config.engine_groups:
            rt_group = next(
                g for g in result.engine_groups if g.group_id == orig_group.group_id
            )
            assert len(rt_group.subgroups) == len(orig_group.subgroups)
            for orig_sg in orig_group.subgroups:
                rt_sg = next(sg for sg in rt_group.subgroups if sg.name == orig_sg.name)
                assert len(rt_sg.circles) == len(orig_sg.circles)
                orig_ids = sorted(c.id for c in orig_sg.circles)
                rt_ids = sorted(c.id for c in rt_sg.circles)
                assert rt_ids == orig_ids

    def test_round_trip_preserves_circle_coordinates(self, parsed_config: ROIConfiguration):
        """Parse → serialize → parse should preserve circle coordinates."""
        svg_str = serialize_roi_configuration(parsed_config)
        result = parse_roi_template(svg_str, template_name=parsed_config.template_name)
        assert isinstance(result, ROIConfiguration)

        for orig_group in parsed_config.engine_groups:
            rt_group = next(
                g for g in result.engine_groups if g.group_id == orig_group.group_id
            )
            for orig_sg in orig_group.subgroups:
                rt_sg = next(sg for sg in rt_group.subgroups if sg.name == orig_sg.name)
                for orig_c in orig_sg.circles:
                    rt_c = next(c for c in rt_sg.circles if c.id == orig_c.id)
                    assert rt_c.cx == pytest.approx(orig_c.cx)
                    assert rt_c.cy == pytest.approx(orig_c.cy)
                    assert rt_c.r == pytest.approx(orig_c.r)


# --- Property-Based Tests (Hypothesis) ---

from hypothesis import given, settings, assume
from hypothesis.strategies import (
    composite,
    floats,
    integers,
    just,
    lists,
    sampled_from,
    sets,
    text,
)

from src.svg_parser import (
    REQUIRED_ENGINE_GROUPS,
    REQUIRED_TEXT_GROUPS,
    REQUIRED_TOP_LEVEL_RECTS,
)


# Sentinel for "no strategy provided"
_NO_STRATEGY = object()


# Strategy for generating valid ROIRect objects
@composite
def roi_rects(draw, *, id_strategy=_NO_STRATEGY):
    """Generate arbitrary ROIRect with coordinates within 1920x1080 viewport."""
    rect_id = draw(id_strategy) if id_strategy is not _NO_STRATEGY else draw(
        text(
            alphabet="abcdefghijklmnopqrstuvwxyz_",
            min_size=2,
            max_size=12,
        )
    )
    x = draw(floats(min_value=0.0, max_value=1800.0, allow_nan=False, allow_infinity=False))
    y = draw(floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False))
    width = draw(floats(min_value=1.0, max_value=200.0, allow_nan=False, allow_infinity=False))
    height = draw(floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False))
    return ROIRect(id=rect_id, x=x, y=y, width=width, height=height)


# Strategy for generating valid ROICircle objects
@composite
def roi_circles(draw, *, id_strategy=_NO_STRATEGY):
    """Generate arbitrary ROICircle with coordinates within 1920x1080 viewport."""
    circle_id = draw(id_strategy) if id_strategy is not _NO_STRATEGY else draw(
        text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789_",
            min_size=2,
            max_size=10,
        )
    )
    cx = draw(floats(min_value=10.0, max_value=1910.0, allow_nan=False, allow_infinity=False))
    cy = draw(floats(min_value=10.0, max_value=1070.0, allow_nan=False, allow_infinity=False))
    r = draw(floats(min_value=1.0, max_value=30.0, allow_nan=False, allow_infinity=False))
    return ROICircle(id=circle_id, cx=cx, cy=cy, r=r)


# Strategy for generating EngineSubgroup objects
@composite
def engine_subgroups(draw, *, name_strategy=_NO_STRATEGY):
    """Generate an EngineSubgroup with 1-5 circles."""
    name = draw(name_strategy) if name_strategy is not _NO_STRATEGY else draw(
        text(
            alphabet="abcdefghijklmnopqrstuvwxyz_",
            min_size=2,
            max_size=15,
        )
    )
    # Generate 1-5 circles with unique IDs within this subgroup
    num_circles = draw(integers(min_value=1, max_value=5))
    circles = []
    for i in range(num_circles):
        circle = draw(roi_circles(id_strategy=just(f"{name}_c{i}")))
        circles.append(circle)
    return EngineSubgroup(name=name, circles=circles)


# Strategy for generating EngineGroup objects
@composite
def engine_groups(draw, *, group_id_strategy=_NO_STRATEGY):
    """Generate an EngineGroup with 1-3 subgroups."""
    group_id = draw(group_id_strategy) if group_id_strategy is not _NO_STRATEGY else draw(
        sampled_from(REQUIRED_ENGINE_GROUPS)
    )
    # Generate 1-3 subgroups with unique names
    num_subgroups = draw(integers(min_value=1, max_value=3))
    subgroups = []
    for i in range(num_subgroups):
        sg = draw(engine_subgroups(name_strategy=just(f"{group_id}_sg{i}")))
        subgroups.append(sg)
    return EngineGroup(
        group_id=group_id,
        bounding_box=ROIRect(id=group_id, x=0, y=0, width=0, height=0),  # placeholder
        subgroups=subgroups,
    )


# Strategy for generating valid ROIConfiguration objects
@composite
def roi_configurations(draw):
    """Generate arbitrary valid ROIConfiguration objects suitable for round-trip testing.

    Produces configurations with:
    - All required top-level rects (time, stage_r, stage_l, stage_sep_text)
    - All required text groups (altitude_r, speed_r, altitude_l, speed_l) with value/unit rects
    - Both required engine groups (engines_starship, engines_superheavy) with subgroups
    """
    template_name = draw(text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789_",
        min_size=3,
        max_size=20,
    ))

    text_regions: dict[str, ROIRect] = {}

    # Generate required top-level rects
    for rect_id in REQUIRED_TOP_LEVEL_RECTS:
        rect = draw(roi_rects(id_strategy=just(rect_id)))
        text_regions[rect_id] = rect

    # Generate required text group regions with value/unit child rects
    # "value" rect uses the group_id directly, "unit" uses group_id_unit
    for group_id in REQUIRED_TEXT_GROUPS:
        value_rect = draw(roi_rects(id_strategy=just(group_id)))
        text_regions[group_id] = value_rect
        unit_key = f"{group_id}_unit"
        unit_rect = draw(roi_rects(id_strategy=just(unit_key)))
        text_regions[unit_key] = unit_rect

    # Generate engine groups
    eg_list = []
    for eg_id in REQUIRED_ENGINE_GROUPS:
        eg = draw(engine_groups(group_id_strategy=just(eg_id)))
        eg_list.append(eg)

    return ROIConfiguration(
        template_name=template_name,
        view_box=(1920, 1080),
        text_regions=text_regions,
        engine_groups=eg_list,
    )


class TestPropertySVGTemplateRoundTrip:
    """Property-based tests for SVG Template Parser.

    **Validates: Requirements 1.3, 1.5**
    """

    @given(config=roi_configurations())
    @settings(max_examples=100)
    def test_svg_template_round_trip(self, config: ROIConfiguration):
        """Property 1: SVG Template Round-Trip.

        For any valid ROIConfiguration object, serializing it to SVG format
        and then parsing the result back SHALL produce an equivalent
        ROIConfiguration object (all coordinates, IDs, and structural
        relationships preserved).

        **Validates: Requirements 1.3**
        """
        # Serialize to SVG
        svg_str = serialize_roi_configuration(config)

        # Parse back
        result = parse_roi_template(svg_str, template_name=config.template_name)
        assert isinstance(result, ROIConfiguration), (
            f"Expected ROIConfiguration but got ParseError: {result}"
        )

        # Verify template name preserved
        assert result.template_name == config.template_name

        # Verify view_box preserved
        assert result.view_box == config.view_box

        # Verify text regions preserved (same keys)
        assert set(result.text_regions.keys()) == set(config.text_regions.keys()), (
            f"Text region keys differ.\n"
            f"Expected: {sorted(config.text_regions.keys())}\n"
            f"Got: {sorted(result.text_regions.keys())}"
        )

        # Verify text region coordinates with float tolerance
        for key in config.text_regions:
            orig = config.text_regions[key]
            rt = result.text_regions[key]
            assert rt.x == pytest.approx(orig.x, abs=1e-6), f"{key}.x mismatch"
            assert rt.y == pytest.approx(orig.y, abs=1e-6), f"{key}.y mismatch"
            assert rt.width == pytest.approx(orig.width, abs=1e-6), f"{key}.width mismatch"
            assert rt.height == pytest.approx(orig.height, abs=1e-6), f"{key}.height mismatch"

        # Verify engine groups preserved (same count and group_ids)
        assert len(result.engine_groups) == len(config.engine_groups)
        result_group_ids = sorted(g.group_id for g in result.engine_groups)
        config_group_ids = sorted(g.group_id for g in config.engine_groups)
        assert result_group_ids == config_group_ids

        # Verify subgroups and circles for each engine group
        for orig_group in config.engine_groups:
            rt_group = next(
                g for g in result.engine_groups if g.group_id == orig_group.group_id
            )
            assert len(rt_group.subgroups) == len(orig_group.subgroups), (
                f"Subgroup count mismatch for {orig_group.group_id}"
            )

            for orig_sg in orig_group.subgroups:
                rt_sg = next(
                    sg for sg in rt_group.subgroups if sg.name == orig_sg.name
                )
                assert len(rt_sg.circles) == len(orig_sg.circles), (
                    f"Circle count mismatch for {orig_sg.name}"
                )

                # Verify circle coordinates
                for orig_c in orig_sg.circles:
                    rt_c = next(c for c in rt_sg.circles if c.id == orig_c.id)
                    assert rt_c.cx == pytest.approx(orig_c.cx, abs=1e-6), (
                        f"Circle {orig_c.id}.cx mismatch"
                    )
                    assert rt_c.cy == pytest.approx(orig_c.cy, abs=1e-6), (
                        f"Circle {orig_c.id}.cy mismatch"
                    )
                    assert rt_c.r == pytest.approx(orig_c.r, abs=1e-6), (
                        f"Circle {orig_c.id}.r mismatch"
                    )


class TestPropertyMalformedSVGErrorCompleteness:
    """Property-based tests for error reporting completeness.

    **Validates: Requirements 1.5**
    """

    @given(
        regions_to_remove=sets(
            sampled_from(
                REQUIRED_TOP_LEVEL_RECTS + REQUIRED_TEXT_GROUPS + REQUIRED_ENGINE_GROUPS
            ),
            min_size=1,
        )
    )
    @settings(max_examples=100)
    def test_malformed_svg_error_completeness(self, regions_to_remove: set):
        """Property 2: Malformed SVG Error Completeness.

        For any SVG input that is missing one or more required named regions
        or contains malformed elements, the parser SHALL return a ParseError
        that lists all missing region names and all malformed element identifiers.

        **Validates: Requirements 1.5**
        """
        # Build a complete valid SVG, then remove selected regions
        svg_parts = [
            '<svg width="1920" height="1080" viewBox="0 0 1920 1080" '
            'fill="none" xmlns="http://www.w3.org/2000/svg">',
            '<g id="Frame">',
        ]

        # Add top-level rects (only those NOT in regions_to_remove)
        for rect_id in REQUIRED_TOP_LEVEL_RECTS:
            if rect_id not in regions_to_remove:
                svg_parts.append(
                    f'<rect id="{rect_id}" x="100" y="100" width="200" height="50"/>'
                )

        # Add text groups (only those NOT in regions_to_remove)
        for group_id in REQUIRED_TEXT_GROUPS:
            if group_id not in regions_to_remove:
                svg_parts.append(f'<g id="{group_id}">')
                svg_parts.append(
                    f'<rect id="value" x="50" y="50" width="100" height="30"/>'
                )
                svg_parts.append(
                    f'<rect id="unit" x="160" y="50" width="60" height="30"/>'
                )
                svg_parts.append("</g>")

        # Add engine groups (only those NOT in regions_to_remove)
        for engine_id in REQUIRED_ENGINE_GROUPS:
            if engine_id not in regions_to_remove:
                svg_parts.append(f'<g id="{engine_id}">')
                svg_parts.append(f'<g id="{engine_id}_sub">')
                svg_parts.append(
                    f'<circle id="{engine_id}_c1" cx="500" cy="500" r="10"/>'
                )
                svg_parts.append("</g>")
                svg_parts.append("</g>")

        svg_parts.append("</g>")
        svg_parts.append("</svg>")

        svg_content = "\n".join(svg_parts)

        # Parse the SVG with missing regions
        result = parse_roi_template(svg_content, template_name="test_template")

        # Must be a ParseError since we removed required regions
        assert isinstance(result, ParseError), (
            f"Expected ParseError for missing regions {regions_to_remove}, "
            f"but got ROIConfiguration"
        )

        # Every removed region must be reported in missing_regions
        for region_name in regions_to_remove:
            assert region_name in result.missing_regions, (
                f"Region '{region_name}' was removed from SVG but not reported "
                f"in ParseError.missing_regions. "
                f"Reported: {result.missing_regions}"
            )
