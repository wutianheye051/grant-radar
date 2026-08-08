"""収集元の共通インターフェース。

収集元ごとに「どうやって取るか」は違うが、
「取ってよいかを確認済みであること」と「結果を CollectionResult で返すこと」は共通にする。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Literal

from ..http import HttpClient
from ..models import CollectionResult

AccessMethod = Literal["official_api", "html"]


class Source(ABC):
    """収集元の基底クラス。"""

    name: ClassVar[str]
    """識別子。Subsidy.source に入る。"""

    access_method: ClassVar[AccessMethod]
    """取得手段。

    ``official_api``
        提供元が機械的な利用のために公開しているAPI。robots.txt はクローラ向けの
        取り決めなので、招かれているAPIに適用するのは筋が違う。ただし
        「なぜ robots.txt を見ないのか」は access_note に必ず書く。
    ``html``
        HTML を解析して取得する。**必ず RobotsAwareFetcher を経由すること。**
    """

    access_note: ClassVar[str]
    """この手段を選んだ理由。README と実行ログの両方に出す。"""

    @abstractmethod
    def collect(self, client: HttpClient) -> CollectionResult:
        """収集して結果を返す。例外は投げず CollectionResult.errors に積む。

        1つの収集元が落ちても他の収集元は続行させたいため、
        ここで throw せずに結果として返す設計にしている。
        """
