"""Tests for the stale-while-revalidate collection listing cache."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import dataset_catalog


@pytest.fixture()
def fake_loader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    calls: list[str] = []
    listings: dict[str, dict] = {"coll": {"id": "coll", "packages": [{"id": "p1", "local_path": "/x/p1"}]}}

    def _load(collection_id: str):
        calls.append(collection_id)
        listing = listings.get(collection_id)
        return None if listing is None else {**listing, "packages": list(listing["packages"])}

    monkeypatch.setattr(dataset_catalog, "_load_collection_uncached", _load)
    monkeypatch.setattr(dataset_catalog, "datasets_root", lambda: tmp_path / "root")
    dataset_catalog.clear_collection_cache()
    dataset_catalog.configure_collection_cache(tmp_path / "cache")
    yield calls, listings
    dataset_catalog.clear_collection_cache()
    dataset_catalog.configure_collection_cache(None)


def test_second_call_is_cached(fake_loader) -> None:
    calls, _ = fake_loader
    dataset_catalog.get_collection("coll")
    dataset_catalog.get_collection("coll")
    assert calls == ["coll"]


def test_expired_entry_served_stale_then_refreshed(fake_loader, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, listings = fake_loader
    dataset_catalog.get_collection("coll")
    listings["coll"]["packages"].append({"id": "p2", "local_path": "/x/p2"})
    monkeypatch.setattr(dataset_catalog, "COLLECTION_CACHE_TTL_S", 0.0)

    stale = dataset_catalog.get_collection("coll")
    assert [p["id"] for p in stale["packages"]] == ["p1"]
    for _ in range(100):
        if len(calls) == 2 and not dataset_catalog._collection_refreshing:
            break
        time.sleep(0.01)
    monkeypatch.setattr(dataset_catalog, "COLLECTION_CACHE_TTL_S", 600.0)
    assert [p["id"] for p in dataset_catalog.get_collection("coll")["packages"]] == ["p1", "p2"]


def test_persisted_listing_survives_restart(fake_loader) -> None:
    calls, _ = fake_loader
    dataset_catalog.get_collection("coll")
    dataset_catalog.clear_collection_cache()  # simulate a process restart
    assert dataset_catalog.get_collection("coll")["id"] == "coll"
    assert calls == ["coll"]


def test_force_refresh_and_unknown_collection(fake_loader) -> None:
    calls, _ = fake_loader
    dataset_catalog.get_collection("coll")
    dataset_catalog.get_collection("coll", force_refresh=True)
    assert calls == ["coll", "coll"]
    with pytest.raises(KeyError):
        dataset_catalog.get_collection("missing")


def test_resolve_package_path_retries_uncached_for_new_package(fake_loader) -> None:
    calls, listings = fake_loader
    dataset_catalog.get_collection("coll")
    listings["coll"]["packages"].append({"id": "new", "local_path": "/x/new"})
    assert dataset_catalog.resolve_package_path("coll", "new") == "/x/new"
    assert calls == ["coll", "coll"]
    with pytest.raises(KeyError):
        dataset_catalog.resolve_package_path("coll", "nope")
