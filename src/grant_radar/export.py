"""収集結果の出力。

CSV は Excel で開かれる前提で作る。ここは別プロジェクト（StockDesk）でも
同じ判断をしている。出力先が Excel である限り、必要な対処は変わらない。

- **UTF-8 BOM を付ける**: 付けないと Excel が Shift_JIS と誤認して日本語が化ける
- **改行は CRLF**（RFC 4180）
- **``=`` ``+`` ``-`` ``@`` 始まりのセルを無害化**: Excel はこれらを数式として実行する。
  公募の件名は外部から来る文字列なので、そのまま書き出してはいけない
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from .models import Subsidy
from .store import Diff

_RISKY_PREFIX = ("=", "+", "-", "@", "\t", "\r")

CSV_COLUMNS: tuple[tuple[str, str], ...] = (
    ("公募番号", "code"),
    ("件名", "title"),
    ("実施機関", "institution"),
    ("対象地域", "target_area"),
    ("従業員数の条件", "target_employees"),
    ("上限額(円)", "max_amount"),
    ("受付開始", "accepts_from"),
    ("受付終了", "accepts_until"),
    ("URL", "url"),
)


def _cell(value: object) -> str:
    if value is None:
        return ""
    text = value.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime) else str(value)
    if text.startswith(_RISKY_PREFIX):
        text = "'" + text
    return text


def to_csv(subsidies: Iterable[Subsidy]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([header for header, _ in CSV_COLUMNS])
    for subsidy in subsidies:
        writer.writerow([_cell(getattr(subsidy, attr)) for _, attr in CSV_COLUMNS])
    return "\ufeff" + buffer.getvalue()


def write_csv(path: Path, subsidies: Iterable[Subsidy]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" にしないと、Windows で CRLF が CRCRLF になる
    path.write_text(to_csv(subsidies), encoding="utf-8", newline="")


def write_json(path: Path, subsidies: Iterable[Subsidy]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [s.model_dump(mode="json") for s in subsidies]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def to_markdown(subsidies: list[Subsidy], diff: Diff, *, generated_at: datetime) -> str:
    """人が読むための要約。定期実行の結果をそのままコミットして差分を追えるようにする。"""
    lines = [
        "# 受付中の公募一覧",
        "",
        f"最終更新: {generated_at.strftime('%Y-%m-%d %H:%M')} (UTC)  ",
        f"件数: {len(subsidies)}  ",
        f"前回からの差分: 新規 {len(diff.added)} / 更新 {len(diff.updated)} / "
        f"掲載終了 {len(diff.disappeared)}",
        "",
        "> このファイルは自動生成されています。",
        "> 出典: [jGrants 補助金電子申請システム](https://www.jgrants-portal.go.jp/)"
        "（[公開API](https://developers.digital.go.jp/documents/jgrants/api/) 経由で取得）",
        "",
    ]

    if diff.added:
        lines += ["## 新着", ""]
        lines += [f"- {_markdown_row(s)}" for s in diff.added]
        lines += [""]

    lines += [
        "## すべて",
        "",
        "| 受付終了 | 件名 | 実施機関 | 対象地域 | 上限額 |",
        "|---|---|---|---|---|",
    ]
    for s in subsidies:
        until = s.accepts_until.strftime("%Y-%m-%d") if s.accepts_until else "—"
        amount = f"{s.max_amount:,}円" if s.max_amount else "—"
        title = f"[{_escape(s.title)}]({s.url})" if s.url else _escape(s.title)
        lines.append(
            f"| {until} | {title} | {_escape(s.institution or '—')} | "
            f"{_escape(s.target_area or '—')} | {amount} |"
        )

    return "\n".join(lines) + "\n"


def _markdown_row(subsidy: Subsidy) -> str:
    until = subsidy.accepts_until.strftime("%Y-%m-%d") if subsidy.accepts_until else "期限未定"
    title = f"[{_escape(subsidy.title)}]({subsidy.url})" if subsidy.url else _escape(subsidy.title)
    return f"{title}（〜{until}）"


def _escape(text: str) -> str:
    """表のセルを壊さないようにする。"""
    return text.replace("|", "\\|").replace("\n", " ")
