"""结构复核幂等键升为索引列（issue #12 卡B K_LIKE 修复）

Revision ID: x6p7q8r9s0t1
Revises: w5o6p7q8r9s0
Create Date: 2026-07-14 00:00:00.000000

设计事实源：任务卡 T20260713-recheck-idem-migration 设计裁定；issue #12 K_LIKE 段。
- ldm015_model_result 增 recheck_idempotency_key（String(128)、nullable、unique+index）；
  取代 find_recheck_by_idempotency 旧 result_content LIKE 片段匹配（`%`/`_` 通配、含反斜杠键
  json 转义后永不自匹配、无域过滤跨项目泄漏三病）。
- 历史回填：stage=item_structure_recheck ∧ judgement=batch_accepted 行解析 result_content 提取
  幂等键回填索引列；解析失败/无键行留 NULL（历史行不再可幂等命中属可接受，如实计数报备）。
- 去重保最新（裁定失败策略）：同键多行仅最新（created_at desc, id desc）保值，其余留 NULL，
  不静默丢行——保证 unique 索引可建。
- 回填键名用与写侧同源常量 RECHECK_IDEMPOTENCY_PAYLOAD_KEY（禁裸字符串双份）。
- 迁移唯一触碰 LDM-015 结构点；downgrade 删索引+列（回填不可逆属预期）。
"""
import json
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.db.models import RECHECK_IDEMPOTENCY_PAYLOAD_KEY


revision: str = 'x6p7q8r9s0t1'
down_revision: Union[str, Sequence[str], None] = 'w5o6p7q8r9s0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_all（槽位库/测试 fixture 走 ORM 建表）与本迁移须产出同名索引，避免 schema 漂移。
_INDEX_NAME = "ix_ldm015_model_result_recheck_idempotency_key"
_logger = logging.getLogger("alembic.runtime.migration")


def _backfill(conn) -> None:
    """回填历史结构复核受理信封的幂等键至新列（去重保最新，计数报备）。"""
    rows = conn.execute(sa.text(
        "SELECT id, result_content FROM ldm015_model_result "
        "WHERE stage = 'item_structure_recheck' AND judgement = 'batch_accepted' "
        "ORDER BY created_at DESC, id DESC"
    )).fetchall()

    filled = parse_failed = no_key = deduped = 0
    seen_keys: set[str] = set()
    for row_id, content in rows:
        if not content:
            parse_failed += 1
            continue
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            parse_failed += 1
            continue
        key = payload.get(RECHECK_IDEMPOTENCY_PAYLOAD_KEY) if isinstance(payload, dict) else None
        if not key:
            no_key += 1  # 链式复核等无幂等键信封：留 NULL，非失败
            continue
        if key in seen_keys:
            deduped += 1  # 同键更早行：留 NULL（最新行已保值），不丢行
            continue
        seen_keys.add(key)
        conn.execute(
            sa.text(
                "UPDATE ldm015_model_result SET recheck_idempotency_key = :k WHERE id = :i"
            ),
            {"k": key, "i": row_id},
        )
        filled += 1

    _logger.info(
        "recheck_idempotency_key 回填：命中信封 %d，回填 %d，无键 %d，去重留空 %d，解析失败 %d",
        len(rows), filled, no_key, deduped, parse_failed,
    )


def upgrade() -> None:
    """加可空列 → 回填（去重保最新）→ 建 unique 索引（去重后无重复非空值可建）。"""
    op.add_column(
        'ldm015_model_result',
        sa.Column('recheck_idempotency_key', sa.String(length=128), nullable=True),
    )
    _backfill(op.get_bind())
    op.create_index(
        _INDEX_NAME, 'ldm015_model_result', ['recheck_idempotency_key'], unique=True,
    )


def downgrade() -> None:
    """删索引+列（回填内容不可逆，历史键仍存于 result_content payload）。"""
    op.drop_index(_INDEX_NAME, table_name='ldm015_model_result')
    op.drop_column('ldm015_model_result', 'recheck_idempotency_key')
