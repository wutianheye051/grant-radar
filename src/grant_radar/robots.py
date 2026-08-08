"""robots.txt を確認してから取得する仕組み。

このプロジェクトの中核。**取得してよいかを確認せずに取りに行かない。**

標準ライブラリの ``urllib.robotparser`` は robots.txt の取得まで自分で行うが、
それだと User-Agent もタイムアウトもリトライも効かない。
ここでは取得を ``HttpClient`` に任せ、解析だけ ``RobotFileParser`` に渡している。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from . import USER_AGENT
from .http import FetchError, HttpClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RobotsDecision:
    """取得の可否と、その判断理由。

    理由を必ず持たせているのは、後から「なぜこのURLを取らなかったのか」を
    説明できるようにするため。落ちたのか、禁止されていたのかが区別できないと
    収集漏れの原因を追えない。
    """

    allowed: bool
    reason: str
    crawl_delay: float | None = None


class RobotsGate:
    """ホスト単位で robots.txt を取得・解釈し、結果をキャッシュする。"""

    def __init__(self, client: HttpClient, *, user_agent: str = USER_AGENT) -> None:
        self._client = client
        self._user_agent = user_agent
        self._cache: dict[str, RobotsDecision | RobotFileParser] = {}

    def _robots_url(self, url: str) -> tuple[str, str]:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        return origin, urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))

    def _load(self, origin: str, robots_url: str) -> RobotsDecision | RobotFileParser:
        try:
            response = self._client.get(robots_url)
        except FetchError as exc:
            # 取得自体ができなかった。許可されているとは言えないので、取りに行かない。
            # 「分からないなら控える」を既定の態度にする。
            return RobotsDecision(False, f"robots.txt を取得できませんでした（{exc}）")

        if response.status_code == 404:
            # robots.txt が無いサイトは、クロールを制限していないと解釈する（RFC 9309）
            return RobotsDecision(True, "robots.txt が存在しない（制限なしと解釈）")

        if response.status_code in (401, 403):
            # robots.txt すら見せないサイトは、自動アクセスを歓迎していない
            return RobotsDecision(
                False, f"robots.txt へのアクセスが拒否された（HTTP {response.status_code}）"
            )

        if response.status_code >= 400:
            return RobotsDecision(False, f"robots.txt の取得に失敗（HTTP {response.status_code}）")

        content_type = response.headers.get("content-type", "")
        if "text/plain" not in content_type:
            # 404 の代わりに HTML のエラーページを返すサイトが実在する。
            # それを robots.txt として解析すると、意味のない結果を「許可」と誤読する。
            return RobotsDecision(
                False,
                f"robots.txt がテキストで返らなかった（Content-Type: {content_type or '不明'}）",
            )

        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        logger.info("robots.txt を読み込みました: %s", origin)
        return parser

    def check(self, url: str) -> RobotsDecision:
        """この URL を取得してよいかを判断する。"""
        origin, robots_url = self._robots_url(url)

        cached = self._cache.get(origin)
        if cached is None:
            cached = self._load(origin, robots_url)
            self._cache[origin] = cached

        if isinstance(cached, RobotsDecision):
            return cached

        if not cached.can_fetch(self._user_agent, url):
            return RobotsDecision(False, "robots.txt で許可されていないパスです")

        delay = cached.crawl_delay(self._user_agent)
        return RobotsDecision(
            True,
            "robots.txt で許可されています",
            crawl_delay=float(delay) if delay is not None else None,
        )


@dataclass
class RobotsAwareFetcher:
    """robots.txt を確認してから GET する。確認を飛ばす経路をあえて用意しない。"""

    client: HttpClient
    gate: RobotsGate = field(init=False)
    skipped: list[tuple[str, str]] = field(default_factory=list, init=False)
    """取得しなかった URL とその理由。実行後にまとめて報告するために持つ。"""

    def __post_init__(self) -> None:
        self.gate = RobotsGate(self.client)

    def get(self, url: str) -> str | None:
        """取得してよければ本文を返す。禁止されていれば None を返して理由を記録する。"""
        decision = self.gate.check(url)
        if not decision.allowed:
            logger.info("取得を見送りました: %s（%s）", url, decision.reason)
            self.skipped.append((url, decision.reason))
            return None

        if decision.crawl_delay is not None:
            # 相手が指定した間隔の方が長ければ、そちらに従う
            self.client.min_interval = max(self.client.min_interval, decision.crawl_delay)

        return self.client.get(url).text
