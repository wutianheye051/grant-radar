"""コマンドラインの結合テスト。

各モジュールが正しくても、配線が間違っていれば動かない。
ここでは実際に collect を通して、保存・出力・終了コードまでを確認する。
"""

from __future__ import annotations

import httpx
import pytest
import respx

from grant_radar.cli import main
from grant_radar.sources.jgrants import API_ENDPOINT

RECORD = {
    "id": "a0W0000000TEST",
    "name": "S-00000001",
    "title": "テスト補助金",
    "institution_name": "テスト省",
    "target_area_search": "全国",
    "target_number_of_employees": "従業員数の制約なし",
    "subsidy_max_limit": 3_000_000,
    "acceptance_start_datetime": "2026-08-01T00:00:00.000Z",
    "acceptance_end_datetime": "2099-12-31T00:00:00.000Z",
}


def ok(records: list[dict[str, object]]) -> httpx.Response:
    return httpx.Response(
        200, json={"metadata": {"resultset": {"count": len(records)}}, "result": records}
    )


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@respx.mock
def test_collect_writes_outputs(workspace):
    respx.get(API_ENDPOINT).mock(return_value=ok([RECORD]))

    code = main(
        ["--db", str(workspace / "t.sqlite3"), "collect", "--export", "--min-interval", "0"]
    )

    assert code == 0
    data = workspace / "data"
    assert (data / "subsidies.json").exists()
    assert (data / "subsidies.csv").exists()
    assert (data / "README.md").exists()
    assert (data / "subsidies.csv").read_text(encoding="utf-8").startswith("﻿")
    assert "テスト補助金" in (data / "README.md").read_text(encoding="utf-8")


@respx.mock
def test_collect_returns_1_on_partial_failure(workspace):
    """一部が失敗したら終了コード1。定期実行から異常を検知できるようにする。"""
    respx.get(API_ENDPOINT).mock(return_value=httpx.Response(500))

    code = main(["--db", str(workspace / "t.sqlite3"), "collect", "--min-interval", "0"])

    assert code == 1


@respx.mock
def test_second_run_reports_no_changes(workspace, capsys):
    """2回目の実行で差分が0になること。"""
    respx.get(API_ENDPOINT).mock(return_value=ok([RECORD]))
    db = str(workspace / "t.sqlite3")

    main(["--db", db, "collect", "--min-interval", "0"])
    capsys.readouterr()
    main(["--db", db, "collect", "--min-interval", "0"])

    output = capsys.readouterr().out
    assert "新規       : 0" in output


@respx.mock
def test_list_shows_saved_subsidies(workspace, capsys):
    respx.get(API_ENDPOINT).mock(return_value=ok([RECORD]))
    db = str(workspace / "t.sqlite3")
    main(["--db", db, "collect", "--min-interval", "0"])
    capsys.readouterr()

    assert main(["--db", db, "list"]) == 0
    assert "テスト補助金" in capsys.readouterr().out


def test_list_without_data_is_not_an_error(workspace, capsys):
    """まだ収集していなくても落ちない。"""
    assert main(["--db", str(workspace / "empty.sqlite3"), "list"]) == 0
    assert "先に" in capsys.readouterr().out
