"""robots.txt の判断ロジック。

このプロジェクトで最も壊れてはいけない部分。
「許可されていないのに許可と判断する」バグは、相手に迷惑をかける形で表面化する。
"""

from __future__ import annotations

import httpx
import pytest
import respx

from grant_radar.http import HttpClient, RetryPolicy
from grant_radar.robots import RobotsAwareFetcher, RobotsGate

ROBOTS_URL = "https://example.test/robots.txt"
TARGET_URL = "https://example.test/list/page1"


def _text(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/plain; charset=utf-8"})


@pytest.fixture
def client():
    # 再試行の待ち時間でテストを遅くしないため、リトライは1回だけにする
    with HttpClient(min_interval=0.0, retry=RetryPolicy(max_attempts=1)) as c:
        yield c


@respx.mock
def test_missing_robots_is_allowed(client):
    """robots.txt が無いサイトは制限なしと解釈する（RFC 9309）。"""
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    decision = RobotsGate(client).check(TARGET_URL)
    assert decision.allowed
    assert "存在しない" in decision.reason


@respx.mock
def test_disallow_all_is_denied(client):
    """jGrants と同じ形。トップ以外を禁じている場合は取得しない。"""
    respx.get(ROBOTS_URL).mock(
        return_value=_text("User-agent: *\nDisallow: /\nAllow: /index.html\n")
    )
    decision = RobotsGate(client).check(TARGET_URL)
    assert not decision.allowed


@respx.mock
def test_allowed_path_is_permitted(client):
    """禁止されているのが別のパスなら、対象パスは取得してよい。"""
    respx.get(ROBOTS_URL).mock(return_value=_text("User-agent: *\nDisallow: /private/\n"))
    decision = RobotsGate(client).check(TARGET_URL)
    assert decision.allowed


@pytest.mark.parametrize("status", [401, 403])
@respx.mock
def test_forbidden_robots_is_denied(client, status):
    """robots.txt すら見せないサイトは、自動アクセスを歓迎していないと解釈する。"""
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(status))
    decision = RobotsGate(client).check(TARGET_URL)
    assert not decision.allowed


@respx.mock
def test_html_response_is_denied(client):
    """404 の代わりに HTML を返すサイトがある。

    HTML を robots.txt として解析すると「禁止ルールが1つも無い」＝許可と誤読する。
    実在する挙動なので、Content-Type で弾く。
    """
    respx.get(ROBOTS_URL).mock(
        return_value=httpx.Response(
            200, text="<html>Not Found</html>", headers={"content-type": "text/html"}
        )
    )
    decision = RobotsGate(client).check(TARGET_URL)
    assert not decision.allowed
    assert "テキスト" in decision.reason


@respx.mock
def test_unreachable_robots_is_denied(client):
    """通信できなかった場合、許可されているとは言えないので取りに行かない。"""
    respx.get(ROBOTS_URL).mock(side_effect=httpx.ConnectError("connection refused"))
    decision = RobotsGate(client).check(TARGET_URL)
    assert not decision.allowed


@respx.mock
def test_crawl_delay_is_respected(client):
    """相手が指定した間隔の方が長ければ、そちらに合わせる。"""
    respx.get(ROBOTS_URL).mock(
        return_value=_text("User-agent: *\nDisallow: /private/\nCrawl-delay: 5\n")
    )
    respx.get(TARGET_URL).mock(return_value=httpx.Response(200, text="ok"))

    fetcher = RobotsAwareFetcher(client)
    assert fetcher.get(TARGET_URL) == "ok"
    assert client.min_interval >= 5.0


@respx.mock
def test_robots_is_fetched_once_per_host(client):
    """同じホストに何度アクセスしても robots.txt の取得は1回だけ。"""
    route = respx.get(ROBOTS_URL).mock(return_value=_text("User-agent: *\nDisallow: /private/\n"))
    gate = RobotsGate(client)
    for _ in range(3):
        gate.check(TARGET_URL)
    assert route.call_count == 1


@respx.mock
def test_skipped_urls_are_recorded(client):
    """取得しなかった理由が残ること。

    「落ちた」のか「禁止されていた」のかが区別できないと、収集漏れの原因を追えない。
    """
    respx.get(ROBOTS_URL).mock(return_value=_text("User-agent: *\nDisallow: /\n"))

    fetcher = RobotsAwareFetcher(client)
    assert fetcher.get(TARGET_URL) is None
    assert len(fetcher.skipped) == 1
    url, reason = fetcher.skipped[0]
    assert url == TARGET_URL
    assert reason
