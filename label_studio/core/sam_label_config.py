"""SAM 2 interactive image labeling config helpers.

Standalone utilities: parse class definitions from an existing label_config and
render a complete SAM-managed label_config XML. Does not modify core LS parsing.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from xml.etree import ElementTree as ET

import defusedxml.ElementTree as etree
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

SAM_MANAGED_ATTR = 'samManaged'
SAM_BRUSH_NAME = 'tag'
SAM_KEYPOINT_NAME = 'tag2'
SAM_RECTANGLE_NAME = 'tag3'
SAM_IMAGE_NAME = 'image'
DEFAULT_IMAGE_VALUE = '$image'
SAM_BRUSH_OPACITY = '0.3'
SAM_BRUSH_STROKE_WIDTH = '3'

DEFAULT_PALETTE = (
    '#FFA39E',
    '#40A9FF',
    '#FFC069',
    '#B37FEB',
    '#73D13D',
    '#FF85C0',
    '#36CFC9',
    '#597EF7',
    '#F759AB',
    '#9254DE',
    '#FA8C16',
    '#A0D911',
)

SAM_HIDDEN_CSS = """
    .sam-hidden {
      position: absolute !important;
      width: 1px !important;
      height: 1px !important;
      padding: 0 !important;
      margin: -1px !important;
      overflow: hidden !important;
      clip: rect(0, 0, 0, 0) !important;
      white-space: nowrap !important;
      border: 0 !important;
    }
