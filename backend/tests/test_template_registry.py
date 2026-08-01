"""Unit tests and property-based tests for the Template Registry module."""

import pytest
from hypothesis import given, settings
from hypothesis.strategies import (
    composite,
    floats,
    integers,
    just,
    lists,
    text,
)

from src.models import ROIConfiguration, ROIRect, ROICircle, EngineSubgroup, EngineGroup
from src.template_registry import (
    DEFAULT_TEMPLATE_NAME,
    TemplateNotFoundError,
    TemplateRegistry,
    load_default_template,
)


@pytest.fixture
def sample_config() -> ROIConfiguration:
    """Create a minimal ROIConfiguration for testing."""
    return ROIConfiguration(
        template_name="test_template",
        view_box=(1920, 1080),
        text_regions={
            "time": ROIRect(id="time", x=0, y=0, width=100, height=50),
        },
        engine_groups=[],
    )


@pytest.fixture
def registry() -> TemplateRegistry:
    """Create an empty TemplateRegistry."""
    return TemplateRegistry()


class TestRegisterAndGet:
    """Tests for registering and retrieving templates."""

    def test_register_and_get_template(
        self, registry: TemplateRegistry, sample_config: ROIConfiguration
    ):
        registry.register("my_template", sample_config)
        result = registry.get("my_template")
        assert result == sample_config

    def test_register_overwrites_existing(
        self, registry: TemplateRegistry, sample_config: ROIConfiguration
    ):
        other_config = ROIConfiguration(
            template_name="other",
            view_box=(1920, 1080),
            text_regions={},
            engine_groups=[],
        )
        registry.register("my_template", sample_config)
        registry.register("my_template", other_config)
        result = registry.get("my_template")
        assert result == other_config


class TestListTemplates:
    """Tests for listing registered templates."""

    def test_empty_registry_returns_empty_list(self, registry: TemplateRegistry):
        assert registry.list_templates() == []

    def test_lists_registered_templates(
        self, registry: TemplateRegistry, sample_config: ROIConfiguration
    ):
        registry.register("template_a", sample_config)
        registry.register("template_b", sample_config)
        names = registry.list_templates()
        assert sorted(names) == ["template_a", "template_b"]


class TestTemplateNotFoundError:
    """Tests for missing template error behavior."""

    def test_get_missing_template_returns_error(self, registry: TemplateRegistry):
        result = registry.get("nonexistent")
        assert isinstance(result, TemplateNotFoundError)
        assert result.template_name == "nonexistent"
        assert result.available_templates == []

    def test_error_includes_available_templates(
        self, registry: TemplateRegistry, sample_config: ROIConfiguration
    ):
        registry.register("existing_one", sample_config)
        result = registry.get("missing")
        assert isinstance(result, TemplateNotFoundError)
        assert result.template_name == "missing"
        assert "existing_one" in result.available_templates


class TestGetDefault:
    """Tests for the default template behavior."""

    def test_get_default_returns_error_when_not_registered(
        self, registry: TemplateRegistry
    ):
        result = registry.get_default()
        assert isinstance(result, TemplateNotFoundError)
        assert result.template_name == DEFAULT_TEMPLATE_NAME

    def test_get_default_returns_config_when_registered(
        self, registry: TemplateRegistry, sample_config: ROIConfiguration
    ):
        registry.register(DEFAULT_TEMPLATE_NAME, sample_config)
        result = registry.get_default()
        assert result == sample_config


class TestLoadDefaultTemplate:
    """Tests for loading the default template from the SVG file on disk."""

    def test_load_default_template_from_svg(self, registry: TemplateRegistry):
        result = load_default_template(registry)
        assert result is None  # No error
        config = registry.get(DEFAULT_TEMPLATE_NAME)
        assert isinstance(config, ROIConfiguration)
        assert config.template_name == DEFAULT_TEMPLATE_NAME

    def test_load_default_template_missing_file(self, registry: TemplateRegistry, tmp_path):
        from src.models import ParseError

        result = load_default_template(registry, svg_path=tmp_path / "missing.svg")
        assert isinstance(result, ParseError)
        assert "not found" in result.message


# --- Property-Based Tests (Hypothesis) ---

# Strategy for generating non-empty identifiers
_id_strategy = text(
    min_size=1,
    max_size=20,
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_",
)

# Strategy for generating template names (non-empty, printable)
_name_strategy = text(
    min_size=1,
    max_size=30,
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
)


@composite
def roi_rects(draw):
    """Generate an arbitrary valid ROIRect."""
    return ROIRect(
        id=draw(_id_strategy),
        x=draw(floats(min_value=0.0, max_value=1920.0, allow_nan=False, allow_infinity=False)),
        y=draw(floats(min_value=0.0, max_value=1080.0, allow_nan=False, allow_infinity=False)),
        width=draw(floats(min_value=1.0, max_value=1920.0, allow_nan=False, allow_infinity=False)),
        height=draw(floats(min_value=1.0, max_value=1080.0, allow_nan=False, allow_infinity=False)),
    )


@composite
def roi_circles(draw):
    """Generate an arbitrary valid ROICircle."""
    return ROICircle(
        id=draw(_id_strategy),
        cx=draw(floats(min_value=0.0, max_value=1920.0, allow_nan=False, allow_infinity=False)),
        cy=draw(floats(min_value=0.0, max_value=1080.0, allow_nan=False, allow_infinity=False)),
        r=draw(floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False)),
    )


@composite
def engine_subgroups(draw):
    """Generate an arbitrary valid EngineSubgroup."""
    return EngineSubgroup(
        name=draw(_name_strategy),
        circles=draw(lists(roi_circles(), min_size=0, max_size=5)),
    )


@composite
def engine_groups(draw):
    """Generate an arbitrary valid EngineGroup."""
    return EngineGroup(
        group_id=draw(_id_strategy),
        bounding_box=draw(roi_rects()),
        subgroups=draw(lists(engine_subgroups(), min_size=0, max_size=3)),
    )


@composite
def roi_configurations(draw):
    """Generate an arbitrary valid ROIConfiguration."""
    # Generate text_regions as a dict of unique keys to ROIRects
    num_regions = draw(integers(min_value=0, max_value=5))
    text_regions = {}
    for i in range(num_regions):
        key = draw(_id_strategy)
        text_regions[key] = draw(roi_rects())

    return ROIConfiguration(
        template_name=draw(_name_strategy),
        view_box=(1920, 1080),
        text_regions=text_regions,
        engine_groups=draw(lists(engine_groups(), min_size=0, max_size=3)),
    )


class TestPropertyTemplateRegistryRoundTrip:
    """Property-based tests for Template Registry.

    **Validates: Requirements 8.1**
    """

    @given(config=roi_configurations(), name=_name_strategy)
    @settings(max_examples=100)
    def test_template_registry_round_trip(
        self, config: ROIConfiguration, name: str
    ):
        """Property 14: Template Registry Round-Trip.

        For any ROIConfiguration registered in the Template Registry under a
        given name, retrieving that template by the same name SHALL return an
        equivalent ROIConfiguration.

        **Validates: Requirements 8.1**
        """
        registry = TemplateRegistry()
        registry.register(name, config)
        result = registry.get(name)
        assert isinstance(result, ROIConfiguration), (
            f"Expected ROIConfiguration but got {type(result).__name__}: {result}"
        )
        assert result == config
