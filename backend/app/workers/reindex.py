"""检索索引回填 CLI（全局检索 02 篇 §5.1）。

用法：
  cd backend && uv run python -m app.workers.reindex --all
  cd backend && uv run python -m app.workers.reindex --project <project_id>

供首次全量回填与手动重建；同步直算（不经队列），读进程配置的 embedder（无端点则 stub 词法）。
只打印 scope/计数摘要，不打印任何 q/body 原文（硬规则 8）。
"""
from __future__ import annotations

import argparse

from app.config import settings
from app.db.base import make_engine, make_session_factory
from app.services.search_index import build_search_indexer


def main() -> None:
    parser = argparse.ArgumentParser(description="重算 search_index 派生检索索引")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="回填全部项目")
    group.add_argument("--project", metavar="ID", help="仅重算指定 project_id")
    args = parser.parse_args()

    session = make_session_factory(make_engine(settings.database_url))()
    try:
        indexer = build_search_indexer(session)
        if args.all:
            stats = indexer.reindex_all()
            total = sum(s.projected for s in stats)
            pruned = sum(s.pruned for s in stats)
            print(f"reindex --all：{len(stats)} 个项目，投影 {total} 节点，prune {pruned} 行")
        else:
            s = indexer.reindex_project(args.project)
            print(
                f"reindex --project {args.project}："
                f"投影 {s.projected}，嵌入 {s.embedded}，upsert {s.upserted}，prune {s.pruned}"
            )
    finally:
        session.close()


if __name__ == "__main__":
    main()
