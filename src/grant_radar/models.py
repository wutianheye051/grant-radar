"""収集した公募情報の型。

外部APIのレスポンスは、こちらの都合と関係なく変わる。
pydantic で受けているのは、変わったときに「保存した後」ではなく
「受け取った瞬間」に気づくため。壊れたデータを DB に入れてから
気づくと、どこまで巻き戻せばよいのかが分からなくなる。
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Subsidy(BaseModel):
    """公募1件。収集元が増えても共通で扱えるようにしている。"""

    model_config = ConfigDict(frozen=True)

    source: str = Field(description="収集元の識別子（例: jgrants）")
    external_id: str = Field(description="収集元での一意なID")
    code: str | None = Field(default=None, description="公募番号など")
    title: str
    institution: str | None = None
    target_area: str | None = None
    target_employees: str | None = None
    max_amount: int | None = Field(default=None, description="上限額（円）。不明な場合は None")
    accepts_from: datetime | None = None
    accepts_until: datetime | None = None
    url: str | None = None

    @field_validator("max_amount", mode="before")
    @classmethod
    def _zero_means_unknown(cls, value: object) -> object:
        """0円の補助金は存在しない。API が 0 を返すのは「未設定」の意味。

        0 のまま保存すると「上限0円の公募」として集計され、
        金額での絞り込みが静かに壊れる。
        """
        if value == 0:
            return None
        return value

    @property
    def key(self) -> str:
        """収集元をまたいで衝突しない一意キー。"""
        return f"{self.source}:{self.external_id}"

    def is_open(self, at: datetime | None = None) -> bool:
        now = at or datetime.now(UTC)
        if self.accepts_until is not None and self.accepts_until < now:
            return False
        return not (self.accepts_from is not None and self.accepts_from > now)


class CollectionResult(BaseModel):
    """1回の収集の結果。何が起きたかを後から説明できるようにする。"""

    source: str
    collected: list[Subsidy] = Field(default_factory=list)
    skipped: list[tuple[str, str]] = Field(default_factory=list, description="(URL, 見送った理由)")
    errors: list[str] = Field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not self.errors
