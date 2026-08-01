"""Template Registry for managing ROI configuration templates.

Stores and retrieves named ROI templates, providing a default template
(starship_rois) for SpaceX Starship IFT livestreams.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

from src.models import ROIConfiguration, ParseError
from src.svg_parser import parse_roi_template


DEFAULT_TEMPLATE_NAME = "starship_rois"


@dataclass
class TemplateNotFoundError:
    """Error returned when a requested template name does not exist."""

    template_name: str
    available_templates: list[str]


class TemplateRegistry:
    """Registry for storing and retrieving ROI configuration templates."""

    def __init__(self) -> None:
        self._templates: dict[str, ROIConfiguration] = {}

    def register(self, name: str, config: ROIConfiguration) -> None:
        """Register a template under the given name."""
        self._templates[name] = config

    def get(self, name: str) -> Union[ROIConfiguration, TemplateNotFoundError]:
        """Retrieve a template by name.

        Returns the ROIConfiguration if found, or TemplateNotFoundError
        if the name does not exist in the registry.
        """
        if name in self._templates:
            return self._templates[name]
        return TemplateNotFoundError(
            template_name=name,
            available_templates=list(self._templates.keys()),
        )

    def list_templates(self) -> list[str]:
        """List all registered template names."""
        return list(self._templates.keys())

    def get_default(self) -> Union[ROIConfiguration, TemplateNotFoundError]:
        """Return the default template (starship_rois).

        Returns TemplateNotFoundError if the default template has not
        been registered yet.
        """
        return self.get(DEFAULT_TEMPLATE_NAME)


def load_default_template(
    registry: TemplateRegistry,
    svg_path: Union[str, Path, None] = None,
) -> Union[None, ParseError]:
    """Load and register the default starship_rois template from the SVG file.

    Args:
        registry: The TemplateRegistry to register the template into.
        svg_path: Path to the SVG file. If None, uses the default location
                  at diagrams/starship_rois.svg relative to the project root.

    Returns:
        None on success, or ParseError if the SVG file cannot be read or parsed.
    """
    if svg_path is None:
        # Default path relative to project root (backend/../diagrams/starship_rois.svg)
        project_root = Path(__file__).parent.parent.parent
        svg_path = project_root / "diagrams" / "starship_rois.svg"
    else:
        svg_path = Path(svg_path)

    if not svg_path.exists():
        return ParseError(message=f"SVG template file not found: {svg_path}")

    svg_content = svg_path.read_text(encoding="utf-8")
    result = parse_roi_template(svg_content, template_name=DEFAULT_TEMPLATE_NAME)

    if isinstance(result, ParseError):
        return result

    registry.register(DEFAULT_TEMPLATE_NAME, result)
    return None
