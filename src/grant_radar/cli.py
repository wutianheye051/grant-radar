"""コマンドラインの入口。

    grant-radar collect            収集して保存し、差分を表示する
    grant-radar collect --export   あわせて data/ に出力する
    grant-radar list               保存済みの受付中公募を表示する

終了コードは、定期実行から使えるように意味を持たせている。
    0  正常
    1  一部の収集元で失敗した（取得できた分は保存済み）
    2  実行そのものに失敗した
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from .export import to_markdown, write_csv, write_json
from .http import HttpClient
from .sources import REGISTRY
from .store import Diff, Store, filter_open

logger = logging.getLogger("grant_radar")

DEFAULT_DB = Path("grant_radar.sqlite3")
DEFAULT_OUTPUT_DIR = Path("data")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _print_diff(source: str, diff: Diff) -> None:
    print(f"[{source}]")
    print(f"  新規       : {len(diff.added)}")
    print(f"  更新       : {len(diff.updated)}")
    print(f"  掲載終了   : {len(diff.disappeared)}")
    print(f"  変化なし   : {diff.unchanged}")
    for subsidy in diff.added[:10]:
        print(f"    + {subsidy.title}")
    if len(diff.added) > 10:
        print(f"    … ほか {len(diff.added) - 10} 件")


def cmd_collect(args: argparse.Namespace) -> int:
    store = Store(args.db)
    exit_code = 0
    merged = Diff()

    with HttpClient(min_interval=args.min_interval) as client:
        for name, source_cls in REGISTRY.items():
            source = source_cls()
            print(f"収集中: {name}（{source.access_method} / {source.access_note}）")
            result = source.collect(client)

            for message in result.errors:
                logger.error("%s: %s", name, message)
            for url, reason in result.skipped:
                logger.info("%s: 取得を見送り %s（%s）", name, url, reason)

            if not result.succeeded:
                exit_code = 1

            diff = store.apply(result)
            _print_diff(name, diff)

            merged.added += diff.added
            merged.updated += diff.updated
            merged.disappeared += diff.disappeared
            merged.unchanged += diff.unchanged

    print(f"\n保存件数（累計）: {store.count()}")

    if args.export:
        output_dir = Path(args.output_dir)
        open_subsidies = filter_open(store.all_subsidies())
        write_json(output_dir / "subsidies.json", open_subsidies)
        write_csv(output_dir / "subsidies.csv", open_subsidies)
        (output_dir / "README.md").write_text(
            to_markdown(open_subsidies, merged, generated_at=datetime.now(UTC)),
            encoding="utf-8",
        )
        print(f"出力しました: {output_dir}/ （受付中 {len(open_subsidies)} 件）")

    return exit_code


def cmd_list(args: argparse.Namespace) -> int:
    store = Store(args.db)
    subsidies = filter_open(store.all_subsidies(source=args.source))
    if not subsidies:
        print("受付中の公募はありません。先に `grant-radar collect` を実行してください。")
        return 0

    for subsidy in subsidies:
        until = subsidy.accepts_until.strftime("%Y-%m-%d") if subsidy.accepts_until else "期限未定"
        amount = f"{subsidy.max_amount:,}円" if subsidy.max_amount else "上限未設定"
        print(f"[〜{until}] {subsidy.title}")
        print(f"          {amount} / {subsidy.target_area or '地域指定なし'} / {subsidy.url}")
    print(f"\n{len(subsidies)} 件")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grant-radar",
        description="補助金・公募情報を定期的に収集し、差分を検知する",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="詳細ログを出す")
    parser.add_argument("--db", default=DEFAULT_DB, type=Path, help="SQLite ファイルのパス")

    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="収集して保存する")
    collect.add_argument("--export", action="store_true", help="data/ に JSON・CSV・要約を出力する")
    collect.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, type=Path)
    collect.add_argument(
        "--min-interval",
        type=float,
        default=1.0,
        help="同一ホストへのリクエストの最小間隔（秒）",
    )
    collect.set_defaults(func=cmd_collect)

    listing = sub.add_parser("list", help="保存済みの受付中公募を表示する")
    listing.add_argument("--source", default=None, help="収集元で絞り込む")
    listing.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("中断しました", file=sys.stderr)
        return 2
    except Exception:
        logger.exception("実行に失敗しました")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
