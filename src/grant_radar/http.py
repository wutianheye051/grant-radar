"""HTTP クライアント。

外部サイトを相手にする以上、「失敗する」ことを前提に組む。
ここで扱うのは次の4つ。

- タイムアウト: 応答が返らないまま無限に待たない
- リトライ: 一時的な失敗だけ再試行する。恒久的な失敗は再試行しない
- レート制限: 同一ホストへの連続アクセスに最小間隔を空ける
- User-Agent: 誰がアクセスしているかを明示する
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from . import USER_AGENT

logger = logging.getLogger(__name__)

# 再試行する意味があるステータスコードだけを列挙する。
# 404 や 403 は何度投げても結果が変わらないため再試行しない。
# 意味のない再試行は、相手にとっては単なる連続アクセスでしかない。
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class FetchError(Exception):
    """取得に失敗した。呼び出し側が「取れなかった」と判断できるようにする。"""


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    initial_backoff: float = 1.0
    backoff_factor: float = 2.0
    max_backoff: float = 30.0

    def backoff_seconds(self, attempt: int) -> float:
        """指数バックオフ。ジッタを乗せて再試行の集中を避ける。"""
        base = min(self.initial_backoff * (self.backoff_factor**attempt), self.max_backoff)
        return base * (0.5 + random.random() / 2)


@dataclass
class HttpClient:
    """ホスト単位でレート制限をかける HTTP クライアント。

    min_interval はホストごとに独立して管理する。
    全体で1本にすると、無関係なホストへのアクセスまで待たされて遅くなる。
    """

    timeout: float = 20.0
    min_interval: float = 1.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    _last_request_at: dict[str, float] = field(default_factory=dict, init=False)
    _client: httpx.Client | None = field(default=None, init=False)

    def __enter__(self) -> HttpClient:
        self._client = httpx.Client(
            timeout=self.timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _wait_for_rate_limit(self, url: str) -> None:
        host = urlsplit(url).netloc
        last = self._last_request_at.get(host)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
        self._last_request_at[host] = time.monotonic()

    def get(self, url: str, *, params: dict[str, str] | None = None) -> httpx.Response:
        """GET する。再試行しても駄目なら FetchError を送出する。"""
        if self._client is None:
            raise RuntimeError("HttpClient は with 文の中で使用してください")

        last_error: Exception | None = None

        for attempt in range(self.retry.max_attempts):
            self._wait_for_rate_limit(url)
            try:
                response = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                # 接続断・タイムアウトは一時的な可能性があるので再試行する
                last_error = exc
                logger.warning("通信に失敗 (%s/%s): %s", attempt + 1, self.retry.max_attempts, exc)
            else:
                if response.status_code not in RETRYABLE_STATUS:
                    return response
                last_error = FetchError(f"HTTP {response.status_code}: {url}")
                logger.warning(
                    "再試行対象の応答 (%s/%s): HTTP %s",
                    attempt + 1,
                    self.retry.max_attempts,
                    response.status_code,
                )

            if attempt < self.retry.max_attempts - 1:
                time.sleep(self.retry.backoff_seconds(attempt))

        raise FetchError(f"{self.retry.max_attempts}回試行しても取得できませんでした: {url}") from (
            last_error
        )
