"""LDM-009 结论裁决模型（SCN-003 v5）：轮次表增结论/裁决字段组，修订记录增所选点出处。

绑定 docs/40 domains/DS-001/data.md LDM-009（2026-07-05 拍板）：
结论=判断仅轮次铸造（verdict_kind/verdict_summary/revision_points/supplement_gaps）；
裁决对象=结论（adjudication_* 字段组）；发现项表人工复核列冻结为历史（不删列、不再新写）。

Revision ID: o7f8a9b0c1d2
Revises: n6f7a8b9c0d1
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'o7f8a9b0c1d2'
down_revision: Union[str, Sequence[str], None] = 'n6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ldm009_diagnosis_round") as batch:
        batch.add_column(sa.Column("trigger", sa.String(24), nullable=False, server_default="user_submit"))
        batch.add_column(sa.Column("verdict_kind", sa.String(16), nullable=True))
        batch.add_column(sa.Column("verdict_summary", sa.Text(), nullable=True))
        batch.add_column(sa.Column("revision_points", sa.Text(), nullable=True))
        batch.add_column(sa.Column("supplement_gaps", sa.Text(), nullable=True))
        batch.add_column(sa.Column("superseded_by", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("excluded_point_refs", sa.Text(), nullable=True))
        batch.add_column(sa.Column("adjudication_decision", sa.String(16), nullable=True))
        batch.add_column(sa.Column("adjudication_selected_points", sa.Text(), nullable=True))
        batch.add_column(sa.Column("adjudication_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("adjudication_operator", sa.String(64), nullable=True))
        batch.add_column(sa.Column("adjudicated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("adjudication_idempotency_key", sa.String(128), nullable=True))
        batch.add_column(sa.Column("overridden", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_unique_constraint(
            "uq_ldm009_round_adjudication_idem", ["adjudication_idempotency_key"]
        )
    with op.batch_alter_table("ldm007_item_revision") as batch:
        batch.add_column(sa.Column("selected_point_refs", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ldm007_item_revision") as batch:
        batch.drop_column("selected_point_refs")
    with op.batch_alter_table("ldm009_diagnosis_round") as batch:
        batch.drop_constraint("uq_ldm009_round_adjudication_idem", type_="unique")
        for col in (
            "overridden", "adjudication_idempotency_key", "adjudicated_at",
            "adjudication_operator", "adjudication_reason", "adjudication_selected_points",
            "adjudication_decision", "excluded_point_refs", "superseded_by",
            "supplement_gaps", "revision_points", "verdict_summary", "verdict_kind", "trigger",
        ):
            batch.drop_column(col)
