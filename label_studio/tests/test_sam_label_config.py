"""Tests for SAM-managed labeling config helpers and API."""
from pathlib import Path

import defusedxml.ElementTree as etree
import pytest
from core.label_config import validate_label_config
from core.sam_label_config import (
    SAM_BRUSH_NAME,
    SAM_KEYPOINT_NAME,
    SAM_RECTANGLE_NAME,
    build_sam_classes_response,
    classes_in_sync,
    is_sam_managed_config,
    is_sam_structure_config,
    normalize_classes,
    parse_classes,
    project_has_interactive_ml_backend,
    render_sam_config,
)
from django.urls import reverse
from ml.models import MLBackend
from tests.utils import make_project

REPO_ROOT = Path(__file__).resolve().parents[2]
MANUAL_TEMPLATE_PATH = REPO_ROOT / 'ml-services' / 'sam2-labeling-config.xml'

REFERENCE_CLASSES = [
    {'value': 'Bowl', 'background': '#FFA39E'},
    {'value': 'Cup', 'background': '#40A9FF'},
    {'value': 'Knife', 'background': '#FFC069'},
]

EXTENDED_CLASSES = REFERENCE_CLASSES + [
    {'value': 'Plate', 'background': '#B37FEB'},
    {'value': 'Fork', 'background': '#73D13D'},
    {'value': 'Spoon', 'background': '#FF85C0'},
]


@pytest.fixture
def manual_template_xml():
    return MANUAL_TEMPLATE_PATH.read_text(encoding='utf-8')


def _control_labels(xml: str, control_name: str, tag_name: str) -> list[tuple[str, str]]:
    root = etree.fromstring(xml)
    for element in root.iter(tag_name):
        if element.get('name') == control_name:
            return [
                (label.get('value'), label.get('background'))
                for label in element.findall('Label')
            ]
    return []


def test_render_matches_manual_template_structure(manual_template_xml):
    generated = render_sam_config(REFERENCE_CLASSES)
    validate_label_config(generated)

    assert is_sam_managed_config(generated)
    assert is_sam_structure_config(generated)
    assert classes_in_sync(generated)

    manual_signature = _control_labels(manual_template_xml, SAM_BRUSH_NAME, 'BrushLabels')
    for control_name, tag_name in (
        (SAM_BRUSH_NAME, 'BrushLabels'),
        (SAM_KEYPOINT_NAME, 'KeyPointLabels'),
        (SAM_RECTANGLE_NAME, 'RectangleLabels'),
    ):
        assert _control_labels(generated, control_name, tag_name) == manual_signature

    root = etree.fromstring(generated)
    image = next(element for element in root.iter('Image') if element.get('name') == 'image')
    assert image.get('value') == '$image'
    assert image.get('zoom') == 'true'
    assert image.get('zoomControl') == 'true'

    hidden = next(element for element in root.iter('View') if element.get('className') == 'sam-hidden')
    keypoint = hidden.find('KeyPointLabels')
    rectangle = hidden.find('RectangleLabels')
    assert keypoint is not None and keypoint.get('smart') == 'true' and keypoint.get('smartonly') == 'true'
    assert rectangle is not None and rectangle.get('smart') == 'true' and rectangle.get('smartonly') == 'true'

    brush = next(element for element in root.iter('BrushLabels') if element.get('name') == SAM_BRUSH_NAME)
    assert brush.get('opacity') == '0.3'
    assert brush.get('strokeWidth') == '3'


def test_parse_classes_from_manual_template(manual_template_xml):
    classes = parse_classes(manual_template_xml)
    assert classes == REFERENCE_CLASSES


def test_render_parse_roundtrip():
    generated = render_sam_config(EXTENDED_CLASSES)
    assert parse_classes(generated) == EXTENDED_CLASSES


def test_classes_in_sync_detects_drift():
    generated = render_sam_config(REFERENCE_CLASSES)
    assert classes_in_sync(generated)

    drifted = generated.replace('#40A9FF', '#000000', 1)
    assert not classes_in_sync(drifted)


def test_normalize_classes_rejects_duplicates_and_empty_values():
    with pytest.raises(Exception):
        normalize_classes([{'value': 'A'}, {'value': 'A'}])

    with pytest.raises(Exception):
        normalize_classes([{'value': '  '}])


@pytest.mark.django_db
def test_get_sam_classes_disabled_for_plain_project(business_client):
    project = make_project(
        {'label_config': '<View><Text name="text" value="$text"/></View>'},
        business_client.user,
        use_ml_backend=False,
    )

    response = business_client.get(reverse('projects:api:project-sam-classes', kwargs={'pk': project.id}))
    assert response.status_code == 200
    payload = response.json()
    assert payload['enabled'] is False
    assert payload['sam_managed'] is False
    assert payload['classes'] == []


@pytest.mark.django_db
def test_put_sam_classes_with_interactive_backend(business_client):
    project = make_project({}, business_client.user, use_ml_backend=False)
    MLBackend.objects.create(project=project, url='http://localhost:9090', is_interactive=True)

    response = business_client.put(
        reverse('projects:api:project-sam-classes', kwargs={'pk': project.id}),
        data={'classes': EXTENDED_CLASSES},
        content_type='application/json',
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload['enabled'] is True
    assert payload['sam_managed'] is True
    assert payload['in_sync'] is True
    assert payload['classes'] == EXTENDED_CLASSES
    assert payload['config_essential_data_has_changed'] is True

    project.refresh_from_db()
    assert is_sam_managed_config(project.label_config)
    assert parse_classes(project.label_config) == EXTENDED_CLASSES
    validate_label_config(project.label_config)
    assert 'tag' in project.parsed_label_config
    assert project.parsed_label_config['tag']['labels'] == [item['value'] for item in EXTENDED_CLASSES]


@pytest.mark.django_db
def test_put_rejected_without_feature_enabled(business_client):
    project = make_project(
        {'label_config': '<View><Text name="text" value="$text"/></View>'},
        business_client.user,
        use_ml_backend=False,
    )

    response = business_client.put(
        reverse('projects:api:project-sam-classes', kwargs={'pk': project.id}),
        data={'classes': REFERENCE_CLASSES},
        content_type='application/json',
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_get_sam_classes_for_managed_config(business_client, manual_template_xml):
    managed_xml = render_sam_config(REFERENCE_CLASSES)
    project = make_project({'label_config': managed_xml}, business_client.user, use_ml_backend=False)
    project.validate_config(managed_xml, strict=True)
    project.save()

    response = business_client.get(reverse('projects:api:project-sam-classes', kwargs={'pk': project.id}))
    assert response.status_code == 200
    payload = response.json()
    assert payload['enabled'] is True
    assert payload['sam_managed'] is True
    assert payload['classes'] == REFERENCE_CLASSES
    assert payload['in_sync'] is True
    assert project_has_interactive_ml_backend(project) is False
    assert build_sam_classes_response(project) == payload
