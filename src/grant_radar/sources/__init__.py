"""収集元。追加するときは Source を実装して registry に登録する。"""

from .base import Source
from .jgrants import JGrantsSource

REGISTRY: dict[str, type[Source]] = {
    JGrantsSource.name: JGrantsSource,
}

__all__ = ["REGISTRY", "JGrantsSource", "Source"]
