"""存量修复：作废「凭空首诊」轮次（T20260714-chained-first-diagnosis-guard 裁定 2）。

缺陷：`start_chained_incremental` 曾无「有诊断史」守卫，形成阶段对**从未诊断**的条目
做修订后凭空链发首轮增量诊断，评审页据此显示「待裁决·补/修」而非「待诊断」。

本脚本处置存量显示态：把缺陷产物轮次按 N07 语义置失效（不删行，留证可审）。
**未来行为**由服务端守卫谓词负责（链式轮次不自证历史，见 `ItemReviewService.
_has_user_initiated_diagnosis`）——本脚本谓词与守卫谓词收敛到同一口径。

判据（收敛到守卫谓词，条目级）：
  该条目**不存在任何用户显式发起的诊断轮次**（trigger ∈ {user_submit, dialogue_reeval}；
  NULL 按 user_submit 计），却仍有在世轮次——那些在世轮次必然全是凭空链式轮。
  命中即失效其**全部**在世轮次（不止最小轮），故被修订两次而留下 round_no=2 凭空轮的
  条目也会被彻底修复。正常采纳链的条目必有 user_submit 轮，永不进入选择集（误伤保护更强）。

幂等：重跑零副作用——被处置条目的轮次全部失效后不再有在世轮次，自然不再命中。
默认干跑，--apply 才写库。
用法：cd backend && uv run python -m app.scripts.repair_phantom_first_diagnosis [--apply] [--project <前缀>]
"""
from __future__ import annotations

import argparse
import re

from sqlalchemy import String, func, select
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401  register tables
from app.config import settings
from app.db.base import make_engine, make_session_factory
from app.db.models import ItemDiagnosisRound, Project, RequirementItem
from app.domain.enums import DiagnosisTrigger
from app.log import log_event
from app.repositories.sqlalchemy import SqlItemReviewRepository

_COMPONENT = "maintenance"
INVALIDATED_REASON = "形成阶段误产首诊，按守卫修复随动作废"
_PROJECT_PREFIX_RE = re.compile(r"^[0-9a-fA-F-]+$")


def _user_initiated_item_refs():
    """存在用户显式发起轮次的条目子查询（白名单口径，与守卫谓词同）。"""
    return (
        select(ItemDiagnosisRound.item_ref)
        .where(
            func.coalesce(
                ItemDiagnosisRound.trigger, DiagnosisTrigger.USER_SUBMIT.value
            ).in_(
                [DiagnosisTrigger.USER_SUBMIT.value, DiagnosisTrigger.DIALOGUE_REEVAL.value]
            )
        )
        .distinct()
    )


def find_phantom_items(session: Session, project_prefix: str | None = None) -> list[tuple]:
    """返回 (item_ref, req_no, project_name, live_round_count) 四元组。

    凭空条目＝不存在任何用户显式发起轮次、却仍有在世轮次者（收敛到守卫谓词）。
    """
    stmt = (
        select(
            ItemDiagnosisRound.item_ref,
            RequirementItem.req_no,
            Project.name,
            func.count(ItemDiagnosisRound.id).label("live_count"),
        )
        .join(RequirementItem, RequirementItem.id == ItemDiagnosisRound.item_ref)
        .join(Project, Project.id == RequirementItem.project_id)
        .where(
            ItemDiagnosisRound.invalidated.is_(False),
            ItemDiagnosisRound.item_ref.not_in(_user_initiated_item_refs()),
        )
        .group_by(ItemDiagnosisRound.item_ref, RequirementItem.req_no, Project.name)
        .order_by(Project.name, RequirementItem.req_no)
    )
    if project_prefix:
        stmt = stmt.where(func.cast(Project.id, String).like(f"{project_prefix}%"))
    return list(session.execute(stmt).all())


def invalidate_phantom_rounds(session: Session, project_prefix: str | None = None) -> list[tuple]:
    """作废全部凭空条目的在世轮次（经仓储单点 invalidate_rounds_of_item），返回命中条目清单。

    可测的写路径提取（find → invalidate → find==[] 回路）；不 commit，交由调用方决定事务边界。
    """
    items = find_phantom_items(session, project_prefix)
    reviews = SqlItemReviewRepository(session)
    for item_ref, _req_no, _name, _live in items:
        reviews.invalidate_rounds_of_item(str(item_ref), INVALIDATED_REASON)
        log_event(_COMPONENT, "maintenance.phantom_round.invalidated",
                  item_ref=str(item_ref), ok=True)
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="作废凭空首诊轮次（缺陷产物）")
    parser.add_argument("--apply", action="store_true", help="真正写库（缺省=干跑只报告）")
    parser.add_argument("--project", default=None, help="项目 id 前缀过滤（缺省=全库；仅限 UUID 前缀字符）")
    args = parser.parse_args()

    if args.project is not None and not _PROJECT_PREFIX_RE.match(args.project):
        parser.error(
            "--project 只接受 UUID 前缀字符（十六进制与连字符）；"
            "LIKE 元字符 %/_ 会静默放宽过滤面，已拒绝。"
        )

    engine = make_engine(str(settings.database_url))
    session = make_session_factory(engine)()
    try:
        items = find_phantom_items(session, args.project)
        if not items:
            print("未发现待处置的凭空首诊条目（幂等：已处置过或本就干净）。")
            return

        print(f"{'[干跑] ' if not args.apply else ''}命中 {len(items)} 个凭空条目：")
        for item_ref, req_no, project_name, live_count in items:
            print(f"  {project_name:<22} {req_no:<9} 在世凭空轮={live_count} item={item_ref}")

        if args.apply:
            invalidate_phantom_rounds(session, args.project)
            session.commit()
            print(f"已作废 {len(items)} 个条目的全部在世凭空轮；相关条目回到「待诊断」，"
                  f"首诊改由用户显式发起。")
        else:
            print("干跑未写库；确认无误后加 --apply 执行。")
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    main()
