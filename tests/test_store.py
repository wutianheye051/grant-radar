"""保存と差分検知。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from grant_radar.models import CollectionResult, Subsidy
from grant_radar.store import Store


def make_subsidy(external_id: str = "A1", **overrides: object) -> Subsidy:
    payload: dict[str, object] = {
        "source": "jgrants",
        "external_id": external_id,
        "code": "S-0001",
        "title": "テスト補助金",
        "institution": "テスト省",
        "target_area": "全国",
        "target_employees": "従業員数の制約なし",
        "max_amount": 1_000_000,
        "accepts_from": datetime(2026, 8, 1, tzinfo=UTC),
        "accepts_until": datetime(2026, 12, 31, tzinfo=UTC),
        "url": "https://example.test/subsidy/A1",
    }
    payload.update(overrides)
    return Subsidy(**payload)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.sqlite3")


def test_first_collection_is_all_new(store):
    result = CollectionResult(source="jgrants", collected=[make_subsidy("A1"), make_subsidy("A2")])
    diff = store.apply(result)
    assert len(diff.added) == 2
    assert diff.updated == []
    assert store.count() == 2


def test_same_content_is_unchanged(store):
    result = CollectionResult(source="jgrants", collected=[make_subsidy("A1")])
    store.apply(result)
    diff = store.apply(result)
    assert diff.added == []
    assert diff.updated == []
    assert diff.unchanged == 1


def test_changed_content_is_detected_as_update(store):
    store.apply(CollectionResult(source="jgrants", collected=[make_subsidy("A1")]))
    diff = store.apply(
        CollectionResult(source="jgrants", collected=[make_subsidy("A1", title="改定後の名称")])
    )
    assert len(diff.updated) == 1
    assert diff.added == []
    assert store.count() == 1


def test_disappeared_is_detected_when_collection_succeeded(store):
    store.apply(
        CollectionResult(source="jgrants", collected=[make_subsidy("A1"), make_subsidy("A2")])
    )
    diff = store.apply(CollectionResult(source="jgrants", collected=[make_subsidy("A1")]))
    assert diff.disappeared == ["jgrants:A2"]


def test_disappeared_is_not_detected_when_collection_failed(store):
    """収集に失敗したときは「消滅」を判定しない。

    これがこのモジュールで最も重要な振る舞い。
    APIが落ちていただけの公募を「終了した」と扱うと、
    実際には募集中の案件を見落とす。
    """
    store.apply(
        CollectionResult(source="jgrants", collected=[make_subsidy("A1"), make_subsidy("A2")])
    )
    failed = CollectionResult(
        source="jgrants",
        collected=[make_subsidy("A1")],
        errors=["keyword=IT: HTTP 503"],
    )
    diff = store.apply(failed)
    assert diff.disappeared == []


def test_records_are_never_deleted(store):
    """消えても行は残す。いつまで見えていたかを後から追えるようにするため。"""
    store.apply(CollectionResult(source="jgrants", collected=[make_subsidy("A1")]))
    store.apply(CollectionResult(source="jgrants", collected=[]))
    assert store.count() == 1


def test_other_sources_are_untouched(store):
    """収集元ごとに独立して差分を取る。片方の失敗が他方に波及しない。"""
    store.apply(CollectionResult(source="jgrants", collected=[make_subsidy("A1")]))
    diff = store.apply(
        CollectionResult(source="other", collected=[make_subsidy("B1", source="other")])
    )
    assert diff.disappeared == []
    assert store.count() == 2


def test_is_open_excludes_finished(store):
    finished = make_subsidy("A1", accepts_until=datetime(2020, 1, 1, tzinfo=UTC))
    assert not finished.is_open()
    assert make_subsidy("A2").is_open()
