"""jGrants 収集元。実際のAPIには接続せず、応答を模して検証する。"""

from __future__ import annotations

import httpx
import pytest
import respx

from grant_radar.http import HttpClient, RetryPolicy
from grant_radar.sources.jgrants import API_ENDPOINT, JGrantsSource


def api_response(records: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"metadata": {"resultset": {"count": len(records)}}, "result": records},
    )


RECORD = {
    "id": "a0WJ200000CDdbOMAT",
    "name": "S-00009541",
    "title": "経済安全保障重要技術育成プログラムの公募",
    "institution_name": "",
    "target_area_search": "全国",
    "target_number_of_employees": "従業員数の制約なし",
    "subsidy_max_limit": 0,
    "acceptance_start_datetime": "2026-07-29T01:00:00.000Z",
    "acceptance_end_datetime": "2026-08-12T03:00:00.000Z",
}


@pytest.fixture
def client():
    with HttpClient(min_interval=0.0, retry=RetryPolicy(max_attempts=1)) as c:
        yield c


@respx.mock
def test_maps_api_record_to_subsidy(client):
    respx.get(API_ENDPOINT).mock(return_value=api_response([RECORD]))

    result = JGrantsSource(keywords=("IT",)).collect(client)

    assert result.succeeded
    assert len(result.collected) == 1
    subsidy = result.collected[0]
    assert subsidy.source == "jgrants"
    assert subsidy.code == "S-00009541"
    assert subsidy.institution is None  # 空文字は None に寄せる
    assert subsidy.max_amount is None  # 0 は「未設定」の意味
    assert subsidy.accepts_until is not None
    assert subsidy.url.endswith(RECORD["id"])


@respx.mock
def test_deduplicates_across_keywords(client):
    """このAPIは keyword が必須で全件取得ができない。

    複数キーワードで引くため同じ公募が重複する。排除できていること。
    """
    respx.get(API_ENDPOINT).mock(return_value=api_response([RECORD]))

    result = JGrantsSource(keywords=("IT", "システム", "デジタル")).collect(client)

    assert len(result.collected) == 1


@respx.mock
def test_partial_failure_keeps_successful_results(client):
    """一部のキーワードが失敗しても、取れた分は捨てない。

    全部を失うより、取れた分を残して「欠けている」と記録する方が使える。
    """
    other = dict(RECORD, id="OTHER", title="別の公募")
    respx.get(API_ENDPOINT).mock(side_effect=[httpx.Response(500), api_response([other])])

    result = JGrantsSource(keywords=("IT", "システム")).collect(client)

    assert not result.succeeded
    assert len(result.errors) == 1
    assert len(result.collected) == 1


@respx.mock
def test_records_without_id_or_title_are_dropped(client):
    """ID も件名も無いものは後で参照も表示もできない。"""
    respx.get(API_ENDPOINT).mock(return_value=api_response([{"id": "", "title": ""}, RECORD]))

    result = JGrantsSource(keywords=("IT",)).collect(client)

    assert len(result.collected) == 1


@respx.mock
def test_unexpected_shape_is_reported_not_crashed(client):
    """APIの形が変わっても落とさず、失敗として記録する。"""
    respx.get(API_ENDPOINT).mock(return_value=httpx.Response(200, json={"result": "文字列"}))

    result = JGrantsSource(keywords=("IT",)).collect(client)

    assert not result.succeeded
    assert result.collected == []
