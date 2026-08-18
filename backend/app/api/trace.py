"""追溯分析服务路由（TRC-001：AEP-058…AEP-066）。

响应约定同 shared/前端契约适配（2xx 裸 DTO）；项目/焦点/关系不存在 → 404；
参数不合法 → 400；复核默认拒绝/守卫不成立 → 409（RejectedTransition）。
边界（06B §3.12）：读端点均为派生只读投影；AEP-066 只做复核路由与转问题项，
不建立、不删除、不改写追溯关系的成立事实。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.schemas import (
    IssueRead,
    TraceChainRead,
    TraceCoverageRead,
    TraceEntryRead,
    TraceGapListRead,
    TraceIssueCommand,
    TraceLinkRead,
    TraceReviewCommand,
    TraceReviewResult,
    TraceSuspectListRead,
    SupportingBasisCommand,
    SupportingBasisResult,
)
from app.deps import get_trace_analysis_service
from app.services.trace_analysis import TraceAnalysisService

router = APIRouter(tags=["trace"])


@router.get("/projects/{project_id}/trace/entry", response_model=TraceEntryRead)
def read_trace_entry(
    project_id: str,
    svc: TraceAnalysisService = Depends(get_trace_analysis_service),
) -> TraceEntryRead:
    """AEP-058：关系网入口锚点 + 项目级小计数（只回入口与计数，不含明细）。"""
    return svc.read_entry(project_id)


@router.get("/projects/{project_id}/trace/upstream", response_model=TraceChainRead)
def read_trace_upstream(
    project_id: str,
    focus_type: str = Query(...),
    focus_ref: str = Query(...),
    depth: int = Query(2, ge=1, le=3),
    limit: int = Query(8, ge=1, le=80),
    include_invalid: bool = Query(False),
    svc: TraceAnalysisService = Depends(get_trace_analysis_service),
) -> TraceChainRead:
    """AEP-059：焦点反向溯源链（漫游重定心=以新焦点重取）。"""
    return svc.read_chain(
        project_id, focus_type, focus_ref, "upstream",
        depth=depth, limit=limit, include_invalid=include_invalid,
    )


@router.get("/projects/{project_id}/trace/downstream", response_model=TraceChainRead)
def read_trace_downstream(
    project_id: str,
    focus_type: str = Query(...),
    focus_ref: str = Query(...),
    depth: int = Query(2, ge=1, le=3),
    limit: int = Query(8, ge=1, le=80),
    include_invalid: bool = Query(False),
    svc: TraceAnalysisService = Depends(get_trace_analysis_service),
) -> TraceChainRead:
    """AEP-060：焦点正向影响链。"""
    return svc.read_chain(
        project_id, focus_type, focus_ref, "downstream",
        depth=depth, limit=limit, include_invalid=include_invalid,
    )


@router.get("/projects/{project_id}/trace/links/{link_ref}", response_model=TraceLinkRead)
def read_trace_link_detail(
    project_id: str,
    link_ref: str,
    svc: TraceAnalysisService = Depends(get_trace_analysis_service),
) -> TraceLinkRead:
    """AEP-061：单条 LDM-013 关系详情（derived 边由前端就地组装，不设端点）。"""
    return svc.read_link_detail(project_id, link_ref)


@router.get("/projects/{project_id}/trace/coverage", response_model=TraceCoverageRead)
def read_trace_coverage(
    project_id: str,
    svc: TraceAnalysisService = Depends(get_trace_analysis_service),
) -> TraceCoverageRead:
    """AEP-062：覆盖度三方向统计（诊断叠加；总览台可复用）。"""
    return svc.read_coverage(project_id)


@router.get("/projects/{project_id}/trace/gaps", response_model=TraceGapListRead)
def read_trace_gaps(
    project_id: str,
    kind: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    svc: TraceAnalysisService = Depends(get_trace_analysis_service),
) -> TraceGapListRead:
    """AEP-063：覆盖缺口与孤儿清单（诊断叠加）。"""
    return svc.read_gaps(project_id, kind=kind, offset=offset, limit=limit)


@router.get("/projects/{project_id}/trace/suspects", response_model=TraceSuspectListRead)
def read_trace_suspects(
    project_id: str,
    include_invalid: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    svc: TraceAnalysisService = Depends(get_trace_analysis_service),
) -> TraceSuspectListRead:
    """AEP-064：可疑失效链路清单（诊断叠加）。"""
    return svc.read_suspects(
        project_id, include_invalid=include_invalid, offset=offset, limit=limit,
    )


@router.post(
    "/projects/{project_id}/trace/links/{link_ref}/review",
    response_model=TraceReviewResult,
)
def review_trace_link(
    project_id: str,
    link_ref: str,
    command: TraceReviewCommand,
    svc: TraceAnalysisService = Depends(get_trace_analysis_service),
) -> TraceReviewResult:
    """AEP-066：可疑链路复核（结论交追溯图谱模块按迁移表重判；恢复=回预建立）。"""
    return svc.review_suspect_link(project_id, link_ref, command)


@router.post("/projects/{project_id}/trace/issues", response_model=IssueRead)
def create_trace_issue(
    project_id: str,
    command: TraceIssueCommand,
    svc: TraceAnalysisService = Depends(get_trace_analysis_service),
) -> IssueRead:
    """AEP-066：诊断项转问题项（origin_kind=trace_diagnosis；幂等）。"""
    return svc.create_diagnosis_issue(project_id, command)


@router.post(
    "/projects/{project_id}/trace/supporting-basis", response_model=SupportingBasisResult
)
def create_supporting_basis(
    project_id: str,
    command: SupportingBasisCommand,
    svc: TraceAnalysisService = Depends(get_trace_analysis_service),
) -> SupportingBasisResult:
    """P4 06 A.1：人工补全支撑依据边（业务翼确认态要素→需求条目）。条目确认态→有效，
    待确认→预建立（P7 引用依据入口复用；随条目确认转有效）。上游/下游对象类型受校验。"""
    return svc.create_supporting_basis(project_id, command)
