"""jGrants（補助金電子申請システム / デジタル庁）からの収集。

**なぜスクレイピングしないのか**

https://www.jgrants-portal.go.jp/robots.txt は次の内容だった（2026-08 時点）。

    User-agent: *
    Disallow: /
    Allow: /index.html

トップページ以外のクロールが明確に禁止されている。したがって画面を解析して
取得してはならない。一方で、デジタル庁は同じ情報を取得できる公開APIを提供している。

    https://developers.digital.go.jp/documents/jgrants/api/

「取れるかどうか」ではなく「取ってよいか」で手段を決めた結果、APIを使っている。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, ClassVar

from ..http import FetchError, HttpClient
from ..models import CollectionResult, Subsidy
from .base import AccessMethod, Source

logger = logging.getLogger(__name__)

API_ENDPOINT = "https://api.jgrants-portal.go.jp/exp/v1/public/subsidies"
DETAIL_URL_TEMPLATE = "https://www.jgrants-portal.go.jp/subsidy/{id}"

DEFAULT_KEYWORDS: tuple[str, ...] = (
    "IT",
    "システム",
    "デジタル",
    "設備",
    "人材",
    "販路",
    "省エネ",
    "研究開発",
)
"""検索キーワード。

このAPIは ``keyword`` が必須で、全件を一度に取得する方法がない。
そのため複数のキーワードで引いて結果を統合する。当然ながら重複するので、
``Subsidy.key`` で排除する。キーワードを増やせば網羅性は上がるが、
その分だけAPIへのリクエストが増えるため、意味のある範囲に留めている。
"""


def _parse_datetime(value: Any) -> datetime | None:
    """API の日時（例: ``2026-08-12T03:00:00.000Z``）を datetime にする。

    Python 3.11 以降の fromisoformat は ``Z`` を解釈できるが、
    将来 API 側の形式が変わっても収集全体が止まらないよう、
    失敗したら None を返して収集は続ける。
    """
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("日時として解釈できませんでした: %r", value)
        return None


class JGrantsSource(Source):
    name: ClassVar[str] = "jgrants"
    access_method: ClassVar[AccessMethod] = "official_api"
    access_note: ClassVar[str] = (
        "サイトの robots.txt が Disallow: / のためスクレイピングは行わず、"
        "デジタル庁が公開している公式APIを使用している"
    )

    def __init__(self, keywords: tuple[str, ...] = DEFAULT_KEYWORDS) -> None:
        self.keywords = keywords

    def collect(self, client: HttpClient) -> CollectionResult:
        result = CollectionResult(source=self.name)
        seen: set[str] = set()

        for keyword in self.keywords:
            try:
                payload = self._fetch(client, keyword)
            except FetchError as exc:
                # 1つのキーワードが失敗しても、他のキーワードの結果は活かす。
                # 全部を失うより、取れた分を残して「欠けている」と記録する方がよい。
                result.errors.append(f"keyword={keyword}: {exc}")
                continue

            for record in payload:
                subsidy = self._to_subsidy(record)
                if subsidy is None or subsidy.key in seen:
                    continue
                seen.add(subsidy.key)
                result.collected.append(subsidy)

        logger.info(
            "jGrants: %s件を取得しました（キーワード%s件、失敗%s件）",
            len(result.collected),
            len(self.keywords),
            len(result.errors),
        )
        return result

    def _fetch(self, client: HttpClient, keyword: str) -> list[dict[str, Any]]:
        response = client.get(
            API_ENDPOINT,
            params={
                "keyword": keyword,
                "sort": "created_date",
                "order": "DESC",
                "acceptance": "1",  # 受付中のみ
            },
        )
        if response.status_code != 200:
            raise FetchError(f"HTTP {response.status_code}")

        body = response.json()
        records = body.get("result")
        if not isinstance(records, list):
            raise FetchError(f"result が配列ではありません: {type(records).__name__}")
        return records

    def _to_subsidy(self, record: dict[str, Any]) -> Subsidy | None:
        external_id = record.get("id")
        title = record.get("title")
        if not external_id or not title:
            # ID と件名が無いものは、後で参照も表示もできない。落とす。
            logger.warning("id または title が欠けている応答を無視しました: %r", record)
            return None

        return Subsidy(
            source=self.name,
            external_id=str(external_id),
            code=record.get("name") or None,
            title=str(title),
            institution=record.get("institution_name") or None,
            target_area=record.get("target_area_search") or None,
            target_employees=record.get("target_number_of_employees") or None,
            max_amount=record.get("subsidy_max_limit"),
            accepts_from=_parse_datetime(record.get("acceptance_start_datetime")),
            accepts_until=_parse_datetime(record.get("acceptance_end_datetime")),
            url=DETAIL_URL_TEMPLATE.format(id=external_id),
        )