"""

_CONTROL_TAGS = {
    SAM_BRUSH_NAME: 'BrushLabels',
    SAM_KEYPOINT_NAME: 'KeyPointLabels',
    SAM_RECTANGLE_NAME: 'RectangleLabels',
}


def default_color(index: int) -> str:
    return DEFAULT_PALETTE[index % len(DEFAULT_PALETTE)]


def _parse_xml(label_config: str) -> Optional[etree.Element]:
    if not label_config or not label_config.strip():
        return None
    try:
        return etree.fromstring(label_config)
    except etree.ParseError:
        logger.debug('Failed to parse label_config XML for SAM helpers', exc_info=True)
        return None


def _find_element(root: etree.Element, tag_name: str, name: str) -> Optional[etree.Element]:
    for element in root.iter(tag_name):
        if element.get('name') == name:
            return element
    return None


def _labels_from_control(element: Optional[etree.Element]) -> list[dict[str, str]]:
    if element is None:
        return []

    labels = []
    for index, label in enumerate(element.findall('Label')):
        value = (label.get('value') or '').strip()
        if not value:
            continue
        item: dict[str, str] = {'value': value}
        background = label.get('background')
        if background:
            item['background'] = background
        else:
            item['background'] = default_color(index)
        alias = label.get('alias')
        if alias:
            item['alias'] = alias
        labels.append(item)
    return labels


def is_sam_managed_config(label_config: str) -> bool:
    root = _parse_xml(label_config)
    if root is None or root.tag != 'View':
        return False
    return root.get(SAM_MANAGED_ATTR) == 'true'


def is_sam_structure_config(label_config: str) -> bool:
    root = _parse_xml(label_config)
    if root is None:
        return False
    for control_name, tag_name in _CONTROL_TAGS.items():
        if _find_element(root, tag_name, control_name) is None:
            return False
    return _find_element(root, 'Image', SAM_IMAGE_NAME) is not None


def parse_image_settings(label_config: str) -> dict[str, str]:
    root = _parse_xml(label_config)
    if root is None:
        return {'name': SAM_IMAGE_NAME, 'value': DEFAULT_IMAGE_VALUE}

    image = _find_element(root, 'Image', SAM_IMAGE_NAME)
    if image is None:
        for element in root.iter('Image'):
            return {
                'name': element.get('name') or SAM_IMAGE_NAME,
                'value': element.get('value') or DEFAULT_IMAGE_VALUE,
            }
        return {'name': SAM_IMAGE_NAME, 'value': DEFAULT_IMAGE_VALUE}

    return {
        'name': image.get('name') or SAM_IMAGE_NAME,
        'value': image.get('value') or DEFAULT_IMAGE_VALUE,
    }


def parse_classes(label_config: str) -> list[dict[str, str]]:
    """Read canonical class list from BrushLabels ``tag``."""
    root = _parse_xml(label_config)
    if root is None:
        return []
    brush = _find_element(root, 'BrushLabels', SAM_BRUSH_NAME)
    return _labels_from_control(brush)


def classes_in_sync(label_config: str) -> bool:
    root = _parse_xml(label_config)
    if root is None:
        return True

    brush = _labels_from_control(_find_element(root, 'BrushLabels', SAM_BRUSH_NAME))
    keypoint = _labels_from_control(_find_element(root, 'KeyPointLabels', SAM_KEYPOINT_NAME))
    rectangle = _labels_from_control(_find_element(root, 'RectangleLabels', SAM_RECTANGLE_NAME))

    if not brush:
        return not keypoint and not rectangle

    def _signature(labels: list[dict[str, str]]) -> list[tuple[str, str]]:
        return [(item['value'], item.get('background', '')) for item in labels]

    signature = _signature(brush)
    return signature == _signature(keypoint) and signature == _signature(rectangle)


def normalize_classes(classes: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not classes:
        raise ValidationError('At least one class is required')

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()

    for index, item in enumerate(classes):
        if not isinstance(item, dict):
            raise ValidationError('Each class must be an object')

        value = str(item.get('value', '')).strip()
        if not value:
            raise ValidationError('Class value cannot be empty')
        if value in seen:
            raise ValidationError(f'Duplicate class value: {value}')
        seen.add(value)

        background = str(item.get('background', '')).strip() or default_color(index)
        normalized_item: dict[str, str] = {'value': value, 'background': background}
        alias = item.get('alias')
        if alias:
            normalized_item['alias'] = str(alias).strip()
        normalized.append(normalized_item)

    return normalized


def _append_label(parent: ET.Element, label: dict[str, str]) -> None:
    element = ET.SubElement(parent, 'Label')
    element.set('value', label['value'])
    element.set('background', label['background'])
    alias = label.get('alias')
    if alias:
        element.set('alias', alias)


def _append_labels_control(
    parent: ET.Element,
    tag_name: str,
    name: str,
    to_name: str,
    classes: list[dict[str, str]],
    *,
    smart: bool = False,
    smartonly: bool = False,
    opacity: Optional[str] = None,
    stroke_width: Optional[str] = None,
) -> ET.Element:
    control = ET.SubElement(parent, tag_name)
    control.set('name', name)
    control.set('toName', to_name)
    if smart:
        control.set('smart', 'true')
    if smartonly:
        control.set('smartonly', 'true')
    if opacity is not None:
        control.set('opacity', opacity)
    if stroke_width is not None:
        control.set('strokeWidth', stroke_width)
    for label in classes:
        _append_label(control, label)
    return control


def render_sam_config(
    classes: list[dict[str, str]],
    *,
    image_name: str = SAM_IMAGE_NAME,
    image_value: str = DEFAULT_IMAGE_VALUE,
) -> str:
    """Build a complete SAM-managed labeling config XML string."""
    root = ET.Element('View')
    root.set(SAM_MANAGED_ATTR, 'true')

    style = ET.SubElement(root, 'Style')
    style.text = SAM_HIDDEN_CSS

    inner = ET.SubElement(root, 'View')

    header = ET.SubElement(inner, 'Header')
    header.set('value', 'Choose label')

    _append_labels_control(
        inner,
        'BrushLabels',
        SAM_BRUSH_NAME,
        image_name,
        classes,
        opacity=SAM_BRUSH_OPACITY,
        stroke_width=SAM_BRUSH_STROKE_WIDTH,
    )

    hidden = ET.SubElement(inner, 'View')
    hidden.set('className', 'sam-hidden')

    _append_labels_control(
        hidden,
        'KeyPointLabels',
        SAM_KEYPOINT_NAME,
        image_name,
        classes,
        smart=True,
        smartonly=True,
    )
    _append_labels_control(
        hidden,
        'RectangleLabels',
        SAM_RECTANGLE_NAME,
        image_name,
        classes,
        smart=True,
        smartonly=True,
    )

    image = ET.SubElement(inner, 'Image')
    image.set('name', image_name)
    image.set('value', image_value)
    image.set('zoom', 'true')
    image.set('zoomControl', 'true')

    return ET.tostring(root, encoding='unicode')


def project_has_interactive_ml_backend(project) -> bool:
    from ml.models import MLBackend

    return MLBackend.objects.filter(project=project, is_interactive=True).exists()


def is_sam_classes_feature_enabled(project) -> bool:
    label_config = project.label_config or ''
    if is_sam_managed_config(label_config):
        return True
    return project_has_interactive_ml_backend(project)


def build_sam_classes_response(project) -> dict[str, Any]:
    label_config = project.label_config or ''
    image = parse_image_settings(label_config)
    return {
        'enabled': is_sam_classes_feature_enabled(project),
        'sam_managed': is_sam_managed_config(label_config),
        'classes': parse_classes(label_config),
        'image_field': image['name'],
        'image_value': image['value'],
        'in_sync': classes_in_sync(label_config),
    }
