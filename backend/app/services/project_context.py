"""项目上下文服务 —— 业务项目 LDM-001 的创建与查询（AEP-067 / AEP-071）。

它是全治理链的"归属边界根"：材料/要素/条目都挂靠在某个业务项目下。
2026-08-07 项目管理组重构：应答改 V2 信封（映射在 api 层）、创建带操作者与幂等键
（同键重放返回同一项目）、status 死列删除。
"""
from __future__ import annotations

from app.api.schemas import CreateProjectCommand, ProjectDetail
from app.domain.errors import InvalidInput, NotFound
from app.interfaces import ProjectRepository, ProjectRow


def to_detail(row: ProjectRow) -> ProjectDetail:
    from app.domain.domain_profiles import get_domain_profile
    # P6b：派生领域档案中文名（None/未知 key → generic 兜底，设置页只读展示）
    label = get_domain_profile(row.domain_profile_key).label
    return ProjectDetail(
        project_id=row.id, name=row.name, scope=row.scope, background=row.background,
        domain_profile_key=row.domain_profile_key, domain_profile_label=label,
        created_at=row.created_at or "",
    )


class ProjectContextService:
    def __init__(self, projects: ProjectRepository) -> None:
        self._projects = projects

    def create_project(self, command: CreateProjectCommand) -> ProjectDetail:  # AEP-067
        name = command.name.strip()
        if not name:
            raise InvalidInput("项目名不能为空")
        # 幂等重放：同键已建过即返回同一项目，不重复建行。
        existing = self._projects.find_by_idempotency_key(command.idempotency_key)
        if existing is not None:
            return to_detail(existing)
        project_id = self._projects.create(
            name, command.scope, command.background,
            domain_profile_key=command.domain_profile_key or None,
            operator_ref=command.operator_ref,
            idempotency_key=command.idempotency_key,
        )
        return to_detail(self._projects.get(project_id))  # 刚建，必存在

    def get_project(self, project_id: str) -> ProjectDetail:  # AEP-071 readProjectScope
        row = self._projects.get(project_id)
        if row is None:
            raise NotFound("业务项目不存在")
        return to_detail(row)

    def list_projects(self) -> list[ProjectRow]:
        return list(self._projects.list_all())
