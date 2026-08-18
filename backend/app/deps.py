"""FastAPI 依赖装配（A2 异步）。

请求侧用 QueuedModelOrchestration：submit 只登记 AgentRun 并入队，判定跑在
worker（REDIS_URL 有）或 inline（无）。测试经 dependency_overrides 换 in-memory。
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings
    from app.services.overview import OverviewService
    from app.services.publication import DocumentOrchestrationService, ExportExecutionService
    from app.services.template_registry import TemplateDraftService, TemplateRegistryService

from sqlalchemy.orm import Session

from app.adapters.event_bus import build_agent_run_event_bus
from app.config import settings
from app.db.base import make_engine, make_session_factory
from app.repositories.agent_run import SqlAgentRunRepository
from app.repositories.in_memory import InMemoryAudit, InMemoryTraceGraph
from app.repositories.sqlalchemy import (
    SqlChartProcessRepository,
    SqlChartRepository,
    SqlIssueRepository,
    SqlItemFormationProcessRepository,
    SqlItemReviewRepository,
    SqlModelResultRepository,
    SqlProcessRecordRepository,
    SqlProjectRepository,
    SqlProjectScope,
    SqlRequirementItemRepository,
    SqlSourceAssetRepository,
    SqlTraceLinkRepository,
)
from app.services.analysis_transformation import AnalysisTransformationService
from app.services.chart_collaboration import ChartCollaborationService
from app.services.item_formation import ItemFormationService, RequirementItemService
from app.adapters.llm import (
    build_element_command_interpreter,
    build_formation_command_interpreter,
    build_item_command_interpreter,
    build_item_draft_composer,
    build_item_source_candidate_composer,
    build_item_explainer,
    build_item_reeval_responder,
    build_section_manuscript_drafter,
)
from app.services.item_review import ItemReviewService
from app.services.material_receiving import MaterialReceivingService
from app.services.model_orchestration import QueuedModelOrchestration
from app.services.project_context import ProjectContextService
from app.workers.queue import make_enqueue

_engine = make_engine(settings.database_url)
_SessionFactory = make_session_factory(_engine)
_enqueue = make_enqueue()  # 接入送检：RQ（REDIS_URL 有）或 inline
_enqueue_recognition = make_enqueue("run_element_recognition")  # 识别送检
_enqueue_review = make_enqueue("run_element_review")  # P03 复核送检
_enqueue_execution = make_enqueue("run_element_execution")  # P04 AI 执行送检
_enqueue_item_formation = make_enqueue("run_item_formation")  # SCN-002 条目格式化送检
# SCN-003 条目诊断送检（issue #10 卡B1 拆逐条目子 job）＋issue #7 受理即回：
# inline 模式经守护线程（每次单发，无池）后台执行，AEP-034 裁决链的链式重诊/体检不再阻塞 HTTP 请求。
_enqueue_item_diagnosis = make_enqueue("run_item_diagnosis", background_inline=True)
_enqueue_item_recheck = make_enqueue("run_item_structure_recheck", background_inline=True)  # AEP-114 结构复核送检（链式体检同随裁决受理即回）
_enqueue_docx_export = make_enqueue("run_docx_export")  # SCN-005 docx 转换执行
_enqueue_chart_suggestion = make_enqueue("run_chart_suggestion")  # SCN-004 图表源码建议送检
_enqueue_chart_verification = make_enqueue("run_chart_verification")  # SCN-004 图文核对送检
# 进度事件总线：Redis Streams（REDIS_URL 有）或 Null（无 → SSE 退回 DB 轮询降级）。
agent_run_event_bus = build_agent_run_event_bus(settings)


def _build_async_service(session: Session) -> MaterialReceivingService:
    orchestration = QueuedModelOrchestration(session, SqlAgentRunRepository(session), _enqueue)
    return MaterialReceivingService(
        project_scope=SqlProjectScope(session),
        model_orchestration=orchestration,
        model_results=SqlModelResultRepository(session),
        process_records=SqlProcessRecordRepository(session),
        source_assets=SqlSourceAssetRepository(session),
        trace_graph=InMemoryTraceGraph(),
        audit=InMemoryAudit(),
    )


def _llm_settings(session: Session) -> "Settings":
    """本次请求生效的模型服务配置（库内启用中 provider 覆盖 env 默认）。

    模块级 `settings` 是进程启动时冻结的 env 快照，直接喂给 LLM 客户端工厂，会让用户在设置页
    改的地址/模型对这条链路**永不生效**（要重启进程才生效）——本仓曾因此让对话类 lane 与异步
    任务 lane 生效口径不一致。凡构建 LLM 客户端，一律先过这里。守护测试见
    backend/tests/test_llm_settings_guard.py。读取失败回落 env：配置面故障不该让功能整个不可用。
    """
    from app.services.config_registry import resolve_llm_settings_or_env

    # 读取失败回落 env 并记 WARN 的逻辑与异步任务 lane 共用一份实现（config_registry 里），
    # 不再两处逐字复制、也不再像从前这样在 deps.py 静默回落无留痕。
    return resolve_llm_settings_or_env(session, settings)


def _build_async_analysis_service(session: Session) -> AnalysisTransformationService:
    orchestration = QueuedModelOrchestration(
        session, SqlAgentRunRepository(session), _enqueue_recognition,
        enqueue_review=_enqueue_review, enqueue_execution=_enqueue_execution,
    )
    return AnalysisTransformationService(
        model_orchestration=orchestration,
        model_results=SqlModelResultRepository(session),
        process_records=SqlProcessRecordRepository(session),
        source_assets=SqlSourceAssetRepository(session),
        command_interpreter=build_element_command_interpreter(_llm_settings(session)),
    )


def get_service() -> Iterator[MaterialReceivingService]:
    session = _SessionFactory()
    try:
        yield _build_async_service(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_analysis_service() -> Iterator[AnalysisTransformationService]:
    session = _SessionFactory()
    try:
        yield _build_async_analysis_service(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _build_async_item_formation_service(session: Session) -> ItemFormationService:
    orchestration = QueuedModelOrchestration(
        session, SqlAgentRunRepository(session), _enqueue_item_formation,
        enqueue_item_formation=_enqueue_item_formation,
        enqueue_item_structure_recheck=_enqueue_item_recheck,
    )
    # AEP-097 对话派发的修订/拆分/归并写权威：与 AEP-036 直发同一装配。
    # 阶段策略解耦 P1：对象层不再绑 on_revised 链式回环——形成阶段的修订只写事实、发布
    # ItemRevised 事件，不自动发起链式复诊（链式复诊迁回评审裁决采纳动作）。
    item_service = RequirementItemService(
        items=SqlRequirementItemRepository(session),
        formation_process=SqlItemFormationProcessRepository(session),
        process_records=SqlProcessRecordRepository(session),
        source_assets=SqlSourceAssetRepository(session),
        reviews=SqlItemReviewRepository(session),
        model_results=SqlModelResultRepository(session),
    )
    from app.services.config_registry import resolve_active_convention

    llm = _llm_settings(session)
    service = ItemFormationService(
        model_orchestration=orchestration,
        model_results=SqlModelResultRepository(session),
        process_records=SqlProcessRecordRepository(session),
        formation_process=SqlItemFormationProcessRepository(session),
        items=SqlRequirementItemRepository(session),
        source_assets=SqlSourceAssetRepository(session),
        command_interpreter=build_formation_command_interpreter(llm),
        draft_composer=build_item_draft_composer(llm),
        explainer=build_item_explainer(llm),
        item_service=item_service,
        active_convention_resolver=lambda: resolve_active_convention(session),
        session=session,  # 缺陷 4：链式派发事务解耦＋单条失败持久通知
    )
    # 走查第三轮裁定：内容修订自动结构体检（对话修订与建议卡采纳同一挂点）
    item_service.on_content_changed_recheck = service.dispatch_chained_recheck
    return service


def get_item_formation_service() -> Iterator[ItemFormationService]:
    session = _SessionFactory()
    try:
        yield _build_async_item_formation_service(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_requirement_item_service() -> Iterator[RequirementItemService]:
    session = _SessionFactory()
    try:
        formation_service = _build_async_item_formation_service(session)
        # 阶段策略解耦 P1：AEP-036 直发对象层不绑 on_revised——直发修订不自动链式复诊，
        # 只写事实、发布 ItemRevised 事件；链式复诊迁回评审裁决采纳动作（_adopt_revise）。
        yield RequirementItemService(
            items=SqlRequirementItemRepository(session),
            formation_process=SqlItemFormationProcessRepository(session),
            process_records=SqlProcessRecordRepository(session),
            source_assets=SqlSourceAssetRepository(session),
            reviews=SqlItemReviewRepository(session),
            model_results=SqlModelResultRepository(session),
            # 走查第三轮裁定：AEP-036 直发（表单/建议卡）同样自动结构体检
            on_content_changed_recheck=formation_service.dispatch_chained_recheck,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _build_async_item_review_service(session: Session) -> ItemReviewService:
    orchestration = QueuedModelOrchestration(
        session, SqlAgentRunRepository(session), _enqueue_item_diagnosis,
        enqueue_item_diagnosis=_enqueue_item_diagnosis,
        # 评审侧采纳修订链式结构体检也经本编排入队（缺省回落会错投诊断队列）
        enqueue_item_structure_recheck=_enqueue_item_recheck,
    )
    llm = _llm_settings(session)
    service = ItemReviewService(
        model_orchestration=orchestration,
        model_results=SqlModelResultRepository(session),
        process_records=SqlProcessRecordRepository(session),
        formation_process=SqlItemFormationProcessRepository(session),
        items=SqlRequirementItemRepository(session),
        source_assets=SqlSourceAssetRepository(session),
        reviews=SqlItemReviewRepository(session),
        draft_composer=build_item_draft_composer(llm),
        explainer=build_item_explainer(llm),
        reeval_responder=build_item_reeval_responder(llm),
        source_candidate_composer=build_item_source_candidate_composer(llm),
        command_interpreter=build_item_command_interpreter(llm),
    )
    # 阶段策略解耦 P1：采纳修订承接方（revision_applier）。对象层不再绑 on_revised——
    # 链式增量诊断由评审服务在裁决采纳动作（_adopt_revise）内显式续接，不经对象层钩子。
    from app.services.item_formation import dispatch_chained_structure_recheck

    review_model_results = SqlModelResultRepository(session)
    review_items = SqlRequirementItemRepository(session)
    item_service = RequirementItemService(
        items=review_items,
        formation_process=SqlItemFormationProcessRepository(session),
        process_records=SqlProcessRecordRepository(session),
        source_assets=SqlSourceAssetRepository(session),
        reviews=SqlItemReviewRepository(session),
        model_results=review_model_results,
        # 走查第三轮裁定：评审采纳修订同样自动结构体检（模块级派发闭包，避免与
        # _build_async_item_formation_service 相互构造成环）
        on_content_changed_recheck=lambda refs: dispatch_chained_structure_recheck(
            review_model_results, orchestration, review_items, refs, session=session,
        ),
    )
    service.revision_applier = item_service.apply_item_revision
    return service


def get_item_review_service() -> Iterator[ItemReviewService]:
    session = _SessionFactory()
    try:
        yield _build_async_item_review_service(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _build_async_chart_service(session: Session) -> ChartCollaborationService:
    orchestration = QueuedModelOrchestration(
        session, SqlAgentRunRepository(session), _enqueue_chart_suggestion,
        enqueue_chart_suggestion=_enqueue_chart_suggestion,
        enqueue_chart_verification=_enqueue_chart_verification,
    )
    return ChartCollaborationService(
        model_orchestration=orchestration,
        model_results=SqlModelResultRepository(session),
        charts=SqlChartRepository(session),
        trace_links=SqlTraceLinkRepository(session),
        issues=SqlIssueRepository(session),
        chart_process=SqlChartProcessRepository(session),
        items=SqlRequirementItemRepository(session),
        source_assets=SqlSourceAssetRepository(session),
    )


def get_chart_collaboration_service() -> Iterator[ChartCollaborationService]:
    session = _SessionFactory()
    try:
        yield _build_async_chart_service(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_publication_service() -> Iterator["DocumentOrchestrationService"]:
    from app.repositories.publication import SqlPublicationRepository
    from app.services.publication import DocumentOrchestrationService

    session = _SessionFactory()
    try:
        yield DocumentOrchestrationService(
            SqlPublicationRepository(session),
            drafter=build_section_manuscript_drafter(_llm_settings(session)),
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_export_execution_service() -> Iterator["ExportExecutionService"]:
    from app.repositories.publication import SqlPublicationRepository
    from app.services.publication import ExportExecutionService

    session = _SessionFactory()
    try:
        yield ExportExecutionService(
            SqlPublicationRepository(session),
            agent_runs=SqlAgentRunRepository(session),
            enqueue=_enqueue_docx_export,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_template_registry_service() -> Iterator["TemplateRegistryService"]:
    from app.repositories.templates import SqlTemplateRegistryRepository
    from app.services.template_registry import TemplateRegistryService

    session = _SessionFactory()
    try:
        yield TemplateRegistryService(SqlTemplateRegistryRepository(session))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_template_draft_service() -> Iterator["TemplateDraftService"]:
    from app.repositories.templates import SqlTemplateDraftRepository
    from app.services.template_registry import TemplateDraftService

    session = _SessionFactory()
    try:
        yield TemplateDraftService(SqlTemplateDraftRepository(session))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_overview_service() -> Iterator["OverviewService"]:
    from app.repositories.overview_read import OverviewReadRepository
    from app.repositories.sqlalchemy import SqlIssueRepository, SqlTraceLinkRepository
    from app.repositories.trace_read import TraceReadRepository
    from app.services.overview import OverviewService
    from app.services.trace_analysis import TraceAnalysisService

    session = _SessionFactory()
    try:
        trace = TraceAnalysisService(
            TraceReadRepository(session),
            trace_links=SqlTraceLinkRepository(session),
            issues=SqlIssueRepository(session),
        )
        yield OverviewService(OverviewReadRepository(session), trace_service=trace)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_ai_effectiveness_service() -> Iterator["AiEffectivenessService"]:
    from app.services.ai_effectiveness import AiEffectivenessService

    session = _SessionFactory()
    try:
        yield AiEffectivenessService(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_asset_catalog_service() -> Iterator["AssetCatalogService"]:
    from app.repositories.asset_read import AssetReadRepository
    from app.services.asset_catalog import AssetCatalogService

    session = _SessionFactory()
    try:
        yield AssetCatalogService(AssetReadRepository(session))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_search_service() -> Iterator["SearchService"]:
    """全局检索服务（04 §4）：注入按配置的 embedder（无端点→StubEmbedder，语义 lane 静默降级）。"""
    from app.adapters.embeddings import build_embedder
    from app.services.search import SearchService

    session = _SessionFactory()
    try:
        yield SearchService(session, build_embedder(settings))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_trace_analysis_service() -> Iterator["TraceAnalysisService"]:
    from app.repositories.trace_read import TraceReadRepository
    from app.services.trace_analysis import TraceAnalysisService

    session = _SessionFactory()
    try:
        yield TraceAnalysisService(
            TraceReadRepository(session),
            trace_links=SqlTraceLinkRepository(session),
            issues=SqlIssueRepository(session),
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_project_service() -> Iterator[ProjectContextService]:
    session = _SessionFactory()
    try:
        yield ProjectContextService(SqlProjectRepository(session))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_project_delete_service() -> Iterator["ProjectDeleteService"]:
    # AEP-113：删除单事务由服务内部 commit（提交后才尽力删落盘文件）；此处只兜底回滚/关闭。
    from app.services.project_delete import ProjectDeleteService

    session = _SessionFactory()
    try:
        yield ProjectDeleteService(session)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_agent_run_repo() -> Iterator[SqlAgentRunRepository]:
    session = _SessionFactory()
    try:
        yield SqlAgentRunRepository(session)
    finally:
        session.close()


def get_notification_repo() -> Iterator["SqlNotificationRepository"]:
    from app.repositories.notification import SqlNotificationRepository

    session = _SessionFactory()
    try:
        yield SqlNotificationRepository(session)
    finally:
        session.close()


def new_session() -> Session:
    """独立 session（SSE 轮询循环用，自行 close）。"""
    return _SessionFactory()


def get_config_registry_service() -> Iterator["ConfigRegistryService"]:
    from app.services.config_registry import ConfigRegistryService

    session = _SessionFactory()
    try:
        yield ConfigRegistryService(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_read_session() -> Iterator[Session]:
    """轻量只读会话：给不需要服务编排的只读投影路由用（首用者＝材料列表）。"""
    session = _SessionFactory()
    try:
        yield session
    finally:
        session.close()
