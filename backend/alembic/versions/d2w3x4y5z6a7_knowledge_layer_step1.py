"""知识层第一步：资产与快照两张表（V2 世界的第一支迁移）

Revision ID: d2w3x4y5z6a7
Revises: c1u2v3w4x5y6
Create Date: 2026-08-08 18:00:00.000000

设计正本＝docs/v2/design/数据模型.md；裁定与实施记录＝docs/v2/drafts/知识层落库对齐稿-讨论稿.md。
纯新增：不碰任何现有表。asset＝知识的户口（身份＋状态）；snapshot＝历次内容快照，
写入后永不修改（触发器拒绝 UPDATE；DELETE 保留给项目级联删除）。
时间口径书面裁定（2026-08-08，随独立评审采纳）：created_at/submitted_at 是「系统记下
这件事的时刻」（事务时间），不是业务生效时间；暂不引入有效时间（双时态延后）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "d2w3x4y5z6a7"
down_revision: Union[str, None] = "c1u2v3w4x5y6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "asset",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="待确认"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('需求知识', '领域概念')", name="ck_asset_kind"),
        sa.CheckConstraint(
            "status IN ('待确认', '已确认', '已拒绝', '已废止', '已合并')", name="ck_asset_status"
        ),
    )
    op.create_table(
        "snapshot",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("asset.id"), nullable=False, index=True),
        sa.Column("seq_no", sa.Integer(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("content_hash_alg", sa.String(32), nullable=False),
        sa.Column("author_kind", sa.String(16), nullable=False),
        sa.Column("task_ref", sa.Uuid(), nullable=True),
        sa.Column("audit_ref", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("asset_id", "seq_no", name="uq_snapshot_asset_seq"),
        sa.CheckConstraint("author_kind IN ('智能体', '治理者')", name="ck_snapshot_author"),
    )
    # 不可变闸（正式库层）：快照禁止 UPDATE；DELETE 不拦（项目级联删除的治理例外）。
    op.execute(
        """
        CREATE FUNCTION forbid_snapshot_update() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '快照不可修改：改内容请追加新快照';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_snapshot_immutable
            BEFORE UPDATE ON snapshot
            FOR EACH ROW EXECUTE FUNCTION forbid_snapshot_update();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_snapshot_immutable ON snapshot")
    op.execute("DROP FUNCTION IF EXISTS forbid_snapshot_update()")
    op.drop_table("snapshot")
    op.drop_table("asset")
