"""CSV 出力。Excel で開かれる前提の要件を守れているかを見る。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from grant_radar.export import to_csv
from grant_radar.models import Subsidy


def make(title: str = "テスト補助金", **overrides: object) -> Subsidy:
    payload: dict[str, object] = {
        "source": "jgrants",
        "external_id": "A1",
        "title": title,
        "max_amount": 1_000_000,
        "accepts_until": datetime(2026, 12, 31, 9, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return Subsidy(**payload)  # type: ignore[arg-type]


def data_rows(csv_text: str) -> list[str]:
    return csv_text.removeprefix("﻿").rstrip("\r\n").split("\r\n")[1:]


def test_has_bom():
    """BOM が無いと Excel が Shift_JIS と誤認して日本語が化ける。"""
    assert to_csv([make()]).startswith("﻿")


def test_uses_crlf():
    csv_text = to_csv([make()])
    assert "\r\n" in csv_text
    assert "\n" not in csv_text.replace("\r\n", "")


def test_quotes_values_containing_comma():
    row = data_rows(to_csv([make(title="補助金,特別枠")]))[0]
    assert '"補助金,特別枠"' in row


@pytest.mark.parametrize("payload", ["=1+1", "+1", "-1", "@SUM(A1)"])
def test_neutralizes_formula_injection(payload):
    """公募の件名は外部から来る文字列。Excel が数式として実行しないようにする。"""
    row = data_rows(to_csv([make(title=payload)]))[0]
    assert "'" + payload in row


def test_empty_values_become_blank():
    row = data_rows(to_csv([make(institution=None, max_amount=None)]))[0]
    assert ",," in row


def test_header_is_written_even_without_rows():
    csv_text = to_csv([])
    assert csv_text.removeprefix("﻿").startswith("公募番号,件名")


def test_zero_amount_is_treated_as_unknown():
    """API は上限未設定を 0 で返す。

    0 のまま保存すると「上限0円の公募」として集計され、
    金額での絞り込みが静かに壊れる。
    """
    assert make(max_amount=0).max_amount is None
