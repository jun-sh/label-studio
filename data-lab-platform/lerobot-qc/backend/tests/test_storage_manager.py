"""Tests for runtime QC storage selection."""

from __future__ import annotations

from pathlib import Path

import pytest

import storage_manager as sm


def test_activate_and_restore_storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host_media = tmp_path / "host-media"
    bookduo = host_media / "BookDuo_21"
    bookduo.mkdir(parents=True)
    (bookduo / "01 - UniFranka_Converted_Ver_3_5").mkdir()

    config_path = tmp_path / "storage.json"
    monkeypatch.setattr(sm, "HOST_MEDIA_ROOT", host_media)
    monkeypatch.setattr(sm, "HOST_MEDIA_HOST_PREFIX", "/media/user01")
    monkeypatch.setattr(sm, "STORAGE_CONFIG_PATH", config_path)
    monkeypatch.setattr(sm, "LEGACY_BOOKDUO_ROOT", tmp_path / "missing-bookduo")

    volumes = sm.list_detected_volumes()
    assert len(volumes) == 1
    assert volumes[0]["status"] == "available"
    assert volumes[0]["host_path"] == "/media/user01/BookDuo_21"

    activated = sm.activate_storage_root(str(bookduo))
    assert activated["active_host_path"] == "/media/user01/BookDuo_21"
    assert sm.get_active_storage_root() == bookduo.resolve()

    status = sm.get_storage_status()
    assert status["active_root"] == str(bookduo.resolve())
    assert status["saved_available"] is True


def test_activate_rejects_missing_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host_media = tmp_path / "host-media"
    host_media.mkdir()
    monkeypatch.setattr(sm, "HOST_MEDIA_ROOT", host_media)
    monkeypatch.setattr(sm, "STORAGE_CONFIG_PATH", tmp_path / "storage.json")

    with pytest.raises(ValueError, match="not found"):
        sm.activate_storage_root(str(host_media / "BookDuo_missing"))


def test_list_detected_volumes_ignores_legacy_bookduo_bind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host_media = tmp_path / "host-media"
    bookduo = host_media / "BookDuo_2"
    bookduo.mkdir(parents=True)
    (bookduo / "01 - UniFranka_Converted_Ver_3_5").mkdir()

    legacy = tmp_path / "data" / "bookduo"
    legacy.mkdir(parents=True)
    (legacy / "01 - UniFranka_Converted_Ver_3_5").mkdir()

    monkeypatch.setattr(sm, "HOST_MEDIA_ROOT", host_media)
    monkeypatch.setattr(sm, "LEGACY_BOOKDUO_ROOT", legacy)
    monkeypatch.setattr(sm, "STORAGE_CONFIG_PATH", tmp_path / "storage.json")

    volumes = sm.list_detected_volumes()
    assert len(volumes) == 1
    assert volumes[0]["name"] == "BookDuo_2"
    assert volumes[0]["container_path"] == str(bookduo.resolve())


def test_offline_bookduo_mount_point_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host_media = tmp_path / "host-media"
    bookduo = host_media / "BookDuo_2"
    bookduo.mkdir(parents=True)

    monkeypatch.setattr(sm, "HOST_MEDIA_ROOT", host_media)
    monkeypatch.setattr(sm, "STORAGE_CONFIG_PATH", tmp_path / "storage.json")

    volumes = sm.list_detected_volumes()
    assert len(volumes) == 1
    assert volumes[0]["status"] == "offline"
    assert volumes[0]["item_count"] == 0
