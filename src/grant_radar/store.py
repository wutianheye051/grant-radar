"""収集結果の保存と差分検知。

保存先は SQLite。外部サービスを立てなくても ``git clone`` した人がそのまま動かせる。
スキーマは1テーブルで足りる規模なので ORM は入れていない。

**差分検知でいちばん重要な判断**

「前回あったのに今回無い」は、必ずしも「公募が終了した」ではない。
APIが落ちていた、キーワードの検索結果から外れた、といった理由でも同じ状態になる。
両者を取り違えると、実際には募集中の公募を「終了した」と扱ってしまう。

そのため、
- **収集が成功したときだけ**「消滅」を判定する
- レコードは削除せず ``last_seen_at`` を更新する

という形にしている。消さずに残しておけば、後から「いつまで見えていたか」を追える。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .models import CollectionResult, Subsidy

SCHEMA = """
CREATE TABLE IF NOT EXISTS subsidies (
    key                TEXT PRIMARY KEY,
    source             TEXT NOT NULL,
    external_id        TEXT NOT NULL,
    code               TEXT,
    title              TEXT NOT NULL,
    institution        TEXT,
    target_area        TEXT,
    target_employees   TEXT,
    max_amount         INTEGER,
    accepts_from       TEXT,
    accepts_until      TEXT,
    url                TEXT,
    content_hash       TEXT NOT NULL,
    first_seen_at      TEXT NOT NULL,
    last_seen_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_subsidies_source     ON subsidies(source);
CREATE INDEX IF NOT EXISTS idx_subsidies_last_seen  ON subsidies(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_subsidies_until      ON subsidies(accepts_until);
"""

_HASHED_FIELDS = (
    "title",
    "institution",
    "target_area",
    "target_employees",
    "max_amount",
    "accepts_from",
    "accepts_until",
)


def content_hash(subsidy: Subsidy) -> str:
    """内容が変わったかを判定するためのハッシュ。

    全フィールドを毎回比較してもよいが、フィールドが増えるたびに比較漏れが起きる。
    ハッシュ対象を1か所にまとめておけば、追加を忘れても「更新が検知されない」で済み、
    「一部だけ比較して食い違う」という気づきにくい壊れ方をしない。
    """
    payload = {name: _serialize(getattr(subsidy, name)) for name in _HASHED_FIELDS}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _serialize(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


@dataclass
class Diff:
    """前回との差分。"""

    added: list[Subsidy] = field(default_factory=list)
    updated: list[Subsidy] = field(default_factory=list)
    disappeared: list[str] = field(default_factory=list)
    """今回の収集で見えなくなったキー。収集が成功した場合のみ判定する。"""

    unchanged: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.updated or self.disappeared)


class Store:
    def __init__(self, path: Path | str = "grant_radar.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def apply(self, result: CollectionResult, *, now: datetime | None = None) -> Diff:
        """収集結果を保存し、差分を返す。"""
        timestamp = (now or datetime.now(UTC)).isoformat()
        diff = Diff()

        with closing(self._connect()) as conn, conn:
            existing = {
                row["key"]: row["content_hash"]
                for row in conn.execute(
                    "SELECT key, content_hash FROM subsidies WHERE source = ?",
                    (result.source,),
                )
            }

            for subsidy in result.collected:
                digest = content_hash(subsidy)
                previous = existing.get(subsidy.key)

                if previous is None:
                    diff.added.append(subsidy)
                elif previous != digest:
                    diff.updated.append(subsidy)
                else:
                    diff.unchanged += 1

                self._upsert(conn, subsidy, digest, timestamp)

            if result.succeeded:
                collected_keys = {s.key for s in result.collected}
                diff.disappeared = sorted(existing.keys() - collected_keys)
            # 収集に失敗した収集元では、消滅の判定を行わない。
            # 取得できなかっただけの公募を「終了した」と誤って扱わないため。

        return diff

    def _upsert(
        self, conn: sqlite3.Connection, subsidy: Subsidy, digest: str, timestamp: str
    ) -> None:
        conn.execute(
            """
            INSERT INTO subsidies (
                key, source, external_id, code, title, institution,
                target_area, target_employees, max_amount,
                accepts_from, accepts_until, url,
                content_hash, first_seen_at, last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET
                code             = excluded.code,
                title            = excluded.title,
                institution      = excluded.institution,
                target_area      = excluded.target_area,
                target_employees = excluded.target_employees,
                max_amount       = excluded.max_amount,
                accepts_from     = excluded.accepts_from,
                accepts_until    = excluded.accepts_until,
                url              = excluded.url,
                content_hash     = excluded.content_hash,
                last_seen_at     = excluded.last_seen_at
            """,
            (
                subsidy.key,
                subsidy.source,
                subsidy.external_id,
                subsidy.code,
                subsidy.title,
                subsidy.institution,
                subsidy.target_area,
                subsidy.target_employees,
                subsidy.max_amount,
                subsidy.accepts_from.isoformat() if subsidy.accepts_from else None,
                subsidy.accepts_until.isoformat() if subsidy.accepts_until else None,
                subsidy.url,
                digest,
                timestamp,
                timestamp,
            ),
        )

    def all_subsidies(self, *, source: str | None = None) -> list[Subsidy]:
        query = "SELECT * FROM subsidies"
        params: tuple[str, ...] = ()
        if source is not None:
            query += " WHERE source = ?"
            params = (source,)
        query += " ORDER BY accepts_until IS NULL, accepts_until, title"

        with closing(self._connect()) as conn:
            return [_row_to_subsidy(row) for row in conn.execute(query, params)]

    def count(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM subsidies").fetchone()
            return int(row["n"])


def _row_to_subsidy(row: sqlite3.Row) -> Subsidy:
    return Subsidy(
        source=row["source"],
        external_id=row["external_id"],
        code=row["code"],
        title=row["title"],
        institution=row["institution"],
        target_area=row["target_area"],
        target_employees=row["target_employees"],
        max_amount=row["max_amount"],
        accepts_from=datetime.fromisoformat(row["accepts_from"]) if row["accepts_from"] else None,
        accepts_until=datetime.fromisoformat(row["accepts_until"])
        if row["accepts_until"]
        else None,
        url=row["url"],
    )


def filter_open(subsidies: Iterable[Subsidy], *, at: datetime | None = None) -> list[Subsidy]:
    return [s for s in subsidies if s.is_open(at)]
