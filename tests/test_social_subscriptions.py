"""Subscription store CRUD and catalog merge."""

from __future__ import annotations

from pathlib import Path

from apps.aggregator.social_catalog import build_catalog, subscription_to_info
from apps.aggregator.social_subscriptions import (
    SocialSubscriptionStore,
    normalize_handle,
    social_source_id,
)


def test_normalize_handle() -> None:
    assert normalize_handle("reddit", "r/OverEmployed") == "overemployed"
    assert normalize_handle("reddit", "/r/jobsearchhacks/") == "jobsearchhacks"
    assert normalize_handle("x", "@SomeHandle") == "somehandle"
    assert social_source_id("reddit", "r/Foo") == "social-reddit-foo"


def test_store_round_trip(tmp_path: Path) -> None:
    store = SocialSubscriptionStore(tmp_path / "subs.json")
    assert store.list() == []

    entry = store.add("reddit", "r/overemployed", origin="catalog", use_cases=["overemployment"])
    assert entry.id == "overemployed"
    assert entry.display_name() == "r/overemployed"
    store.add("x", "@ThreatWire")

    subs = store.list()
    assert len(subs) == 2
    assert [s.id for s in store.enabled("reddit")] == ["overemployed"]
    assert [s.id for s in store.enabled("x")] == ["threatwire"]

    assert store.remove("reddit", "overemployed") is True
    assert store.remove("reddit", "overemployed") is False
    assert store.enabled("reddit") == []


def test_add_is_idempotent(tmp_path: Path) -> None:
    store = SocialSubscriptionStore(tmp_path / "subs.json")
    store.add("reddit", "jobsearchhacks")
    store.add("reddit", "R/JobSearchHacks")
    assert len(store.list()) == 1


def test_malformed_file_treated_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "subs.json"
    path.write_text("not json", encoding="utf-8")
    assert SocialSubscriptionStore(path).list() == []


def test_catalog_covers_all_use_cases() -> None:
    catalog = build_catalog()
    ids = {info.id for info in catalog if info.platform == "reddit"}
    assert {"overemployed", "jobsearchhacks"} <= ids
    covered = {uc for info in catalog for uc in info.use_cases}
    assert covered == {
        "overemployment",
        "data-exfiltration",
        "credential-misuse",
        "shadow-it",
    }
    # sysadmin is suggested for multiple use cases but appears once
    sysadmin = [i for i in catalog if i.platform == "reddit" and i.id == "sysadmin"]
    assert len(sysadmin) == 1
    assert len(sysadmin[0].use_cases) >= 2


def test_subscription_to_info(tmp_path: Path) -> None:
    store = SocialSubscriptionStore(tmp_path / "subs.json")
    entry = store.add("reddit", "overemployed", use_cases=["overemployment"])
    info = subscription_to_info(entry)
    assert info.subscribed is True
    assert info.source_id == "social-reddit-overemployed"
    assert info.url == "https://www.reddit.com/r/overemployed/"
