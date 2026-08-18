"""条目评审服务（AEP-032/033/034/095 + 确认/终止承接）—— SCN-003 v5 结论裁决。

设计事实源：docs/40 domains/DS-001（data.md LDM-009 / state-machines/需求条目.md /
interfaces/条目评审服务.md）、docs/40 slices/SCN-003-P01/页面详细设计.md（v5）。
- 结论=判断，仅诊断轮次铸造；聚合一致性由本服务确定性守卫（不信任模型自检）。
- 草案=作品（LDM-015 + AEP-036 候选建议投影），采纳前零副作用；解释=说明（LDM-015）。
- AEP-034 裁决对象=结论：采纳按状态字原子执行副作用链（pass→确认内联；revise→AEP-036
  应用所选点+链式增量诊断；withdraw→终止；supplement→登记缺口）；拒绝→结论作废。
- 对话轮不铸结论；改判必经轻量重评轮次（trigger=dialogue_reeval）。
- 评审位置=派生显示态（无结论/诊断中/待裁决），不持久化为条目状态。
业务结局用返回值；默认拒绝/版本冲突用 RejectedTransition。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from app.adapters.llm import (
    ItemDraftComposer,
    ItemExplainer,
    ItemReevalResponder,
    ItemSourceCandidateComposer,
    StubItemDraftComposer,
    StubItemExplainer,
    StubItemReevalResponder,
    StubItemSourceCandidateComposer,
    serialize_diagnosed_finding,
)
from app.api.schemas import (
    ActionFact,
    DiagnosisRunProgressRead,
    DialogueMessageRead,
    ItemConfirmationCommand,
    ItemConfirmationResult,
    FindingVetoCommand,
    FindingVetoRead,
    ItemReviewDiagnosisCommand,
    ItemReviewDiagnosisRequestResult,
    ItemQualityRead,
    ItemReviewWorkspaceRead,
    ItemRevisionCommand,
    ItemRevisionRecordRead,
    ItemWithdrawCommand,
    ItemWithdrawResult,
    ReviewDialogueCommand,
    ReviewDialogueResult,
    ReviewFindingRead,
    ReviewRequirementItemRead,
    RevisionPointRead,
    SourceAlignmentRead,
    SourceAttestationCommand,
    SourceAttestationRead,
    SourceCandidateRead,
    VerdictAdjudicationCommand,
    VerdictAdjudicationRead,
    VerdictRead,
)
from app.domain.anchors import first_anchor_quote
from app.domain.chat_commands import ITEM_REVIEW_COMMANDS, ChatCommand, UnknownCommand, resolve_command
from app.domain.enums import (
    AiRequestStage,
    DiagnosisMode,
    DiagnosisProcessingStatus as DPS,
    DiagnosisTrigger,
    DialogueOutcomeType,
    ElementProcessStatus,
    ItemRevisionMode,
    knowledge_category_of,
    RequirementItemStatus as IS,
    RequirementQualityRule,
    ReviewDisplayCode as RDC,
    ReviewItemStatus as RIS,
    VerdictDecision,
    VerdictKind,
)
from app.domain.errors import InvalidInput, NotFound, RejectedTransition
from app.domain.labels import NON_REVISION_FIELD_KEYS, SOURCE_ATTESTATION_FIELD_KEY
from app.domain.revision_points import compose, expand_selection, validate_points
from app.domain.state_machine import ItemEvent, ItemState, item_transition
from app.interfaces import (
    DiagnosisRoundRow,
    FindingVetoRow,
    ItemFormationProcessRepository,
    ItemReviewRepository,
    ItemRevisionRow,
    ItemRow,
    ModelOrchestration,
    ModelResultRepository,
    ProcessRecordRepository,
    RequirementItemRepository,
    SourceAssetRepository,
)
from app.log import log_event
from app.services.item_formation import (
    build_material_canvas,
    project_element,
    split_verification_methods,
)
from app.services.run_liveness import dead_run_verdict

_COMPONENT = "item-review"

# 人工确认背书的落库口径（零迁移：借 LDM-007 修订记录表既有的操作者/理由/时间/幂等键四列）。
#
# 用一个不对应任何条目字段的 field_key 来记账，是刻意的：背书**不改条目的任何字段**，
# 尤其不动 source_element_refs——那里只能放材料里真实存在的要素。写进修订记录而不另起炉灶，
# 一是不必加列、不必迁移，二是修订记录本来就是全局投影的一部分，下游读得到这笔账。
_SOURCE_ATTESTATION_FIELD = SOURCE_ATTESTATION_FIELD_KEY  # 单一来源在 domain/labels.py
_SOURCE_ATTESTATION_VALUE = "已人工确认为真实需求（材料未记载）"
_SOURCE_ATTESTATION_INVALIDATE_REASON = "已由人工确认为真实需求（材料未记载），旧结论失效"

# 「改了这么多次还没通过」的提示阈值。**只用来提示，不停任何流程**
# （2026-07-20 用户拍板废除原采纳链空转熔断）。
#
# 原先此处是一道强制闸：同一条目被采纳过 3 次「建议修订」仍未通过，就停发自动链式复诊。
# 废除的理由，按分量排序：
# 1. 评审是「AI 提建议 → 用户给反馈」的往复过程，它的终点只有两个——AI 判通过，或人工撤回
#    该条目。**什么时候不值得再改，是用户的判断，不是机器数出来的。**
# 2. 那道闸原本也没停住任何东西：它只掐自动链，用户照样能手动发起诊断（注释自陈「不挡用户
#    手动发起」）。宣称防的反复空转与预算失控一条没防住，只换来一个莫名掉回「待诊断」的状态。
# 3. 也不存在需要它兜底的失控：start_chained_incremental 全仓唯一调用点是用户的「采纳」动作，
#    每一轮复诊都由一次人的点击换来，机器自己不会往前走一步。
# 4. 真正治「不收敛」的手段是本卡新增的逐条否决——用户直接判定某个问题不成立，它就永久
#    不再阻塞。比机器数到 3 然后掐掉自动链有效得多。
_REPEATED_REVISE_HINT_AT = 3

# 本服务批次 lane 的 rq task 名（HK-2 判死阈值经 job_timeout_for(lane) 取值的键）
_DIAGNOSIS_LANE = "run_item_diagnosis"

_DIAGNOSIS_MODES = {m.value for m in DiagnosisMode}
_FINDING_TYPES = {"source_inconsistency", "ambiguous_expression", "untestable", "missing_field", "no_blocker"}
_VERDICT_KINDS = {k.value for k in VerdictKind}

# 轮次「诊断失败」判定的单一来源（VerdictRead.status 投影的 "failed" 桶 = failed ∪ not_diagnosable）：
# 供 _project_round 状态投影、显示态细分（诊断失败格/连击计数）与 run 级 failed_count 三处共用。
# 注：与 ai_effectiveness._is_delivery_failed 是不同的判失败轴——后者按 LDM-015 judgement 字符串
# `_failed` 后缀判定，不含 not_diagnosable（准入/版本/上下文拒绝）；轮次处理状态轴须自成单点，
# 二者同一来源不可互换（否则 not_diagnosable 轮会在 run 失败计数中漏计，与前端既有归因不一致）。
_FAILED_ROUND_STATUSES = frozenset({DPS.FAILED.value, DPS.NOT_DIAGNOSABLE.value})

_STAGE_DRAFT = "item_revision_draft"
_STAGE_EXPLAIN = "item_review_explanation"

# 起草交换的来源页面。条目形成页与本页共用 _STAGE_DRAFT 这一个阶段键（建议卡机制同源，
# 是刻意决定），载荷若不标来源，本页读投影就分不清哪些交换是在本页发生的——形成页的
# 修订建议交换因此混进了本页对话历史（2026-07-20 用户报障）。存量载荷没有这一项。
_ORIGIN_REVIEW = "review"
_ORIGIN_FORMATION = "formation"


@dataclass(frozen=True)
class ChainedDiagnosisOutcome:
    """修订应用后链式增量诊断的三态结果（漏斗单点：调用方据此回显真话，不再靠 run_ref 反推）。

    status:
      - "submitted"           链式增量诊断已发起，agent_run_ref 为运行引用；
      - "skipped_no_history"  本条尚无用户显式发起的诊断记录，守卫按设计跳过，不凭空产生首轮；
      - "rejected"            链式发起被拒（如条目正在诊断中），note 为服务端给出的真实原因。
    note 为面向用户的可读说明（skipped/rejected 承载原因；submitted 一般为空）。

    2026-07-20 起不再有 "skipped_no_convergence"：采纳链空转熔断已废除，往复次数不再是停发理由
    （理由见 _REPEATED_REVISE_HINT_AT）。存量调用方若仍分支该值，走不到而已，不会误判。

    当前无任何生产代码消费本对象的 status：唯一生产调用点（_adopt_revise 里的
    start_chained_incremental）直接丢弃返回值，item_formation.apply_item_revision 早在阶段策略
    解耦 P1 摘钩后已不接收本对象、回执只按 content_changed 三分；只有测试读 status。若将来要让
    status 影响用户可见回执，需先给它接一个消费者——否则新增 status 加了也不会有任何行为差异。
    """

    status: str
    agent_run_ref: Optional[str] = None
    note: Optional[str] = None

# AI 交付失败分关 → 用户可读标签（诊断可靠性设计裁定 4；stage 枚举见 LDM-015 failure.stage）
_FAILURE_STAGE_LABELS: dict[str, str] = {
    "llm_error": "模型服务调用失败",
    "parse": "模型回复不完整",
    "structure": "回复缺少必要内容",
    "aggregation": "结论与证据不自洽",
    "synthesis": "修订点无法应用到原文",
}

# 诊断模式 → 上下文覆盖说明（SCN-003 §4 诊断模式表；模式只影响上下文范围与检查重点）
_MODE_COVERAGE: dict[str, str] = {
    "quick": "快速覆盖：当前条目、来源锚点与必要字段，重点检查明显阻断项。",
    "standard": "标准覆盖：当前条目、来源要素、形成依据、对应原文片段与字段修订记录。",
    "comprehensive": "全面覆盖：标准范围外加相关确认态条目与历史评审记录。",
    "incremental": "增量覆盖：本次变更字段、受影响来源与旧诊断结论。",
}

# 对话意图路由启发式（修订动词→草案；疑问→解释；其余→轻量重评）
_DRAFT_MARKS = ("修订为", "改成", "改为", "写进", "加上", "补上", "表达修订", "起草")
_QUESTION_MARKS = ("为什么", "依据是", "什么意思", "解释", "出处")

_VERDICT_TEXT = {"pass": "建议通过", "revise": "建议修订", "withdraw": "建议撤回", "supplement": "建议补充来源"}

# AEP-095 斜杠命令解释操作码 → 时间线展示标签
_ITEM_DIALOGUE_OPERATION_LABELS = {
    "start_diagnosis": "发起诊断",
    "adjudicate_adopt": "采纳结论",
    "adjudicate_reject": "拒绝结论",
    "adopt_draft": "采纳草案",
    "manual_revision": "人工修订",
    "draft": "AI 起草修订草案",
    "override_confirm": "覆盖确认",
    "withdraw": "撤回条目",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_NUM_TOKEN_RE = re.compile(r"\d{2,}(?:\.\d+)?")  # 多位数（阈值类）；单位数（如「5 秒」）不计入偏离


def _drift_tokens(expression: str, source_content: str) -> list[str]:
    """条目表达中出现、但该来源内容中没有的多位数阈值（来源偏离信号，与 source_drift 同思路）。

    最小启发式（不引入 analysis_transformation 的 novel-token 全量逻辑）：只看多位数字，
    低噪；命中 = 条目取值与该来源现值不一致（如条目 500 元、来源 800 元）。
    """
    src = set(_NUM_TOKEN_RE.findall(source_content or ""))
    return [t for t in dict.fromkeys(_NUM_TOKEN_RE.findall(expression or "")) if t not in src][:6]


#: quality_meta.findings 里承载质量字段的键；判「有没有内容」只看这几个，
#: finding_type 是回指标签、finding_ref 是配对引用，二者都不算内容。
_QUALITY_FINDING_KEYS = ("rule_code", "evidence_span", "severity", "dimension")


def _build_quality_meta(verdict: dict, finding_refs: Optional[list[str]] = None) -> Optional[str]:
    """从诊断结论对象抽出 v2 质量诊断器旁路元数据 → JSON（无质量字段则 None）。

    每条 findings 元数据带 finding_ref＝同批写入 LDM-009 的那一行，读投影按引用配回。
    此前靠「写入序＝读出序」按下标 zip，但同事务写入的发现项 created_at 全相同，读侧
    (created_at, id) 排序退化为随机 UUID 序，配对随即错位（REQ-101 实证）。
    finding_refs 缺省或长度不足时该条不带引用，读侧自会退回下标配对（存量轮次同此路径）。

    降级不拒收：本函数只做搬运，不做校验（校验已在适配器 _sanitize_verdict 完成），
    异常时返回 None 不影响结论。
    """
    try:
        refs = finding_refs or []
        finding_meta = [
            {
                "finding_type": f.get("finding_type"),
                "finding_ref": refs[i] if i < len(refs) else None,
                "rule_code": f.get("rule_code"),
                "evidence_span": f.get("evidence_span"),
                "severity": f.get("severity"),
                "dimension": f.get("dimension"),
            }
            for i, f in enumerate(verdict.get("findings") or [])
        ]
        meta = {
            "quality_profile": verdict.get("quality_profile"),
            "ears_rewrite": verdict.get("ears_rewrite"),
            "source_alignments": verdict.get("source_alignments"),
            "findings": finding_meta,
        }
        # 全空（既无画像/EARS/对齐分，findings 也无任何质量字段）→ 不落库。
        # 只看质量键：finding_ref 恒有值，若计入则任何一条发现项都会让这里恒真。
        has_any = (
            meta["quality_profile"] or meta["ears_rewrite"] or meta["source_alignments"]
            or any(any(fm.get(k) for k in _QUALITY_FINDING_KEYS) for fm in finding_meta)
        )
        return json.dumps(meta, ensure_ascii=False) if has_any else None
    except (TypeError, ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# 问题否决（AEP-116）：用户裁定「这条不是问题」后，同一个问题不再重提、不再阻塞确认。
#
# 匹配的是**问题指纹**而不是发现项行：发现项每诊断一轮都重写一批新行，而一次否决必须对
# 未来所有轮次生效。指纹＝规则码 + 证据片段，两者都是模型受约束产出的结构化字段
# （证据片段的契约要求它在基准表达中恰好出现一次）。绝不拿模型自由撰写的问题摘要当键——
# 那种键随措辞漂移，会同时制造误命中和漏命中，且事后无法解释为什么命中或没命中。
#
# 全过程是字符串比较，可离线复算；没有任何一步交给模型判断（全站纪律）。
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")

#: 片段包含规则的最短长度。短于此的证据片段基本是虚词或标点级碎片，
#: 「一个片段包含另一个」不足以证明两轮指的是同一处证据，故只认完全相等。
_VETO_CONTAIN_MIN = 4

#: 匹配强度（越大越强）。一条否决在同一轮内至多命中一条发现项，多个候选时按强度取一条，
#: 强度相同则取本轮读出序在前的那条；未被选中的候选照常计入阻断（C1 根治）。
_VETO_MATCH_NONE = 0
_VETO_MATCH_NARROWED = 1  # 新片段落在已否决片段之内（模型少截了几个字）
_VETO_MATCH_EXACT = 2     # 两个片段完全相等


def _veto_norm(text: Optional[str]) -> str:
    """归一化：去首尾空白 + 内部连续空白折叠为一个空格。

    刻意不做大小写与全半角折叠：中文语料里收益极低，却会引入解释不清的等价类
    （用户无法预期为什么两个看着不同的片段被判成同一个问题）。
    """
    return _WS_RE.sub(" ", (text or "").strip())


def _veto_key(
    rule_code: Optional[str], evidence_span: Optional[str], finding_type: Optional[str],
) -> Optional[tuple[str, str]]:
    """问题指纹：(规则键, 归一化证据片段)；无法指纹化时返回 None。

    规则码缺失（存量轮次或质量元数据降级）时退化为发现项类型，加 `type:` 前缀与真规则码隔开。
    证据片段缺失则整条无法指纹化——没有可复算的定位依据，界面因此不给这条否决入口。
    """
    span = _veto_norm(evidence_span)
    if not span:
        return None
    code = _veto_norm(rule_code)
    return (code or f"type:{_veto_norm(finding_type)}", span)


def _veto_match(
    veto_key: Optional[tuple[str, str]], finding_key: Optional[tuple[str, str]],
) -> int:
    """已登记的否决与本轮某条发现项是否指同一个问题，并给出匹配强度。

    参数有方向：veto_key 是已登记否决的指纹，finding_key 是当前这条发现项的指纹。

    判据（2026-07-21 方案门拍板，替代 07-20 的对称包含判据）：
    ① 规则键相等 ∧ 片段完全相等 → _VETO_MATCH_EXACT。
    ② 规则键相等 ∧ 新片段落在已否决片段**之内**（且不短于 _VETO_CONTAIN_MIN）
       → _VETO_MATCH_NARROWED。保留「模型下一轮少截几个字」的容差。
    ③ 新片段**真包含**已否决片段（范围比用户当初否决的更宽）→ 不命中。多出来的那截是
       用户没看过的内容，可能是一个真的新问题，宁可重新提出让用户再裁一次。

    为什么不是「指纹加位置偏移」（07-20 曾拟的取向，07-21 析案否证）：证据片段落库前经
    适配器 _anchor_once 校验「在基准表达中恰好出现一次」，定位失败即丢弃片段、该条不可
    指纹化。所以片段文字与起止偏移一一对应，位置带不来字符串判据之外的区分力——嵌套片段
    的两个区间必然重叠，按重叠判仍会连带命中，按相等判则等于取消跨轮容差。真正区分「同
    一个问题」与「另一个问题」的是本函数的方向不对称，以及 _mark_vetoes 的同轮唯一命中。

    两种错判的代价不对称，判据据此偏向保守：漏命中的代价是用户把同一条问题再标一次；
    误命中的代价是用户没看过的问题被替他判定为不成立，并且放行「直接确认」。
    """
    if veto_key is None or finding_key is None or veto_key[0] != finding_key[0]:
        return _VETO_MATCH_NONE
    vetoed_span, finding_span = veto_key[1], finding_key[1]
    if vetoed_span == finding_span:
        return _VETO_MATCH_EXACT
    if len(finding_span) >= _VETO_CONTAIN_MIN and finding_span in vetoed_span:
        return _VETO_MATCH_NARROWED
    return _VETO_MATCH_NONE


def _veto_hit(veto_key: Optional[tuple[str, str]], finding_key: Optional[tuple[str, str]]) -> bool:
    """_veto_match 的布尔形态（供测试与只关心命中与否的判据复用；登记去重已改走 marks-based，不再经此）。"""
    return _veto_match(veto_key, finding_key) != _VETO_MATCH_NONE


@dataclass(frozen=True)
class _VetoMarks:
    """一轮诊断结论逐条发现项的否决判定结果（读投影时现算，不落库）。

    为什么现算而不是在结论落库时打标：否决可以撤销，撤销后必须恢复计入阻断。写入时固化标记，
    撤销就无法回溯重算；读时现算天然随撤销恢复，且判据是纯函数，随时可复验。
    """

    by_finding_ref: dict[str, "FindingVetoRow"]  # 命中否决的发现项行 → 命中的那条留痕
    by_meta_index: dict[int, "FindingVetoRow"]   # 存量轮次（元数据无引用）的下标回退
    fingerprintable: set[str]                    # 可指纹化的发现项行（界面才给否决入口）
    blocking_open: int                           # 本轮仍然成立的阻断问题条数
    all_blocking_vetoed: bool                    # 曾报阻断问题，且已被逐条否决、一条不剩
    #: 本轮报过阻断问题，且此刻一条待处理的都不剩（被裁定或被降格都算）。与上一条的差别：
    #: 上一条只认用户的逐条裁定，这一条还认人工确认降格，用于开直接确认通道（K5）。
    blocking_cleared: bool
    blocking_veto_count: int                     # 命中阻断问题的去重否决行数（＝用户裁定过几条）
    #: 因条目已有人工确认来源而降格为非阻断提示的发现项行（同样读时现算：背书是粘性事实，
    #: 但降格结果随规则码/类型变化，落库同样会失真）。
    attested_source_refs: set[str]
    #: 同一批降格发现项的读出序下标。存量轮次的质量元数据没有 finding_ref，修订点只能经
    #: 「元数据下标」找回它针对的发现项（与 by_meta_index 同一条回退路径），故降格集合也要
    #: 备一份下标口径，否则那些轮次的降格点剔不掉。
    attested_meta_indexes: set[int]


class ItemReviewService:
    """条目评审服务（诊断 + 结论裁决 + 对话 + 确认/终止 + 工作区读视图）。"""

    def __init__(
        self,
        model_orchestration: ModelOrchestration,
        model_results: ModelResultRepository,
        process_records: ProcessRecordRepository,
        formation_process: ItemFormationProcessRepository,
        items: RequirementItemRepository,
        source_assets: SourceAssetRepository,
        reviews: ItemReviewRepository,
        draft_composer: Optional[ItemDraftComposer] = None,
        explainer: Optional[ItemExplainer] = None,
        reeval_responder: Optional[ItemReevalResponder] = None,
        source_candidate_composer: Optional[ItemSourceCandidateComposer] = None,
        # 采纳修订承接方（= 对象层 apply_item_revision）；接受 origin 关键字参数
        # （阶段策略解耦 P1：采纳路径传 origin="review_adoption" 标注 ItemRevised 事件来源）。
        revision_applier: Optional[Callable[..., object]] = None,
        command_interpreter=None,  # AEP-095 斜杠命令解释 lane（可选注入；deps 装配）
        trace_links=None,  # P7：支撑依据边只读（评审「业务依据」段；可选注入，缺则段为空）
    ) -> None:
        self._model_orchestration = model_orchestration
        self._model_results = model_results
        self._process_records = process_records
        self._formation_process = formation_process
        self._items = items
        self._source_assets = source_assets
        self._reviews = reviews
        self._draft_composer = draft_composer or StubItemDraftComposer()
        self._explainer = explainer or StubItemExplainer()
        self._reeval_responder = reeval_responder or StubItemReevalResponder()
        self._source_candidate_composer = (
            source_candidate_composer or StubItemSourceCandidateComposer()
        )
        # AEP-036 承接方（需求条目服务）；由装配层注入，避免服务间硬依赖
        self.revision_applier = revision_applier
        self._command_interpreter = command_interpreter
        self._trace_links = trace_links

    # ------------------------------------------------------------------
    # AEP-032：诊断请求提交（批次级提交、条目级处理、条目级实时返回）
    # ------------------------------------------------------------------

    def start_item_diagnosis(
        self, command: ItemReviewDiagnosisCommand,
        trigger: DiagnosisTrigger = DiagnosisTrigger.USER_SUBMIT,
    ) -> ItemReviewDiagnosisRequestResult:
        replay = self._reviews.find_batch_by_idempotency(command.idempotency_key)
        if replay is not None:  # 幂等重放：返回原批次，不重复受理
            batch = self._reviews.get_batch(replay)
            return ItemReviewDiagnosisRequestResult(
                status="submitted",
                review_context_ref=batch.review_context_ref if batch else None,
            )

        if command.diagnosis_mode not in _DIAGNOSIS_MODES:
            log_event(_COMPONENT, "review.diagnosis.rejected", level="WARN",
                      reject_reason="invalid_mode", ok=False)
            return ItemReviewDiagnosisRequestResult(
                status="rejected_precheck",
                next_action="诊断模式不可识别，请重新选择快速/标准/全面/增量后提交",
            )
        if not command.item_refs:
            return ItemReviewDiagnosisRequestResult(
                status="rejected_precheck",
                next_action="请先勾选需要诊断的待确认条目",
            )

        items: list[ItemRow] = []
        for ref in command.item_refs:
            item = self._items.get_item(ref)
            if item is None or item.project_ref != command.project_ref:
                return ItemReviewDiagnosisRequestResult(
                    status="rejected_precheck",
                    next_action="选定条目不在当前项目或已不存在，请刷新工作区后重新选择",
                )
            if item.status != IS.PENDING_CONFIRMATION.value:
                return ItemReviewDiagnosisRequestResult(
                    status="rejected_precheck",
                    next_action="选定条目不处于待确认状态，不能进入评审诊断，请重新选择",
                )
            if self._reviews.has_running_round(item.id):
                return ItemReviewDiagnosisRequestResult(
                    status="rejected_precheck",
                    next_action="选定条目正在诊断中，请等待本轮结束后再提交",
                )
            if trigger is DiagnosisTrigger.USER_SUBMIT and self._open_supplement_gaps(item.id):
                return ItemReviewDiagnosisRequestResult(
                    status="rejected_precheck",
                    next_action="选定条目存在未闭合的来源缺口（已采纳「建议补充来源」），请先补充来源或修订表达",
                )
            items.append(item)

        parse_result_ref = items[0].parse_result_ref
        if any(i.parse_result_ref != parse_result_ref for i in items):
            raise InvalidInput("一次诊断批次只能覆盖同一要素工作区的条目")
        parse_context = self._source_assets.parse_context_of(parse_result_ref)
        if parse_context is None:
            raise NotFound("条目所属要素工作区不存在")
        current = str(self._process_records.read_workspace_version(parse_context))
        if trigger is DiagnosisTrigger.USER_SUBMIT and command.workspace_version != current:
            return ItemReviewDiagnosisRequestResult(
                status="rejected_precheck",
                next_action="工作区已更新（版本不一致），请刷新后重试",
            )

        batch_ref = self._reviews.create_batch(
            command.project_ref, parse_context, parse_result_ref,
            items[0].formation_context_ref, json.dumps([i.id for i in items]),
            command.diagnosis_mode, command.operator_ref, command.idempotency_key,
        )
        coverage = _MODE_COVERAGE[command.diagnosis_mode]
        for item in items:
            self._reviews.create_round(
                command.project_ref, item.id, batch_ref, command.diagnosis_mode,
                coverage, trigger=trigger.value,
            )

        run = self._model_orchestration.request_item_diagnosis(batch_ref)
        log_event(_COMPONENT, "review.diagnosis.submitted", batch_ref=batch_ref,
                  item_count=len(items), diagnosis_mode=command.diagnosis_mode,
                  trigger=trigger.value, ok=True)
        return ItemReviewDiagnosisRequestResult(
            status="submitted",
            review_context_ref=items[0].formation_context_ref,
            agent_run_ref=run,
        )

    def _has_user_initiated_diagnosis(self, item_ref: str) -> bool:
        """该条目是否曾进入用户显式发起的诊断生命周期（含已失效轮次）。

        白名单口径（详设「采纳副作用链」行不变式）：只认用户显式发起的 trigger，即
        ``user_submit`` 与 ``dialogue_reeval``（后者要求已有站立结论，永不可能是首轮）。
        采白名单而非黑名单是为了让未来新增的 trigger 枚举值**失败关闭**——不被误当作诊断史。
        NULL trigger 按 ``user_submit`` 计（历史数据兜底，见仓储 coalesce 归一）。

        为何失效轮次仍算数：`apply_item_revision` 先失效旧轮再回调本链，链式复诊前置
        必然全是失效轮，要求"未失效"会使合法链永不触发。
        为何链式轮次不算历史：本守卫落地之前，从未诊断的条目会被凭空产生首轮
        revision_chained，那类历史数据里的链式轮恰恰证明用户从未要求过诊断。
        """
        return self._reviews.has_user_initiated_round(item_ref)

    def start_chained_incremental(
        self, item_ref: str, revision_record_ref: str, operator_ref: str,
    ) -> ChainedDiagnosisOutcome:
        """修订应用后的链式自动增量诊断（v5 强制回环；幂等键由修订记录派生）。

        首诊永远由用户显式发起：无诊断史的条目（形成阶段修订即此语境）按设计跳过，
        不凭空产生首轮结论。返回三态结果（成功／无史跳过／被拒），语义不再以 run_ref 是否为空反推
        （漏斗单点，见 ChainedDiagnosisOutcome）——但目前无生产调用方消费该结果，仅测试读它。

        往复次数不设上限：评审是「AI 提建议 → 用户给反馈」的过程，终点只有 AI 判通过与人工撤回
        两个，都由人来定（2026-07-20 用户拍板废除原采纳链空转熔断）。
        """
        item = self._items.get_item(item_ref)
        if item is None or item.status != IS.PENDING_CONFIRMATION.value:
            return ChainedDiagnosisOutcome(status="skipped_no_history")
        if not self._has_user_initiated_diagnosis(item_ref):
            log_event(_COMPONENT, "review.chained.skipped_no_history", item_ref=item_ref,
                      revision_record_ref=revision_record_ref, ok=True)
            return ChainedDiagnosisOutcome(status="skipped_no_history")
        # 采纳次数只记日志、不设闸（2026-07-20 用户拍板废除熔断，理由见 _REPEATED_REVISE_HINT_AT）：
        # 往复次数多是「这条不好改」的信号，值得留痕给人看，但停不停由用户决定，不由计数决定。
        adopted = self._reviews.count_adopted_revise_rounds(item_ref)
        if adopted >= _REPEATED_REVISE_HINT_AT:
            log_event(_COMPONENT, "review.chained.repeated_revise", level="WARN",
                      item_ref=item_ref, revision_record_ref=revision_record_ref,
                      adopted_revise_rounds=adopted, ok=True)
        parse_context = self._source_assets.parse_context_of(item.parse_result_ref)
        version = str(self._process_records.read_workspace_version(parse_context)) if parse_context else "1"
        result = self.start_item_diagnosis(
            ItemReviewDiagnosisCommand(
                project_ref=item.project_ref, item_refs=[item.id],
                diagnosis_mode=DiagnosisMode.INCREMENTAL.value,
                workspace_version=version, operator_ref=operator_ref,
                idempotency_key=f"chain:{revision_record_ref}",
            ),
            trigger=DiagnosisTrigger.REVISION_CHAINED,
        )
        if result.status != "submitted":
            reason = result.next_action or "链式增量诊断未被承接"
            log_event(_COMPONENT, "review.chained.skipped", level="WARN",
                      item_ref=item_ref, reject_reason=reason, ok=False)
            return ChainedDiagnosisOutcome(status="rejected", note=reason)
        return ChainedDiagnosisOutcome(status="submitted", agent_run_ref=result.agent_run_ref)

    # ------------------------------------------------------------------
    # 编排回调：执行前准入复核 + 诊断上下文组装
    # ------------------------------------------------------------------

    def prepare_item_diagnosis(self, batch_ref: str, item_ref: str) -> Optional[dict]:
        """返回该条目的诊断执行上下文；不能送检时标记未能进行诊断并返回 None。"""
        batch = self._reviews.get_batch(batch_ref)
        round_ = self._reviews.running_round_of(batch_ref, item_ref)
        if batch is None or round_ is None:
            return None
        item = self._items.get_item(item_ref)
        if item is None or item.status != IS.PENDING_CONFIRMATION.value:
            self._reviews.finish_round(
                round_.id, DPS.NOT_DIAGNOSABLE.value,
                reason="条目已离开待确认状态，本轮不能继续诊断；请刷新后重新选择",
            )
            log_event(_COMPONENT, "review.item.not_diagnosable", item_ref=item_ref,
                      batch_ref=batch_ref, ok=False)
            return None

        def _source_row(element) -> dict:
            quote = first_anchor_quote(element.source_anchor)
            return {
                "id": element.id, "element_type": element.element_type,
                "content": element.content, "source_quote": quote,
            }

        source_refs = list(json.loads(item.source_element_refs or "[]"))
        sources: list[dict] = []  # 需求来源（element_item 上游要素）
        for ref in source_refs:
            element = self._source_assets.get_element(ref)
            if element is not None:
                sources.append(_source_row(element))
        # P7 §2.1 业务依据段：引用登记的 supporting_basis 上游业务知识（读时投影）
        business_sources: list[dict] = []
        if self._trace_links is not None:
            for ref in self._trace_links.supporting_basis_upstream_refs(item.id):
                element = self._source_assets.get_element(ref)
                if element is not None and not element.superseded:
                    business_sources.append(_source_row(element))
        if source_refs and not sources:
            self._reviews.finish_round(
                round_.id, DPS.NOT_DIAGNOSABLE.value,
                reason="来源要素或上下文无法取回，本轮不能继续诊断；请回需求分析检查来源",
            )
            log_event(_COMPONENT, "review.item.not_diagnosable", item_ref=item_ref,
                      batch_ref=batch_ref, ok=False)
            return None

        mode = batch.diagnosis_mode
        parse_context = self._source_assets.parse_context_of(item.parse_result_ref)
        raw_text = ""
        if mode != DiagnosisMode.QUICK.value and parse_context is not None:
            material_ref = self._process_records.read_parse_material_ref(parse_context)
            content = self._source_assets.read_material_content(material_ref) if material_ref else None
            raw_text = content.raw_text if content else ""

        # 只把真实字段修订喂给增量诊断提示词。人工确认背书借表落库、field_key 不在提示词
        # 声明的可修订字段封闭集内，若不过滤，模型会收到一条「字段从空改成『已人工确认…』」的
        # 伪修订并被要求重点核对，且用户理由原文一并入提示词（NON_REVISION_FIELD_KEYS 单点）。
        # 属性字段修订不过滤——保「无背书条目提示词逐字节不变」。
        all_revisions = self._items.revisions_of(item.id)  # 单次查询，供修订清单与背书区块共用
        revisions: list[dict] = []
        if mode in (DiagnosisMode.STANDARD.value, DiagnosisMode.INCREMENTAL.value,
                    DiagnosisMode.COMPREHENSIVE.value):
            revisions = [
                {"field_key": r.field_key, "before_value": r.before_value,
                 "after_value": r.after_value, "reason": r.reason}
                for r in all_revisions
                if r.field_key not in NON_REVISION_FIELD_KEYS
            ]
            filtered = len(all_revisions) - len(revisions)
            if filtered:
                # 背书等非修订行不入提示词是刻意的，但静默丢弃会掩盖「这条信息没进上下文」；命中时记一条
                # （仅 filtered>0，避免每次诊断都发噪声）。
                log_event(_COMPONENT, "review.diagnosis.revisions_filtered",
                          item_ref=item_ref, filtered=filtered)

        # 人工确认背书进上下文（T20260721-attested-diagnosis-context A）：与上面那条过滤是同一件事的
        # 两半——伪修订行不入提示词（那个形态会被模型当成"字段改过"重点核对），背书事实改由本区块以
        # 正规形态进入。**只在条目确有背书时给值**：无背书条目 attestation=None，模板里那一
        # **分段**不渲染，user 块逐字节不变（A1 护栏）；system 块的处置规则则是对所有条目统一
        # 新增的，不受此条件约束。理由原文按既有字段同等对待，不转义不摘编。
        # 不分诊断模式：quick 模式没有材料原文，模型更容易判「材料未记载」，背书信息在该模式下更需要。
        attestation_read = self._project_attestation(all_revisions)
        attestation: Optional[dict] = None
        if attestation_read is not None:
            attestation = {
                "reason": attestation_read.reason,
                "operator_ref": attestation_read.operator_ref,
                "at": attestation_read.at,
            }

        prior_findings: list[dict] = []
        if mode == DiagnosisMode.INCREMENTAL.value:
            prior_findings = self._prior_findings_of(item.id, exclude_round=round_.id)

        confirmed_items: list[dict] = []
        if mode == DiagnosisMode.COMPREHENSIVE.value:
            confirmed_items = [
                {"req_no": i.req_no, "expression": i.expression, "req_type": i.req_type}
                for i in self._items.items_of_parse_result(item.parse_result_ref)
                if i.status == IS.CONFIRMED.value
            ]

        return {
            "project_ref": item.project_ref,
            "diagnosis_mode": mode,
            "item": {
                "item_ref": item.id, "req_no": item.req_no,
                "expression": item.expression, "req_type": item.req_type,
                "related_confirmed_items": confirmed_items,
            },
            "sources": sources,
            "business_sources": business_sources,  # P7 业务依据段（与业务知识一致性判据用）
            "raw_text": raw_text,
            "revisions": revisions,
            "attestation": attestation,  # 人工确认背书（可空；无背书时模板整段不渲染）
            "prior_findings": prior_findings,
            # v5：已排除修订点（不得重复纠缠）与对话上下文
            "excluded_points": self._excluded_points_of(item.id),
            "thread_context": self._thread_context_of(item.id),
        }

    def _prior_findings_of(self, item_ref: str, exclude_round: str) -> list[dict]:
        # 无用户显式发起的诊断史时，仅有的轮次是凭空链式首轮（守卫已作废），其发现项不得
        # 作为"前序发现"喂给首次真诊断的模型。复用血统谓词而非按 invalidated 过滤——后者会
        # 打断合法增量诊断（合法链的前置轮必然全已失效）。
        if not self._has_user_initiated_diagnosis(item_ref):
            return []
        for round_ in self._rounds_of_item(item_ref):
            if round_.id == exclude_round or round_.processing_status != DPS.COMPLETED.value:
                continue
            return [
                {"finding_type": f.finding_type, "diagnosis_summary": f.diagnosis_summary}
                for f in self._reviews.findings_of_round(round_.id)
            ]
        return []

    def _rounds_of_item(self, item_ref: str) -> list[DiagnosisRoundRow]:
        """条目全部轮次（新→旧）。仓储只暴露 latest；此处经批次聚合避免加宽接口。"""
        item = self._items.get_item(item_ref)
        if item is None:
            return []
        rounds: list[DiagnosisRoundRow] = []
        for batch in self._reviews.batches_of_parse_result(item.parse_result_ref):
            rounds.extend(r for r in self._reviews.rounds_of_batch(batch.id) if r.item_ref == item_ref)
        rounds.sort(key=lambda r: r.round_no, reverse=True)
        return rounds

    def _excluded_points_of(self, item_ref: str) -> list[dict]:
        """用户已经表过态、不得重复纠缠的两类事项（供重评/增量诊断上下文）。

        两类合用同一个上下文通道，各自带 kind 与 note 自述身份：
        - excluded_point：采纳修订时没有勾选的那些点（既有口径）；
        - vetoed_finding：用户明确裁定「这不是问题」的问题（AEP-116，新增）。
        提示词层是软约束——模型换个措辞照样能重提；真正拦住重提的是读投影时的确定性判定
        （见 _mark_vetoes）。这里注入只为减少无谓的来回，不作为防重提的依靠。
        """
        # 与 _prior_findings_of 同戒：无用户诊断史时不把凭空链式轮的排除点喂给首次真诊断。
        if not self._has_user_initiated_diagnosis(item_ref):
            return []
        excluded: list[dict] = []
        for round_ in self._rounds_of_item(item_ref):
            refs = set(json.loads(round_.excluded_point_refs or "[]"))
            if not refs:
                continue
            for p in json.loads(round_.revision_points or "[]"):
                if str(p.get("point_ref")) in refs:
                    excluded.append({
                        "kind": "excluded_point",
                        "label": p.get("label"), "find": p.get("find"),
                        "replace": p.get("replace"), "note": "用户已排除",
                    })
        return excluded + self._vetoed_findings_of(item_ref)

    # ------------------------------------------------------------------
    # AEP-116：问题否决（登记/撤销 + 逐轮命中判定）
    # ------------------------------------------------------------------

    def _vetoed_findings_of(self, item_ref: str) -> list[dict]:
        """生效中的否决，整理成诊断上下文条目（供提示词层：不得再提这些问题）。"""
        return [
            {
                "kind": "vetoed_finding",
                "rule_code": v.rule_code,
                "evidence_span": v.evidence_span,
                "summary": v.finding_summary,
                "user_reason": v.reason,
                "note": "用户已裁定这不是问题，本轮不得再提",
            }
            for v in self._reviews.vetoes_of_item(item_ref)
        ]

    def _mark_vetoes(
        self, round_: DiagnosisRoundRow, rows: list, vetoes: list[FindingVetoRow],
        attested: bool = False,
    ) -> _VetoMarks:
        """把该轮每条发现项与否决集合比对一遍（纯字符串判据，见 _veto_match）。

        指纹的两个字段（规则码/证据片段）不在发现项行上，而在轮次的 quality_meta 里，
        按 finding_ref 配回（存量轮次无引用时回退下标，与 _project_round 同一口径）。

        同轮唯一命中（C1 根治，2026-07-21）：一条否决在本轮至多命中一条发现项。同一轮里的
        两条发现项按定义就是模型分别报出的两个不同问题，一次否决不该同时消解它们——此前
        逐条各取「第一个命中的否决」，嵌套片段下一次点击会连带消解另一条问题并放行确认。
        多个候选时取匹配强度最高者（完全相等 > 片段被包含），强度相同取读出序在前的一条。
        """
        quality = json.loads(round_.quality_meta) if round_.quality_meta else {}
        fmeta = quality.get("findings") or []
        meta_by_ref = {str(m.get("finding_ref")): m for m in fmeta if m.get("finding_ref")}
        fingerprintable: set[str] = set()
        finding_keys: list[Optional[tuple[str, str]]] = []
        for i, f in enumerate(rows):
            meta = meta_by_ref.get(str(f.id))
            if meta is None:
                meta = fmeta[i] if not meta_by_ref and i < len(fmeta) else {}
            key = _veto_key(meta.get("rule_code"), meta.get("evidence_span"), f.finding_type)
            finding_keys.append(key)
            if key is not None:
                fingerprintable.add(str(f.id))

        by_ref: dict[str, FindingVetoRow] = {}
        by_index: dict[int, FindingVetoRow] = {}
        for veto in vetoes:  # 否决按 (created_at, id) 定序，逐条认领本轮至多一条发现项
            veto_key = _veto_key(veto.rule_code, veto.evidence_span, veto.finding_type)
            origin = str(veto.origin_finding_ref or "")
            best_index, best_rank = -1, ()
            for i, key in enumerate(finding_keys):
                strength = _veto_match(veto_key, key)
                if strength == _VETO_MATCH_NONE:
                    continue
                # 择一顺序：匹配强度 > 就是当初被否决的那一行 > 阻断问题优先 > 读出序在前。
                # 「就是那一行」用 origin_finding_ref 判：同指纹的兄弟发现项抢不走它。
                # 「阻断优先」保证同指纹下 no_blocker 不会把用户的裁定吸走、留着阻断项挡确认。
                rank = (strength, str(rows[i].id) == origin, rows[i].finding_type != "no_blocker", -i)
                if rank > best_rank:
                    best_index, best_rank = i, rank
            if best_index < 0:
                continue
            claimed = rows[best_index]
            by_ref.setdefault(str(claimed.id), veto)
            by_index.setdefault(best_index, veto)

        # 人工确认降格（T20260721-attested-diagnosis-context A3）：条目的来源缺口已由具名操作者
        # 确认闭合后，「表达与来源要素对不上」这类发现项不再是阻断问题——它报的正是那个已被
        # 授权例外闭合的缺口。降格谓词刻意收窄到来源对齐一类：
        # - 取 source_inconsistency 类型，且规则码必须**明确等于** SRC-DRIFT（白名单，2026-07-25
        #   冷审查 K6 消费）。此前的写法是「规则码不等于 BIZ-RULE-CONFLICT 就降格」，规则码取
        #   不到（存量轮次无 quality_meta、部分带引用的元数据配不上、模型漏写或写错枚举被
        #   llm.py 置 None）时一律落到降格分支——红线在信息缺失时倒向宽松，「与业务规则矛盾」
        #   报成 source_inconsistency 却漏写规则码就被静默放行。白名单让信息缺失倒向保守：
        #   取不到规则码就不降格，该发现项照旧阻断，用户仍可逐条裁定「不是问题」。
        # - BIZ-RULE-CONFLICT 的显式排除保留（白名单已蕴含，留作可读的红线声明）；比较前
        #   strip，外部产生的元数据带首尾空白时不至于漏判。
        # - 歧义/可测试性/字段缺漏等其余判据一条都不降格（红线：背书≠有材料出处）。
        #
        # 已知边界（本次不处置，留待后续裁定）：条目人工确认之后又登记了真实来源，此后来源
        # 被修订而条目取值变旧，那是一条**真实的**来源漂移，规则码同样是 SRC-DRIFT，会被一次
        # 与它无关的人工确认压成非阻断提示。窄化到「确认时间早于该轮」可解，但那要连同 K12
        # 的历史轮追溯降格一并拍板，不在本次范围内。
        attested_source_refs: set[str] = set()
        attested_meta_indexes: set[int] = set()
        if attested:
            for i, f in enumerate(rows):
                if f.finding_type != "source_inconsistency":
                    continue
                meta = meta_by_ref.get(str(f.id))
                if meta is None:
                    meta = fmeta[i] if not meta_by_ref and i < len(fmeta) else {}
                rule_code = str(meta.get("rule_code") or "").strip()
                if rule_code == RequirementQualityRule.BIZ_RULE_CONFLICT.value:
                    continue
                if rule_code != RequirementQualityRule.SRC_DRIFT.value:
                    continue
                attested_source_refs.add(str(f.id))
                attested_meta_indexes.add(i)

        # 三个口径分开数，谁也别顶替谁（2026-07-25 冷审查 K5/K10(c) 消费）：
        # - blocking_reported：模型这一轮**报出过**几条阻断类问题（降格与否决都不减）。它回答
        #   「这一轮本来有没有问题」，是「曾有阻断」这个前提的唯一依据。
        # - blocking_total：降格之后还算问题的条数。降格项不计入，是因为界面上它已经不在问题
        #   列表里了；用它去判「是否曾有问题」会把全员降格的一轮说成从来没有问题。
        # - blocking_open：此刻仍要用户处理的条数（既没被否决、也没被降格）。
        blocking_reported = sum(1 for f in rows if f.finding_type != "no_blocker")
        blocking_total = sum(
            1 for f in rows
            if f.finding_type != "no_blocker" and str(f.id) not in attested_source_refs
        )
        blocking_open = sum(
            1 for f in rows
            if f.finding_type != "no_blocker" and str(f.id) not in by_ref
            and str(f.id) not in attested_source_refs
        )
        # 用户实际裁定过的条数＝命中阻断问题的**去重否决行**数。不取命中的发现项条数：
        # 「一条否决命中了几条发现项」不是「用户点了几次」，据此写留痕会让账面数字大于
        # 界面上出现过的问题数（本仓纪律：计数须与用户可见输入自洽）。
        # 不再把降格项排除在外（K10(c)）：用户在人工确认之前就裁定过的那一条，确认之后
        # 按指纹重新认领同一个问题，此前既不计数也不显示，于是「已被逐条裁定（N 条）」比
        # 用户在留痕列表里数得到的条数少。这个数问的是「用户点过几次」，降格与它无关。
        blocking_veto_refs = {
            str(by_ref[str(f.id)].id) for f in rows
            if f.finding_type != "no_blocker" and str(f.id) in by_ref
        }
        return _VetoMarks(
            by_finding_ref=by_ref, by_meta_index=by_index,
            # 降格项不再给「这不是问题」入口：它已经不是问题了，对一条提示问「是不是问题」
            # 只会让用户以为自己还欠一次处理。
            fingerprintable=fingerprintable - attested_source_refs,
            blocking_open=blocking_open,
            all_blocking_vetoed=blocking_total > 0 and blocking_open == 0,
            # 「本轮报过阻断问题，现在一条待处理的都不剩」——不问那些问题是被用户裁掉的还是
            # 因人工确认降格的。直接确认通道按这一条开门（K5）：界面上零待处理时把通道关掉，
            # 用户就只能走覆盖确认，而覆盖确认要填理由、要打覆盖标记进效能统计，账目会失真。
            # 与 all_blocking_vetoed 分成两个谓词，是因为留痕那句「已被逐条裁定（N 条）」必须
            # 继续只由真实裁定驱动，不能被降格顶开（见 confirm_item 的 collapse_reason）。
            blocking_cleared=blocking_reported > 0 and blocking_open == 0,
            blocking_veto_count=len(blocking_veto_refs),
            attested_source_refs=attested_source_refs,
            attested_meta_indexes=attested_meta_indexes,
        )

    def _vetoed_point_refs(self, round_: DiagnosisRoundRow, points: list[dict]) -> set[str]:
        """该轮里「所针对的问题已经不用用户处理」的修订点——这些改法默认不采纳。

        两类问题都算，它们对采纳链的效果相同：
        - 用户裁定为「不是问题」的（AEP-116 否决）；
        - 因条目已有人工确认来源而降格为提示的来源对齐类发现（2026-07-25 冷审查 K1 消费）。
          区5 的结论卡对用户写着「AI 就此给过改法，但这条不用改，**采纳时不会应用它**」，此前
          后端一行都没为这句话改过：绑在降格发现项上的修订点仍留在默认采纳集里，用户不点名
          修订点直接采纳（区5 斜杠命令恒传 None）就会把它应用上去，条目表达被改写而用户从未
          选过那个改法。降格与否决在这里必须同等对待，否则界面承诺与写库行为相反。

        点经 finding_index 指向轮次 quality_meta 的同序元数据，再由元数据的 finding_ref
        落到发现项行（存量轮次无引用时回退下标，与读投影同一口径）。
        """
        marks = self._marks_of_round(round_)
        quality = json.loads(round_.quality_meta) if round_.quality_meta else {}
        fmeta = quality.get("findings") or []
        out: set[str] = set()
        for p in points:
            index = int(p.get("finding_index") or 0)
            ref = str(fmeta[index].get("finding_ref") or "") if 0 <= index < len(fmeta) else ""
            if ref:
                excluded = ref in marks.by_finding_ref or ref in marks.attested_source_refs
            else:
                excluded = index in marks.by_meta_index or index in marks.attested_meta_indexes
            if excluded:
                out.add(str(p.get("point_ref")))
        return out

    def _marks_of_round(self, round_: DiagnosisRoundRow) -> _VetoMarks:
        """该轮的否决判定（自取发现项与否决集合；供确认门禁等非投影路径复算）。"""
        return self._mark_vetoes(
            round_, self._reviews.findings_of_round(round_.id),
            self._reviews.vetoes_of_item(round_.item_ref),
            attested=self._is_attested(round_.item_ref),
        )

    def _is_attested(self, item_ref: str) -> bool:
        """条目是否已有人工确认来源（降格判据的输入；读时现算，与背书投影同一单点）。"""
        return self._project_attestation(self._items.revisions_of(item_ref)) is not None

    def record_finding_veto(self, command: FindingVetoCommand) -> ItemReviewWorkspaceRead:
        """AEP-116：把一条诊断问题裁定为「不是问题」，或撤销该裁定。

        否决登记的是问题指纹（规则码 + 证据片段），此后所有轮次里同一个问题都不再计入阻断；
        撤销写撤销时间而不删行——用户否决过又改主意，这个事实本身要留住。

        C46：拒绝分支与「同指纹已存在」的静默返回都落痕。用户点了却什么都没发生的两种情形
        （被拒、被去重）此前在日志里毫无踪迹，事后无法解释他看到的是什么。理由与证据片段
        不进日志（硬规矩第 8 条），只记引用与稳定的拒绝码。
        """
        def _reject(reason_code: str, message: str, exc=InvalidInput):
            log_event(_COMPONENT, "review.finding.veto_rejected", item_ref=command.item_ref,
                      finding_ref=command.finding_ref, veto_ref=command.veto_ref,
                      action=(command.action or "").strip() or None,
                      operator_ref=command.operator_ref, reject_reason=reason_code,
                      level="WARN", ok=False)
            return exc(message)

        item = self._items.get_item(command.item_ref)
        if item is None or item.project_ref != command.project_ref:
            raise _reject("item_not_found", "需求条目不存在", NotFound)
        action = (command.action or "").strip()
        if action not in ("veto", "restore"):
            raise _reject("unsupported_action", "不支持的操作；只能是标记不是问题或撤销标记")

        if action == "restore":
            if not command.veto_ref:
                raise _reject("missing_veto_ref", "撤销标记必须指明要撤销哪一条")
            veto = self._reviews.get_finding_veto(command.veto_ref)
            if veto is None or veto.item_ref != item.id:
                raise _reject("veto_not_found", "该标记不存在", NotFound)
            self._reviews.revoke_finding_veto(veto.id, command.operator_ref)
            log_event(_COMPONENT, "review.finding.veto_revoked", item_ref=item.id,
                      veto_ref=veto.id, operator_ref=command.operator_ref, ok=True)
            return self._workspace_of_item(item.id)

        replay = self._reviews.find_veto_by_idempotency(command.idempotency_key)
        if replay is not None:  # 幂等重放
            return self._workspace_of_item(item.id)
        if not command.finding_ref:
            raise _reject("missing_finding_ref", "必须指明是哪一条问题")
        finding = self._reviews.get_finding(command.finding_ref)
        if finding is None or finding.item_ref != item.id:
            raise _reject("finding_not_found", "该问题不存在", NotFound)
        round_ = next(
            (r for r in self._rounds_of_item(item.id) if r.id == finding.round_ref), None,
        )
        if round_ is None:
            raise _reject("round_not_found", "该问题所属的诊断轮次不存在", NotFound)
        quality = json.loads(round_.quality_meta) if round_.quality_meta else {}
        fmeta = quality.get("findings") or []
        meta = next((m for m in fmeta if str(m.get("finding_ref")) == finding.id), None)
        if meta is None:  # 存量轮次的元数据没有引用，退回读出序下标（与投影同口径）
            rows = self._reviews.findings_of_round(round_.id)
            index = next((i for i, f in enumerate(rows) if f.id == finding.id), -1)
            meta = fmeta[index] if 0 <= index < len(fmeta) else {}
        marks = self._marks_of_round(round_)
        if str(finding.id) in marks.attested_source_refs:
            # 降格项没有「这不是问题」入口（读投影把它移出 fingerprintable），但界面隐藏不是
            # 门禁：页面陈旧或直接调接口仍能对一条提示登记否决，而这行否决既不进阻断计数、
            # 也不在卡片上显示，等于一次没有任何可见后果的写入（K10(b)）。与 can_veto=False
            # 对称，服务端显式拒绝并说清它已经不需要处理。
            raise _reject(
                "already_attested_degraded",
                "这条已经不需要你处理了——这条需求的来源已由人工确认，它不再算阻断问题，"
                "不用再标一次「不是问题」",
                RejectedTransition,
            )
        key = _veto_key(meta.get("rule_code"), meta.get("evidence_span"), finding.finding_type)
        if key is None:
            # 没有可复算的定位依据就无法跨轮认出同一个问题。此时宁可拒绝，也不退而用问题摘要
            # 之类的自由文本当匹配键——那样的标记下一轮就会误命中别的问题。
            raise _reject(
                "not_fingerprintable",
                "这条问题没有可定位的原文依据，暂时无法标记；可改用拒绝结论并说明理由",
            )
        # 去重口径必须与打标口径同源：判「这条问题是否已被标记」＝它在本轮是否已被某条否决
        # 认领（_mark_vetoes 的同轮唯一命中），而不是「有没有哪条否决的指纹与它匹配」。两者
        # 分家会造出死路：用户先否决整句、再否决其中的从句，后者按指纹算「已标记过」被静默
        # 吞掉，可它在本轮并没有被认领，于是照旧阻断，用户再点多少次都没有反应。
        existing = marks.by_finding_ref.get(str(finding.id))
        if existing is not None:  # 同一个问题已标记过，不重复记账
            log_event(_COMPONENT, "review.finding.veto_deduplicated", item_ref=item.id,
                      finding_ref=finding.id, veto_ref=existing.id, round_ref=round_.id,
                      operator_ref=command.operator_ref, ok=True)
            return self._workspace_of_item(item.id)
        veto_ref = self._reviews.add_finding_veto(
            project_ref=item.project_ref, item_ref=item.id,
            finding_type=finding.finding_type,
            rule_code=meta.get("rule_code"), evidence_span=meta.get("evidence_span"),
            finding_summary=finding.diagnosis_summary or "",
            origin_finding_ref=finding.id,
            reason=(command.reason or "").strip() or None,
            operator_ref=command.operator_ref, idempotency_key=command.idempotency_key,
        )
        log_event(_COMPONENT, "review.finding.vetoed", item_ref=item.id,
                  veto_ref=veto_ref, round_ref=round_.id,
                  rule_code=str(meta.get("rule_code") or ""),
                  operator_ref=command.operator_ref, ok=True)
        return self._workspace_of_item(item.id)

    def attest_source(self, command: SourceAttestationCommand) -> ItemReviewWorkspaceRead:
        """人工确认背书：材料里漏写了这条，人工确认它是真实需求，条目据此离开「待补充来源」。

        这是对「条目的依据必须能在材料里指出来」的授权例外，所以做法上有两条红线：

        1. **不伪造材料锚点**。只写一条 field_key=source_attestation 的修订记录留下
           操作者/理由/时间，条目的 source_element_refs 一个字都不动——绝不塞一个假的
           要素编号进去骗过下游，也不生成任何引文。背书在读视图里是与来源要素并列的
           独立证据类别（source_attestation 字段），不混进来源要素清单。
        2. **不直接改状态位**。「待补充来源」是派生态，成立条件是最新一轮为已采纳且未失效的
           supplement 结论（见 _open_supplement_gaps）。所以离开该态的正当做法是让那一轮失效，
           而不是去清什么标志位——与「登记来源」殊途同归，只是失效理由不同。

        不设撤销：让轮次失效不可逆，撤销只会留下自相矛盾的记录。这不构成死胡同——后来发现
        材料确有出处就正常登记真实来源（背书记录作为历史留着），重新诊断后若 AI 再判需要
        补充来源并被采纳，条目会自然回到该态。
        """
        # 失败/幂等各分支均留痕（C31）。理由原文属敏感用户输入，一律不进日志（脱敏边界），
        # 只记条目、操作者与拒绝码。
        item = self._items.get_item(command.item_ref)
        if item is None or item.project_ref != command.project_ref:
            log_event(_COMPONENT, "review.source.attest_rejected", level="WARN",
                      item_ref=command.item_ref, reason_code="item_not_found", ok=False)
            raise NotFound("需求条目不存在")
        reason = (command.reason or "").strip()
        if not reason:
            log_event(_COMPONENT, "review.source.attest_rejected", level="WARN",
                      item_ref=item.id, reason_code="reason_empty", ok=False)
            raise InvalidInput("请说明为什么它是真实需求——人工确认必须留下理由")
        operator = (command.operator_ref or "").strip()
        if not operator:
            # 背书是授权例外，必须能追到具体的人；操作者留空会留下无人负责的背书（C21）。
            log_event(_COMPONENT, "review.source.attest_rejected", level="WARN",
                      item_ref=item.id, reason_code="operator_empty", ok=False)
            raise InvalidInput("人工确认必须记录操作者")

        replay = self._items.find_revision_by_idempotency(command.idempotency_key)
        if replay is not None:  # 幂等重放：同一次提交重发不重复记账
            log_event(_COMPONENT, "review.source.attest_replayed",
                      item_ref=item.id, operator_ref=operator, ok=True)
            return self._workspace_of_item(item.id)

        # 状态类拒绝用 RejectedTransition(409)：与本文件既有口径一致（区别于填错入参的
        # InvalidInput/400），让客户端能分辨「你填错了」与「状态变了，刷新后可能就能做」（C18）。
        if item.status != IS.PENDING_CONFIRMATION.value:
            log_event(_COMPONENT, "review.source.attest_rejected", level="WARN",
                      item_ref=item.id, reason_code="not_supplement_pending", ok=False)
            raise RejectedTransition("只有待补充来源的条目可以人工确认来源")
        gaps = self._open_supplement_gaps(item.id)
        if not gaps:
            # 不在「待补充来源」态就没有缺口可闭合。此时放行只会凭空多一条背书记录，
            # 让条目看上去"有人背书过"却没有对应的缺口事实。
            log_event(_COMPONENT, "review.source.attest_rejected", level="WARN",
                      item_ref=item.id, reason_code="no_open_gap", ok=False)
            raise RejectedTransition("这个条目当前没有未闭合的来源缺口，不需要人工确认")
        if self._project_attestation(self._items.revisions_of(item.id)) is not None:
            # 二次人工确认闭合不了任何东西（T20260721-attested-diagnosis-context 走查发现）。
            # 人工确认承认的是「材料里没写这条需求」，出处缺口在第一次就已经闭合；此后 AI 再判
            # 「建议补充来源」，缺的必定是格式/字段/阈值这类**具体值**，而人工确认一个值都不提供。
            # 放行只会让用户在「确认→重诊→又说缺→再确认」里绕圈：每一圈都闭合一次已经闭合的
            # 东西，真正缺的值一次也没补上。出路是把口径写进表达（人工修订）或登记真实来源。
            log_event(_COMPONENT, "review.source.attest_rejected", level="WARN",
                      item_ref=item.id, reason_code="already_attested", ok=False)
            raise RejectedTransition(
                "这条已经人工确认过来源了。本轮缺的是具体口径（格式、字段、阈值这类），"
                "人工确认提供不了这些值——请直接把口径写进条目表达，或登记真实来源。"
            )

        record_ref = self._items.record_item_revision(
            item_ref=item.id,
            field_key=_SOURCE_ATTESTATION_FIELD,
            before_value="",
            after_value=_SOURCE_ATTESTATION_VALUE,
            revision_mode=ItemRevisionMode.MANUAL.value,
            suggestion_ref=None,
            reason=reason,
            operator_ref=operator,
            idempotency_key=command.idempotency_key,
        )
        self._reviews.invalidate_rounds_of_item(item.id, _SOURCE_ATTESTATION_INVALIDATE_REASON)
        log_event(
            _COMPONENT, "review.source.attested", item_ref=item.id,
            record_ref=record_ref, gap_count=len(gaps), operator_ref=operator, ok=True,
        )
        return self._workspace_of_item(item.id)

    def _project_attestation(
        self, revisions: list[ItemRevisionRow],
    ) -> Optional[SourceAttestationRead]:
        """取最新一条背书记录（revisions_of 按时间倒序，取首个命中即最新）。"""
        row = next((r for r in revisions if r.field_key == _SOURCE_ATTESTATION_FIELD), None)
        if row is None:
            return None
        return SourceAttestationRead(
            record_ref=row.id, reason=row.reason or "",
            operator_ref=row.operator_ref, at=row.at,
        )

    def _project_veto(self, v: FindingVetoRow) -> FindingVetoRead:
        return FindingVetoRead(
            veto_ref=v.id, item_ref=v.item_ref, finding_type=v.finding_type,
            rule_code=v.rule_code, evidence_span=v.evidence_span,
            finding_summary=v.finding_summary, reason=v.reason,
            operator_ref=v.operator_ref, at=v.created_at,
            revoked=v.revoked_at is not None, revoked_at=v.revoked_at,
        )

    def _thread_context_of(self, item_ref: str) -> str:
        """近段对话摘要（用户消息拼接，供重评/增量诊断参考）。"""
        rows = self._model_results.stage_payloads_of(_STAGE_EXPLAIN, [item_ref])
        messages: list[str] = []
        for row in rows[-3:]:
            try:
                body = json.loads(row.payload or "{}")
            except ValueError:
                continue
            if body.get("user_message"):
                messages.append(str(body["user_message"]))
        return "；".join(messages)

    # ------------------------------------------------------------------
    # 编排回调：结论承接（结构校验 + 聚合守卫 + 可合成性 → 写 LDM-009）
    # ------------------------------------------------------------------

    def accept_item_diagnosis_result(
        self, batch_ref: str, item_ref: str, model_result_ref: str
    ) -> None:
        round_ = self._reviews.running_round_of(batch_ref, item_ref)
        if round_ is None:
            raise RejectedTransition("该条目没有进行中的诊断轮次，结果不可承接")
        result = self._model_results.read_stage_payload(model_result_ref)
        if result is None:
            raise RejectedTransition("诊断类 LDM-015 不存在")

        if result.result_code == "diagnosis_failed":
            # 分关原因（LDM-015 result_content.failure）→ 用户可读文案（白话，不再是三合一笼统句）
            try:
                failure = (json.loads(result.payload) if result.payload else {}).get("failure") or {}
            except ValueError:
                failure = {}
            stage = str(failure.get("stage") or "")
            detail = str(failure.get("detail") or result.basis or "诊断失败")
            stage_label = _FAILURE_STAGE_LABELS.get(stage)
            prefix = f"AI 诊断未完成（{stage_label}）：" if stage_label else "AI 诊断未完成："
            # 「已自动重试」只对分关落账的新式记录如实声明（failure 非空=经 adapter 重试后落账）；
            # 旧格式/外部生产者的失败行不得虚构重试事实（不造假纪律）。
            retry_note = "。已自动重试仍未成功，" if failure else "。"
            self._reviews.finish_round(
                round_.id, DPS.FAILED.value, model_result_ref=model_result_ref,
                reason=prefix + detail + retry_note + "可重新诊断或人工处理，不伪造结论",
            )
            log_event(_COMPONENT, "review.item.diagnosis_failed", level="WARN",
                      item_ref=item_ref, batch_ref=batch_ref, failure_stage=stage, ok=False)
            return

        item = self._items.get_item(item_ref)
        body = json.loads(result.payload) if result.payload else {}
        verdict = body.get("verdict") or {}
        error = self._verdict_guard(verdict, item.expression if item else "")
        if error is not None:
            # 聚合守卫（服务端确定性校验）：整轮不承接，不伪造结论
            self._reviews.finish_round(
                round_.id, DPS.FAILED.value, model_result_ref=model_result_ref,
                reason=f"模型结果未通过结论守卫（{error}）；可重试诊断或人工处理",
            )
            log_event(_COMPONENT, "review.item.result_unacceptable", level="WARN",
                      item_ref=item_ref, batch_ref=batch_ref, reject_reason=error, ok=False)
            return

        # 证据发现项（只读证据行，无人工复核字段）。
        # 逐条接住新行引用：规则编号/严重度/维度/原文高亮存在轮次 quality_meta 里，
        # 过去靠「写入序＝读出序」用下标配回，而同事务写入的这批行 created_at 全相同
        # （数据库 now() 同事务内同值），读侧 (created_at, id) 排序遂退化为随机 UUID 序，
        # 配对随即错位（REQ-101 实证）。改为按引用配对，顺序怎么变都不影响。
        finding_refs: list[str] = []
        for entry in verdict.get("findings") or []:
            finding_refs.append(self._reviews.add_finding(
                round_.id, item_ref, str(entry.get("finding_type")),
                str(entry.get("diagnosis_summary") or "").strip(),
                str(entry.get("basis_summary") or "").strip(),
                "none", None, None, None, None, model_result_ref,
            ))
        points = verdict.get("revision_points") or []
        gaps = [str(g) for g in (verdict.get("supplement_gaps") or [])]
        quality_meta_json = _build_quality_meta(verdict, finding_refs)  # v2 旁路元数据（降级不拒收）
        self._reviews.set_round_verdict(
            round_.id, str(verdict.get("verdict_kind")),
            str(verdict.get("verdict_summary") or "").strip(),
            json.dumps(points, ensure_ascii=False) if points else None,
            json.dumps(gaps, ensure_ascii=False) if gaps else None,
            quality_meta_json,
        )
        self._reviews.finish_round(
            round_.id, DPS.COMPLETED.value, model_result_ref=model_result_ref,
        )
        # 对话重评改判：替代旧站立结论（旧卡收折为"已替代"）
        if round_.trigger == DiagnosisTrigger.DIALOGUE_REEVAL.value:
            for old in self._rounds_of_item(item_ref):
                if (old.id != round_.id and old.verdict_kind
                        and old.adjudication_decision is None and not old.superseded_by):
                    self._reviews.supersede_round(old.id, round_.id)
                    if old.model_result_ref:
                        self._model_results.record_adoption(
                            model_result_ref=old.model_result_ref,
                            project_ref=round_.project_ref, stage="item_diagnosis",
                            subject_type="review_round", subject_ref=old.id,
                            outcome="superseded", operator_ref="system",
                            idempotency_key=f"supersede:{old.id}:{round_.id}",
                        )
        log_event(_COMPONENT, "review.item.verdict_minted", item_ref=item_ref,
                  batch_ref=batch_ref, verdict_kind=str(verdict.get("verdict_kind")), ok=True)

    def _verdict_guard(self, verdict: dict, base_expression: str) -> Optional[str]:
        """聚合一致性守卫（与适配器同规则；服务端裁定为准）。返回错误说明；None=通过。"""
        kind = str(verdict.get("verdict_kind") or "")
        summary = str(verdict.get("verdict_summary") or "").strip()
        findings = verdict.get("findings") or []
        points = verdict.get("revision_points") or []
        gaps = verdict.get("supplement_gaps") or []
        if kind not in _VERDICT_KINDS or not summary:
            return "缺少结论状态字或总结"
        if not findings or len(findings) > 6:
            return "证据发现项缺失或过多"
        for f in findings:
            if not isinstance(f, dict) or str(f.get("finding_type")) not in _FINDING_TYPES \
                    or not str(f.get("diagnosis_summary") or "").strip():
                return "证据发现项结构不完整"
        if kind == "revise" and not points:
            return "建议修订必须携带修订点"
        if kind != "revise" and points:
            return "非修订结论不得携带修订点"
        if kind == "pass" and any(str(f.get("finding_type")) != "no_blocker" for f in findings):
            return "建议通过与阻断证据冲突"
        if kind == "supplement" and not gaps:
            return "建议补充来源必须给出缺口清单"
        if kind != "supplement" and gaps:
            return "非补充结论不得携带缺口清单"
        if points:
            for i, p in enumerate(points):
                if not isinstance(p, dict):
                    return "修订点结构不完整"
                try:
                    fi = int(p.get("finding_index"))
                except (TypeError, ValueError):
                    return "修订点未绑定证据"
                if not (0 <= fi < len(findings)):
                    return "修订点绑定的证据不存在"
            err = validate_points(base_expression, [dict(p) for p in points])
            if err:
                return err
        return None

    # ------------------------------------------------------------------
    # AEP-034：结论裁决（v5 重定义；采纳副作用链原子执行）
    # ------------------------------------------------------------------

    def adjudicate_verdict(self, command: VerdictAdjudicationCommand) -> ItemReviewWorkspaceRead:
        replay = self._reviews.find_adjudication_by_idempotency(command.idempotency_key)
        if replay is not None:  # 幂等重放：返回当前工作区
            return self._workspace_of_item(command.item_ref)

        item = self._items.get_item(command.item_ref)
        if item is None:
            raise NotFound("需求条目不存在")
        parse_context = self._source_assets.parse_context_of(item.parse_result_ref)
        current = str(self._process_records.read_workspace_version(parse_context)) if parse_context else command.workspace_version
        if command.workspace_version != current:
            raise RejectedTransition("工作区已更新（版本不一致），请刷新后重试")

        round_ = self._reviews.latest_round_of_item(command.item_ref)
        if round_ is None or round_.id != command.round_ref:
            raise RejectedTransition("该结论不是当前有效结论，请刷新后重试")
        if not self._is_standing_verdict(item, round_):
            raise RejectedTransition("该结论已失效、已被替代或已被裁决，不能重复裁决")

        reason = (command.reason or "").strip() or None
        if command.decision is VerdictDecision.REJECTED and not reason:
            raise InvalidInput("拒绝结论必须给出理由（回复正文即理由）")

        kind = VerdictKind(round_.verdict_kind)
        points = json.loads(round_.revision_points or "[]")

        if command.decision is VerdictDecision.REJECTED:
            self._reviews.record_adjudication(
                round_.id, VerdictDecision.REJECTED.value, None, None,
                reason, command.operator_ref, command.idempotency_key,
            )
            self._record_verdict_adoption(round_, "rejected", command.operator_ref, command.idempotency_key)
            log_event(_COMPONENT, "review.verdict.rejected", item_ref=item.id,
                      round_ref=round_.id, verdict_kind=kind.value, ok=True)
            return self._workspace_of_item(item.id)

        # ---- 采纳：按状态字原子执行副作用链 ----
        if kind is VerdictKind.PASS:
            self._confirm_via_verdict(item, round_, command)
        elif kind is VerdictKind.REVISE:
            self._adopt_revise(item, round_, points, command)
        elif kind is VerdictKind.WITHDRAW:
            self._terminate_item(
                item, reason=round_.verdict_summary or "采纳「建议撤回」结论",
                operator_ref=command.operator_ref, basis_round=round_,
            )
            self._reviews.record_adjudication(
                round_.id, VerdictDecision.ADOPTED.value, None, None,
                reason, command.operator_ref, command.idempotency_key,
            )
            self._record_verdict_adoption(round_, "adopted", command.operator_ref, command.idempotency_key)
        else:  # SUPPLEMENT：登记缺口（缺口未闭合阻断再诊断；闭合=修订/新轮次）
            self._reviews.record_adjudication(
                round_.id, VerdictDecision.ADOPTED.value, None, None,
                reason, command.operator_ref, command.idempotency_key,
            )
            self._record_verdict_adoption(round_, "adopted", command.operator_ref, command.idempotency_key)
            log_event(_COMPONENT, "review.verdict.supplement_registered", item_ref=item.id,
                      round_ref=round_.id, ok=True)
        return self._workspace_of_item(item.id)

    def _adopt_revise(
        self, item: ItemRow, round_: DiagnosisRoundRow, points: list[dict],
        command: VerdictAdjudicationCommand,
    ) -> None:
        """采纳「建议修订」：应用所选点（AEP-036）→ 旧结论随版本失效 → 链式增量诊断。"""
        if self.revision_applier is None:
            raise RejectedTransition("修订承接方未装配，无法采纳修订结论")
        all_refs = [str(p.get("point_ref")) for p in points]
        # 「不用用户处理的问题」所绑的改法：含被裁定为不是问题的，也含因人工确认降格的（K1）
        inactive = self._vetoed_point_refs(round_, points)
        # C5：不点名修订点时，默认采纳「全部未被排除」的点，而不是全部点。否则用户否决过
        # 任意一条带改法的问题后，凡是不传该字段的调用方（区5 斜杠命令采纳）都会把被否决点也带上、
        # 必然撞上下面的联动剔除后为空而失败。
        default_refs = [r for r in all_refs if r not in inactive]
        selected = command.selected_point_refs if command.selected_point_refs is not None else default_refs
        expanded = expand_selection(points, list(selected))
        if not expanded:
            # C12：文案不提「勾选」——界面上已没有复选框，只有「改」与「标为不是问题」两个出口。
            raise InvalidInput("没有选中任何改法；这一轮的建议都不想要，请用「拒绝」结束这一轮")
        # C4：联动组会把已被排除的点重新拉回选择集（同组整组入选）。这些点的改法不应用；
        # 联动组不可拆，故要剔就连同整组一起剔——而不是报错让用户去操作界面上已不存在的入口。
        dropped: set[str] = set()
        inactive_in = inactive & set(expanded)
        if inactive_in:
            by_ref = {str(p.get("point_ref")): p for p in points}
            drop_groups = {
                str(by_ref[r].get("group")) for r in inactive_in if by_ref[r].get("group")
            }
            dropped = {
                r for r in expanded
                if r in inactive_in
                or (by_ref[r].get("group") and str(by_ref[r].get("group")) in drop_groups)
            }
            expanded = [r for r in expanded if r not in dropped]
            if not expanded:
                raise InvalidInput(
                    "没有可采纳的改法：你选的改法所针对的问题都已经不用处理了"
                    "（被你标为「不是问题」，或因这条需求的来源已由人工确认而降为提示），"
                    "也可能是与这类问题同属一个必须整组采纳的联动组。"
                    "要采纳请先把对应问题恢复计入，否则请用「拒绝」结束这一轮"
                )
        # 采纳前逐条可编辑：用户改稿只换替换文本，定位片段 find 不动，因此多点独立合成的
        # 语义不受影响。AI 原案留在轮次 revision_points 列里原样不变，两者并存供留痕对照。
        # 被联动整组剔除掉的点，其改稿一并忽略（那些点本就不再应用），不当作错误——否则 C4 的整组
        # 剔除会把「同组另一个点携带的改稿」变成一条新的拒绝。用户真没勾选的点仍照常在 _point_edits 里拒。
        live_edits = {
            k: v for k, v in (command.point_edits or {}).items() if str(k) not in dropped
        }
        edits = self._point_edits(points, expanded, live_edits)
        applied = [
            {**p, "replace": edits[str(p.get("point_ref"))]}
            if str(p.get("point_ref")) in edits else p
            for p in points
        ]
        err = validate_points(item.expression, [dict(p) for p in applied])
        if err:  # 确定性守卫（与承接结论时同一函数），改稿同样过这一关
            raise InvalidInput(f"修改后的内容无法应用：{err}")
        composed = compose(item.expression, applied, expanded)
        excluded = [r for r in all_refs if r not in set(expanded)]
        point_labels = "、".join(
            str(p.get("label")) for p in points if str(p.get("point_ref")) in set(expanded)
        )
        # K_dispatch 原子性（issue #12）：裁决 + 采纳结局 stats 行必须与修订同一事务原子提交。
        # revision_applier 内部链式派发（增量诊断/结构体检）会 mid-use-case commit——若把
        # _record_verdict_adoption 放在其后，stats 行落在该 commit 之后，一旦故障即永久缺行、
        # 且 retry 被「adjudication_decision 已 ADOPTED」弹回不自愈。故先落裁决 + stats 行
        # （同一未提交事务），再交 revision_applier；其派发 commit 一并原子持久整个裁决单元。
        # outcome 只依赖 excluded（此刻已知），可先行判定。派发前抛出（版本冲突等）则请求层
        # 回滚，裁决 + stats 均未提交，条目可干净重试（A2）。
        self._reviews.record_adjudication(
            round_.id, VerdictDecision.ADOPTED.value,
            json.dumps(expanded, ensure_ascii=False),
            json.dumps(excluded, ensure_ascii=False) if excluded else None,
            command.reason, command.operator_ref, command.idempotency_key,
            point_edits_json=json.dumps(edits, ensure_ascii=False) if edits else None,
        )
        outcome = "adopted" if not excluded else "adopted_with_revision"
        self._record_verdict_adoption(round_, outcome, command.operator_ref, command.idempotency_key)
        result = self.revision_applier(ItemRevisionCommand(
            project_ref=item.project_ref, item_ref=item.id,
            workspace_version=command.workspace_version,
            revision_mode=ItemRevisionMode.MANUAL,
            field_key="expression", revised_value=composed,
            selected_point_refs=expanded,
            reason=f"采纳结论修订点（{point_labels}）",
            operator_ref=command.operator_ref,
            idempotency_key=f"{command.idempotency_key}:revision",
        ), origin="review_adoption")
        if getattr(result, "status", "") != "applied":
            raise RejectedTransition(getattr(result, "next_action", None) or "修订应用未被承接")
        # C46：edited＝有几条改法用的是用户改稿而非 AI 原案（只记条数，改稿文本不进日志）。
        # 本卡引入「实际应用的文本可能不是 AI 写的」这一新事实，日志里没有字段承载它时，
        # 事后无法判断某次修订到底照谁的写法落的库。
        log_event(_COMPONENT, "review.verdict.revise_adopted", item_ref=item.id,
                  round_ref=round_.id, selected=len(expanded), excluded=len(excluded),
                  edited=len(edits), ok=True)
        # 阶段策略解耦 P1：链式增量诊断迁回评审采纳动作——对象层不再经 on_revised 无差别触发，
        # 评审服务在此裁决采纳动作内显式续接。两前置：条目待确认∧存在用户发起诊断史
        # （均由 start_chained_incremental 内部把守）+「修订源自采纳」由身处本动作结构性满足。
        # 返回的三态回执此处不消费（adjudicate 回读工作区投影），其副作用即新增链式轮次。
        self.start_chained_incremental(
            item.id, result.revision_record_ref, command.operator_ref,
        )

    def _point_edits(
        self, points: list[dict], expanded: list[str], raw: Optional[dict],
    ) -> dict[str, str]:
        """校验并归整用户对修订点替换文本的改稿（{point_ref: 终稿}）。

        三条校验，都给白话拒绝理由：改稿必须指向本轮存在的点；必须是这次要采纳的点（不采纳
        的点不会被应用，允许携带改稿只会让用户以为改了却没生效）；不能是空文本。改成与条目
        原文一模一样也拒——那不是修改，用户多半是误操作。

        C11：首尾空白先剔掉再入账。判空用的是 strip 后的值，存的却曾是原值，粘贴或换行留下的
        前后空格会原样落进条目表达，而条目表达是 SRS 发布稿的原文。
        C12：拒绝理由不再提「勾选」——第 2 轮重设计已整体撤除复选框，界面上没有可勾的东西；
        改为指向现存的两个出口：换个写法，或者把这个问题标为不是问题。
        """
        if not raw:
            return {}
        by_ref = {str(p.get("point_ref")): p for p in points}
        chosen = set(expanded)
        edits: dict[str, str] = {}
        for ref, value in raw.items():
            key = str(ref)
            point = by_ref.get(key)
            if point is None:
                raise InvalidInput("修改的内容对应不上本轮的修订建议，请刷新后重试")
            label = point.get("label") or key
            if key not in chosen:
                raise InvalidInput(f"「{label}」这条改法这次没有被采纳，你写的内容不会生效")
            text = str(value).strip()
            if not text:
                raise InvalidInput(
                    f"「{label}」的内容是空的；填上你想要的写法，或者把这个问题标为不是问题"
                )
            if text == str(point.get("replace") or ""):
                continue  # 与 AI 原案一字不差＝没改，不必记账
            if text == str(point.get("find") or ""):
                raise InvalidInput(
                    f"「{label}」被改回了原文，等于没改；换个写法，或者把这个问题标为不是问题"
                )
            edits[key] = text
        return edits

    def _promote_supporting_basis(self, item_ref: str) -> None:
        """P7 §1.2：条目确认 → 其预建立支撑依据边（引用的业务知识）转有效。"""
        if self._trace_links is not None:
            self._trace_links.promote_pre_established_supporting_basis(item_ref)

    def _confirm_via_verdict(
        self, item: ItemRow, round_: DiagnosisRoundRow, command: VerdictAdjudicationCommand,
    ) -> None:
        """采纳「建议通过」= 确认（原 AEP-037 准入内联；在途草案放弃留痕）。"""
        nxt = item_transition(ItemState(item.status), ItemEvent.CONFIRM)
        assert nxt is ItemState.CONFIRMED
        self._reviews.record_adjudication(
            round_.id, VerdictDecision.ADOPTED.value, None, None,
            command.reason, command.operator_ref, command.idempotency_key,
        )
        abandoned = self._abandon_inflight_draft(item.id)
        self._items.set_item_status(item.id, IS.CONFIRMED.value)
        self._promote_supporting_basis(item.id)  # P7：预建立支撑依据边随确认转有效
        self._reviews.record_confirmation(
            round_.id, "confirmed",
            f"采纳「建议通过」结论：{round_.verdict_summary or ''}",
            command.operator_ref, f"{command.idempotency_key}:confirm",
        )
        parse_context = self._source_assets.parse_context_of(item.parse_result_ref)
        if parse_context is not None:
            self._process_records.bump_workspace_version(parse_context)
        self._record_verdict_adoption(round_, "adopted", command.operator_ref, command.idempotency_key)
        log_event(_COMPONENT, "item.status.transition", item_ref=item.id,
                  from_status=item.status, to_status=IS.CONFIRMED.value,
                  sm_event=ItemEvent.CONFIRM.value,
                  draft_abandoned=abandoned or "", ok=True)

    def _terminate_item(
        self, item: ItemRow, reason: str, operator_ref: str,
        basis_round: Optional[DiagnosisRoundRow],
    ) -> None:
        nxt = item_transition(ItemState(item.status), ItemEvent.TERMINATE)
        assert nxt is ItemState.TERMINATED
        self._items.set_item_status(item.id, IS.TERMINATED.value)
        parse_context = self._source_assets.parse_context_of(item.parse_result_ref)
        if parse_context is not None:
            self._process_records.bump_workspace_version(parse_context)
        log_event(_COMPONENT, "item.status.transition", item_ref=item.id,
                  from_status=IS.PENDING_CONFIRMATION.value, to_status=IS.TERMINATED.value,
                  sm_event=ItemEvent.TERMINATE.value, reject_reason=reason, ok=True)

    def _abandon_inflight_draft(self, item_ref: str) -> Optional[str]:
        """确认时放弃在途草案（回执留痕；草案本身零副作用）。返回被放弃的草案引用。"""
        draft = self._latest_inflight_draft(item_ref)
        if draft is None:
            return None
        if draft.get("suggestion_ref"):
            self._formation_process.set_suggestion_status(str(draft["suggestion_ref"]), "expired")
        log_event(_COMPONENT, "review.draft.abandoned", item_ref=item_ref,
                  draft_ref=str(draft.get("message_ref")), ok=True)
        return str(draft.get("message_ref"))

    def _record_verdict_adoption(
        self, round_: DiagnosisRoundRow, outcome: str, operator_ref: str, idempotency_key: str,
    ) -> None:
        """结论裁决明细（效能口径 §4：subject=review_round）。"""
        if not round_.model_result_ref:
            return
        self._model_results.record_adoption(
            model_result_ref=round_.model_result_ref, project_ref=round_.project_ref,
            stage="item_diagnosis", subject_type="review_round", subject_ref=round_.id,
            outcome=outcome, operator_ref=operator_ref,
            idempotency_key=f"{idempotency_key}:adoption:{round_.id}",
        )

    # ------------------------------------------------------------------
    # AEP-095：评审对话（解释 / 草案 / 轻量重评；领域零写入）
    # ------------------------------------------------------------------

    def review_dialogue(
        self, command: ReviewDialogueCommand,
        on_stage: Optional[Callable[[str], None]] = None,  # AiRequestStage 稳定码回调（SSE 流式链路回执）
    ) -> ReviewDialogueResult:
        def _stage(stage: AiRequestStage) -> None:
            if on_stage is not None:
                on_stage(stage.value)

        item = self._items.get_item(command.item_ref)
        if item is None:
            raise NotFound("需求条目不存在")
        message = (command.message or "").strip()
        if not message:
            raise InvalidInput("消息不能为空")
        if self._reviews.has_running_round(item.id):
            return ReviewDialogueResult(
                outcome_type=DialogueOutcomeType.EXPLANATION,
                explanation="诊断进行中，请等待新结论产出后再对话。",
                next_action="等待诊断收束",
            )

        # 斜杠预处理段：命令词确定性解析 → LLM 参数解释 → 校验派发（2026-07-06 扩展）
        try:
            chat_command, _ = resolve_command(ITEM_REVIEW_COMMANDS, message)
        except UnknownCommand as exc:
            words = "、".join(f"/{w}" for w in ITEM_REVIEW_COMMANDS)
            log_event(_COMPONENT, "dialogue.command.unknown", level="WARN",
                      item_ref=item.id, word=exc.word, ok=False)
            return ReviewDialogueResult(
                outcome_type=DialogueOutcomeType.COMMAND, command_word=exc.word,
                message=f"未知命令 /{exc.word}。可用命令：{words}；不带斜杠即自由对话。",
            )
        _stage(AiRequestStage.ACCEPTED)
        if chat_command is not None:
            return self._dialogue_command(item, chat_command, command, _stage)

        standing = self._standing_round_of(item)
        # 意图路由：修订动词→草案；疑问→解释；其余→轻量重评（改判唯一通道）
        # 三个生成型 lane 的模型调用统一映射「执行中」阶段
        _stage(AiRequestStage.RUNNING)
        if any(m in message for m in _DRAFT_MARKS):
            return self._dialogue_draft(item, message, command)
        if standing is None or any(m in message for m in _QUESTION_MARKS) or message.rstrip().endswith(("？", "?")):
            return self._dialogue_explain(item, standing, message, command)
        return self._dialogue_reeval(item, standing, message, command)

    def _dialogue_command(
        self, item: ItemRow, chat_command: ChatCommand, command: ReviewDialogueCommand,
        _stage: Callable[[AiRequestStage], None],
    ) -> ReviewDialogueResult:
        word = chat_command.word
        log_event(_COMPONENT, "dialogue.command.resolved", item_ref=item.id, command_word=word)
        if self._command_interpreter is None:
            raise InvalidInput("命令解释能力未装配")

        standing = self._standing_round_of(item)
        draft = self._latest_inflight_draft(item.id)
        points = json.loads(standing.revision_points or "[]") if standing else []
        context = {
            "item": {"item_ref": item.id, "req_no": item.req_no,
                     "expression": item.expression, "req_type": item.req_type,
                     "status": item.status},
            "standing_verdict": (
                {"round_ref": standing.id, "round_no": getattr(standing, "round_no", None),
                 "verdict_kind": standing.verdict_kind,
                 "verdict_summary": standing.verdict_summary,
                 "revision_points": [
                     {"ordinal": i + 1, "point_ref": p.get("point_ref"), "label": p.get("label")}
                     for i, p in enumerate(points)
                 ]}
                if standing else None
            ),
            "inflight_draft": (
                {"draft_seq": draft.get("draft_seq"), "suggestion_ref": draft.get("suggestion_ref")}
                if draft else None
            ),
            "selected_item_refs": command.selected_item_refs,
        }
        _stage(AiRequestStage.INTERPRETING)
        interpretation = self._command_interpreter.interpret(word, command.message, context)

        def _reply(message: str) -> ReviewDialogueResult:
            return ReviewDialogueResult(
                outcome_type=DialogueOutcomeType.COMMAND, command_word=word, message=message,
            )

        if interpretation.failed:
            log_event(_COMPONENT, "dialogue.interpret.completed", level="WARN",
                      item_ref=item.id, command_word=word, ok=False)
            return _reply("命令解释服务暂不可用，请稍后重试；结论卡一键裁决不受影响。")
        if interpretation.status in ("clarify", "cannot_comply"):
            log_event(_COMPONENT, "dialogue.interpret.refused", item_ref=item.id,
                      command_word=word, status=interpretation.status)
            return _reply(interpretation.reason or "请补充信息后重试。")
        operation = interpretation.operation
        if operation not in chat_command.operations:
            log_event(_COMPONENT, "dialogue.params.invalid", level="WARN",
                      item_ref=item.id, command_word=word, operation=operation, ok=False)
            return _reply("该命令不支持解释出的操作，请换个说法或换用对应命令。")
        log_event(_COMPONENT, "dialogue.interpret.completed", item_ref=item.id,
                  command_word=word, operation=operation, ok=True)
        _stage(AiRequestStage.DISPATCHING)

        try:
            result = self._dispatch_item_command(
                item, word, operation, dict(interpretation.params), standing, draft, command,
            )
        except (InvalidInput, RejectedTransition) as exc:
            log_event(_COMPONENT, "dialogue.dispatch.failed", level="WARN",
                      item_ref=item.id, command_word=word, operation=operation,
                      reason=str(exc), ok=False)
            return _reply(str(exc))
        log_event(_COMPONENT, "dialogue.dispatch.completed", item_ref=item.id,
                  command_word=word, operation=operation,
                  outcome=result.outcome_type.value, ok=True)
        return result

    def _dispatch_item_command(
        self, item: ItemRow, word: str, operation: str, params: dict,
        standing: Optional[DiagnosisRoundRow], draft: Optional[dict],
        command: ReviewDialogueCommand,
    ) -> ReviewDialogueResult:
        dispatch_key = f"{command.idempotency_key}:dispatch"
        label = _ITEM_DIALOGUE_OPERATION_LABELS.get(operation, operation)

        def _command_result(
            message: Optional[str], agent_run_ref: Optional[str] = None,
            next_action: Optional[str] = None,
        ) -> ReviewDialogueResult:
            return ReviewDialogueResult(
                outcome_type=DialogueOutcomeType.COMMAND, command_word=word,
                operation=operation, operation_label=label, params_echo=params,
                message=message, agent_run_ref=agent_run_ref, next_action=next_action,
            )

        if operation == "start_diagnosis":
            mode = str(params.get("diagnosis_mode") or DiagnosisMode.STANDARD.value)
            refs = (
                list(command.selected_item_refs)
                if params.get("scope") == "selected" and command.selected_item_refs
                else [item.id]
            )
            result = self.start_item_diagnosis(ItemReviewDiagnosisCommand(
                project_ref=command.project_ref, item_refs=refs, diagnosis_mode=mode,
                workspace_version=command.workspace_version,
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            ))
            if result.status != "submitted":
                return _command_result(result.next_action)
            return _command_result(
                f"已发起诊断（{len(refs)} 条），结论产出后进入待裁决。",
                agent_run_ref=result.agent_run_ref,
            )

        if operation in ("adjudicate_adopt", "adjudicate_reject"):
            if standing is None:
                return _command_result("当前条目没有待裁决的有效结论。")
            selected_point_refs = None
            ordinals = params.get("selected_point_ordinals") or []
            if operation == "adjudicate_adopt" and ordinals:
                points = json.loads(standing.revision_points or "[]")
                try:
                    selected_point_refs = [str(points[int(o) - 1]["point_ref"]) for o in ordinals]
                except (IndexError, KeyError, TypeError, ValueError):
                    return _command_result("点名的修订点序号不存在，请对照结论卡修订点重试。")
            decision = (
                VerdictDecision.ADOPTED if operation == "adjudicate_adopt" else VerdictDecision.REJECTED
            )
            self.adjudicate_verdict(VerdictAdjudicationCommand(
                project_ref=command.project_ref, item_ref=item.id, round_ref=standing.id,
                decision=decision, selected_point_refs=selected_point_refs,
                reason=str(params.get("reason") or "") or None,
                workspace_version=command.workspace_version,
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            ))
            verdict_text = _VERDICT_TEXT.get(standing.verdict_kind, standing.verdict_kind)
            done = "已采纳（副作用链已执行）" if decision is VerdictDecision.ADOPTED else "已拒绝（结论作废）"
            return _command_result(f"「{verdict_text}」结论{done}。")

        if operation == "adopt_draft":
            if draft is None or not draft.get("suggestion_ref"):
                return _command_result("当前条目没有在途修订草案。")
            if self.revision_applier is None:
                raise RejectedTransition("修订承接方未装配，无法采纳草案")
            result = self.revision_applier(ItemRevisionCommand(
                project_ref=command.project_ref, item_ref=item.id,
                workspace_version=command.workspace_version,
                revision_mode=ItemRevisionMode.ACCEPT_SUGGESTION, field_key="expression",
                suggestion_ref=str(draft["suggestion_ref"]),
                reason="采纳对话草案（斜杠命令）",
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            ))
            if getattr(result, "status", "") != "applied":
                return _command_result(getattr(result, "next_action", None) or "草案采纳未被承接。")
            # 回显服务端算好的真话（含链式诊断是否真被触发），不写死结论；agent_run_ref 供前端点灯
            return _command_result(
                getattr(result, "next_action", None) or "草案已采纳，修订已应用。",
                agent_run_ref=getattr(result, "agent_run_ref", None),
            )

        if operation == "manual_revision":
            new_expression = str(params.get("new_expression") or "").strip()
            if not new_expression:
                return _command_result("请写出「修订为：<完整表达>」，或只写修订方向由 AI 起草。")
            if self.revision_applier is None:
                raise RejectedTransition("修订承接方未装配，无法应用人工修订")
            result = self.revision_applier(ItemRevisionCommand(
                project_ref=command.project_ref, item_ref=item.id,
                workspace_version=command.workspace_version,
                revision_mode=ItemRevisionMode.MANUAL, field_key="expression",
                revised_value=new_expression, reason="人工修订（斜杠命令）",
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            ))
            if getattr(result, "status", "") != "applied":
                return _command_result(getattr(result, "next_action", None) or "修订应用未被承接。")
            # 回显服务端算好的真话（含链式诊断是否真被触发），不写死结论；agent_run_ref 供前端点灯
            return _command_result(
                getattr(result, "next_action", None) or "修订已应用。",
                agent_run_ref=getattr(result, "agent_run_ref", None),
            )

        if operation == "draft":
            result = self._dialogue_draft(item, str(params.get("instruction") or command.message), command)
            return result.model_copy(update={
                "command_word": word, "operation": operation,
                "operation_label": label, "params_echo": params,
            })

        if operation == "find_sources":
            result = self._dialogue_find_sources(item)
            return result.model_copy(update={
                "command_word": word, "operation": operation,
                "operation_label": label, "params_echo": params,
            })

        if operation == "override_confirm":
            result = self.confirm_item(ItemConfirmationCommand(
                project_ref=command.project_ref, item_ref=item.id,
                workspace_version=command.workspace_version, override=True,
                reason=str(params.get("reason") or ""),
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            ))
            return _command_result(result.next_action or "条目已覆盖确认。")

        if operation == "withdraw":
            result = self.withdraw_item(ItemWithdrawCommand(
                project_ref=command.project_ref, item_ref=item.id,
                workspace_version=command.workspace_version,
                reason=str(params.get("reason") or ""),
                operator_ref=command.operator_ref, idempotency_key=dispatch_key,
            ))
            return _command_result(getattr(result, "next_action", None) or "条目已撤回终止。")

        raise InvalidInput(f"不支持的对话操作：{operation}")

    def _dialogue_draft(
        self, item: ItemRow, message: str, command: ReviewDialogueCommand,
    ) -> ReviewDialogueResult:
        current = self._latest_inflight_draft(item.id)
        current_value = str(current.get("proposed_value") or "") if current else None
        outcome = self._draft_composer.compose(
            {"item_ref": item.id, "req_no": item.req_no, "expression": item.expression,
             "req_type": item.req_type},
            self._sources_brief(item), message, current_value,
            # 本页暂不注入自己的诊断上下文（形成页注入的是结构体检结果；评审页该注入什么
            # 超出本次范围，留待另行裁定）。显式传空而不是靠默认值，让这个选择在代码里看得见。
            None,
        )
        if outcome.failed:
            return ReviewDialogueResult(
                outcome_type=DialogueOutcomeType.EXPLANATION,
                explanation="草案起草服务不可用，请稍后重试或使用人工修订。",
                next_action="重试或人工修订",
            )
        if not outcome.proposed_value:
            # cannot_comply 拒绝通道：模型判断该意图无法起草为修订草案，原因直接回给用户
            return ReviewDialogueResult(
                outcome_type=DialogueOutcomeType.EXPLANATION,
                explanation=outcome.reason or "AI 判断该意图无法起草为修订草案。",
                next_action="调整修订意图后重试，或使用人工修订",
            )
        # 稿次按本页草案递增。这里必须安全取值：在途稿可能来自条目形成页（跨页续稿是刻意
        # 保留的行为），而形成页写入的载荷里没有稿次这一项，直接取键会抛错——用户在形成页
        # 起草但不采纳、再到评审页说一句修订意图，接口就 500（2026-07-20 走查调查发现，
        # 本卡顺手修，用户拍板：缺稿次按 0 算，新稿即第 1 稿，序号语义不变）。
        seq = (int(current.get("draft_seq") or 0) + 1) if current else 1
        if current and current.get("suggestion_ref"):
            # 新稿替代旧稿：旧候选过期（原位迭代，稿次链留痕）
            self._formation_process.set_suggestion_status(str(current["suggestion_ref"]), "expired")
        message_ref = self._model_results.record_stage_payload(
            _STAGE_DRAFT, item.id, "drafted",
            json.dumps({
                "item_ref": item.id, "draft_seq": seq, "proposed_value": outcome.proposed_value,
                "note": outcome.note, "user_message": message, "at": _now_iso(),
                # 来源页面：两页共用同一个阶段键，载荷不标来源则读侧无从区分（见 _project_dialogue）
                "origin": _ORIGIN_REVIEW,
            }, ensure_ascii=False),
            "对话修订草案（未采纳零副作用）",
        )
        suggestion_ref = self._formation_process.save_suggestion(
            item.id, "expression", outcome.proposed_value,
            f"对话草案 D{seq}（由用户意见起草）", message_ref,
        )
        draft = DialogueMessageRead(
            message_ref=message_ref, kind=DialogueOutcomeType.DRAFT,
            user_message=message, draft_value=outcome.proposed_value,
            draft_note=outcome.note or None, draft_seq=seq,
            suggestion_ref=suggestion_ref, in_flight=True, created_at=_now_iso(),
        )
        log_event(_COMPONENT, "review.dialogue.drafted", item_ref=item.id,
                  draft_seq=seq, ok=True)
        # 采纳后是否真会增量重诊取决于本条是否有用户诊断史——按守卫谓词分叉，不预先许诺
        adopt_hint = (
            "采纳草案将应用修订并自动增量重诊"
            if self._has_user_initiated_diagnosis(item.id)
            else "采纳草案将应用修订；本条尚无诊断记录，需另行发起首次诊断"
        )
        return ReviewDialogueResult(
            outcome_type=DialogueOutcomeType.DRAFT, draft=draft,
            next_action=f"继续说可原位迭代草案；{adopt_hint}；不采纳零副作用",
        )

    def _source_candidate_pool(self, item: ItemRow) -> list[dict]:
        """候选来源差集：同批次已确认、未链接到本条目、未被替代的要素（各带原文引文）。

        排除四类（issue #30 / ADR-0002 P3 候选口径）：已链接（在 source_element_refs 内）、
        待确认（process_status≠confirmed）、已撤销（process_status=revoked）、异批次
        （elements_of 按本条 parse_result_ref 取，跨批要素天然不在其中）；额外排除 superseded
        旧版本要素（与评审读路径同口径）。候选只从此集里选，禁 LLM 自拟。
        """
        linked = set(json.loads(item.source_element_refs or "[]"))
        pool: list[dict] = []
        for element in self._source_assets.elements_of(item.parse_result_ref):
            if element.superseded:
                continue
            if element.process_status != ElementProcessStatus.CONFIRMED.value:
                continue
            if element.id in linked:
                continue
            pool.append({
                "id": element.id, "element_type": element.element_type,
                "content": element.content,
                "source_quote": first_anchor_quote(element.source_anchor),
            })
        return pool

    def _dialogue_find_sources(self, item: ItemRow) -> ReviewDialogueResult:
        """/找来源：在候选差集中检索候选来源并按相关度排序（issue #30 出口三部曲之二）。

        空池确定性拒绝（不调模型）；lane 失败=基础设施不可用；lane cannot_comply 或无
        落在差集内的候选=如实回原因（诚实性优先，不凑数）；成功=COMMAND 回执 + 候选载荷。
        本卡只到「给候选」，登记动作由前端后续卡接线。
        """
        pool = self._source_candidate_pool(item)
        if not pool:
            log_event(_COMPONENT, "review.find_sources.empty_pool", item_ref=item.id, ok=True)
            return ReviewDialogueResult(
                outcome_type=DialogueOutcomeType.COMMAND,
                message="当前批次没有可作候选来源的要素（同批次已确认、且尚未链接到本条的要素为空）。"
                        "可改用「撤回」终止本条，或回需求分析补入新材料后重新识别。",
                next_action="撤回本条，或回需求分析补入材料",
            )
        outcome = self._source_candidate_composer.find(
            {"item_ref": item.id, "req_no": item.req_no, "expression": item.expression,
             "req_type": item.req_type},
            pool,
        )
        if outcome.failed:
            log_event(_COMPONENT, "review.find_sources.failed", level="WARN",
                      item_ref=item.id, ok=False)
            return ReviewDialogueResult(
                outcome_type=DialogueOutcomeType.COMMAND,
                message="找来源服务暂不可用，请稍后重试。",
                next_action="稍后重试",
            )
        if not outcome.candidates:
            # cannot_comply：候选要素在语义上都与本条不是同一件事，如实告知（ADR-0002 §2.2 诚实性优先）
            log_event(_COMPONENT, "review.find_sources.cannot_comply", item_ref=item.id, ok=True)
            return ReviewDialogueResult(
                outcome_type=DialogueOutcomeType.COMMAND,
                message=outcome.reason or "AI 在给定要素中未找到贴切的候选来源。",
                next_action="撤回本条，或回需求分析补入更贴切的材料",
            )
        by_id = {c["id"]: c for c in pool}
        # 候选必在差集内（与 composer 层 element_id ⊆ pool 的不变式一致）：
        # 未命中即显式丢弃，不产出字段全空的壳候选
        candidates = [
            SourceCandidateRead(
                element_ref=c["element_id"],
                element_type=str(by_id[c["element_id"]].get("element_type") or ""),
                content=str(by_id[c["element_id"]].get("content") or ""),
                source_quote=by_id[c["element_id"]].get("source_quote"),
                reason=str(c.get("reason") or ""),
                rank=int(c.get("rank") or 0),
            )
            for c in outcome.candidates
            if c["element_id"] in by_id
        ]
        log_event(_COMPONENT, "review.find_sources.done", item_ref=item.id,
                  candidate_count=len(candidates), ok=True)
        return ReviewDialogueResult(
            outcome_type=DialogueOutcomeType.COMMAND,
            source_candidates=candidates,
            message=f"为本条找到 {len(candidates)} 条候选来源（按相关度排序，附推荐理由）。",
            next_action="核对候选后登记为本条来源，或撤回本条",
        )

    def _dialogue_explain(
        self, item: ItemRow, standing: Optional[DiagnosisRoundRow], message: str,
        command: ReviewDialogueCommand,
    ) -> ReviewDialogueResult:
        context = self._verdict_context(standing) if standing else {"verdict_summary": "当前无有效结论"}
        text = self._explainer.explain(
            {"item_ref": item.id, "expression": item.expression}, context, message,
        )
        if not text:
            return ReviewDialogueResult(
                outcome_type=DialogueOutcomeType.EXPLANATION,
                explanation="解释服务不可用，请稍后重试。",
            )
        self._model_results.record_stage_payload(
            _STAGE_EXPLAIN, item.id, "explained",
            json.dumps({"item_ref": item.id, "user_message": message, "explanation": text,
                        "at": _now_iso()}, ensure_ascii=False),
            "评审解释（不改结论）",
        )
        return ReviewDialogueResult(
            outcome_type=DialogueOutcomeType.EXPLANATION, explanation=text,
            next_action="解释不改变结论；若你认为判定不成立，直接给出反驳理由可触发重评",
        )

    def _dialogue_reeval(
        self, item: ItemRow, standing: DiagnosisRoundRow, message: str,
        command: ReviewDialogueCommand,
    ) -> ReviewDialogueResult:
        outcome = self._reeval_responder.reeval(
            {"item_ref": item.id, "expression": item.expression, "req_type": item.req_type},
            self._verdict_context(standing), message,
            self._excluded_points_of(item.id), self._thread_context_of(item.id),
        )
        if outcome.failed:
            return ReviewDialogueResult(
                outcome_type=DialogueOutcomeType.EXPLANATION,
                explanation="重评服务不可用或结果不可承接，结论维持不变。",
            )
        if outcome.action == "maintain":
            self._model_results.record_stage_payload(
                _STAGE_EXPLAIN, item.id, "explained",
                json.dumps({"item_ref": item.id, "user_message": message,
                            "explanation": outcome.explanation, "at": _now_iso()}, ensure_ascii=False),
                "轻量重评：维持结论",
            )
            return ReviewDialogueResult(
                outcome_type=DialogueOutcomeType.EXPLANATION, explanation=outcome.explanation,
                next_action="结论维持；不同意可继续给出依据，或使用覆盖确认/拒绝结论",
            )
        # supersede：改判必经轮次——铸新轮次承载新结论（trigger=dialogue_reeval）
        parse_context = self._source_assets.parse_context_of(item.parse_result_ref)
        version = str(self._process_records.read_workspace_version(parse_context)) if parse_context else "1"
        batch_ref = self._reviews.create_batch(
            item.project_ref, parse_context or "", item.parse_result_ref,
            item.formation_context_ref, json.dumps([item.id]),
            standing.diagnosis_mode, command.operator_ref,
            f"reeval:{command.idempotency_key}",
        )
        round_ref = self._reviews.create_round(
            item.project_ref, item.id, batch_ref, standing.diagnosis_mode,
            "轻量重评：以用户对话意见为上下文重新判定。",
            trigger=DiagnosisTrigger.DIALOGUE_REEVAL.value,
        )
        verdict = outcome.verdict
        model_result_ref = self._model_results.record_stage_payload(
            "item_diagnosis", batch_ref, "diagnosed",
            json.dumps({"item_ref": item.id, "verdict": {
                "verdict_kind": verdict.verdict_kind,
                "verdict_summary": verdict.verdict_summary,
                # 与常规诊断同一序列化：必须带上 rule_code/evidence_span/severity/dimension，
                # 否则本轮 quality_meta 为空、已登记的否决在改判后的新结论上永不命中，用户被困
                # （GitHub issue #55 第 2 条 / 冷审查 C3）。
                "findings": [serialize_diagnosed_finding(f) for f in verdict.findings],
                "revision_points": list(verdict.revision_points),
                "supplement_gaps": list(verdict.supplement_gaps),
            }, "reeval_explanation": outcome.explanation, "at": _now_iso()}, ensure_ascii=False),
            "轻量重评改判",
        )
        self.accept_item_diagnosis_result(batch_ref, item.id, model_result_ref)
        log_event(_COMPONENT, "review.dialogue.reevaluated", item_ref=item.id,
                  old_round=standing.id, new_round=round_ref, ok=True)
        return ReviewDialogueResult(
            outcome_type=DialogueOutcomeType.REEVAL,
            explanation=outcome.explanation, agent_run_ref=batch_ref,
            next_action="已改判：新结论卡已产出并替代旧结论（旧卡收折为已替代）",
        )

    def _sources_brief(self, item: ItemRow) -> list[dict]:
        sources: list[dict] = []
        for ref in json.loads(item.source_element_refs or "[]"):
            element = self._source_assets.get_element(ref)
            if element is not None:
                sources.append({"id": element.id, "content": element.content})
        return sources

    def _verdict_context(self, round_: DiagnosisRoundRow) -> dict:
        """站立结论的证据上下文：解释通道与轻量重评通道共用。

        带上人工确认与降格标记（2026-07-25 冷审查 K4 消费）。此前这里只序列化结论状态字、
        摘要、发现项与修订点，两条通道因此都看不见「这条需求的来源已由人工确认」这个事实：
        - 轻量重评是第二条会铸出正式诊断轮次的路径，模型不知道有过人工确认，可以重新判
          「建议补充来源」并带缺口，用户一采纳，条目就回到「待补充来源」——而人工确认按
          「不重复确认」的准入已经拒绝第二次确认，这条循环的逃生口正是本卡关掉的；
        - 解释通道会把一条已降格的发现项当阻断问题向用户解释。

        无背书条目返回的字典与改动前逐字段相同（不新增键），提示词渲染因此不变。
        """
        rows = self._reviews.findings_of_round(round_.id)
        attestation = self._project_attestation(self._items.revisions_of(round_.item_ref))
        attested_refs: set[str] = set()
        if attestation is not None:
            attested_refs = self._mark_vetoes(
                round_, rows, self._reviews.vetoes_of_item(round_.item_ref), attested=True,
            ).attested_source_refs
        findings: list[dict] = []
        for f in rows:
            entry = {
                "finding_type": f.finding_type, "diagnosis_summary": f.diagnosis_summary,
                "basis_summary": f.basis_summary,
            }
            if attestation is not None:
                # 降格标记只在确有背书时出现：无背书条目的上下文形状一个字都不变
                entry["source_attested"] = str(f.id) in attested_refs
            findings.append(entry)
        context = {
            "round_ref": round_.id,
            "verdict_kind": round_.verdict_kind,
            "verdict_summary": round_.verdict_summary,
            "findings": findings,
            "revision_points": json.loads(round_.revision_points or "[]"),
        }
        if attestation is not None:
            # 适配器把这一项取出来单独渲染成提示词里的条件区块（与 item_diagnosis 同形态），
            # 不让它混在结论 JSON 里——同一事实以两种格式重复入上下文是本卡已经处置过的病。
            context["attestation"] = {
                "reason": attestation.reason,
                "operator_ref": attestation.operator_ref,
                "at": attestation.at,
            }
        return context

    # ------------------------------------------------------------------
    # 确认写入（覆盖确认直写）与人工撤回（需求条目服务面；本服务承接留痕）
    # ------------------------------------------------------------------

    def _veto_cleared_round(self, item: ItemRow) -> Optional[DiagnosisRoundRow]:
        """站立结论是「建议修订」，且它报的阻断问题此刻一条待处理的都不剩。

        这是「直接确认」通道的开门条件（AEP-116 层③）。服务端在确认时**重新核算一遍**，
        不采信前端传来的判断；判据是纯字符串比对（见 _veto_match），可离线复算。

        2026-07-25 冷审查 K5 消费：开门条件由 all_blocking_vetoed 放宽到 blocking_cleared，
        即「被用户逐条裁定」与「因人工确认降格」都算数。此前一轮里的阻断项**全部**被降格时，
        降格后的阻断总数归零、all_blocking_vetoed 恒为 False，通道消失——而界面上此刻一条
        待处理的问题都没有，用户只剩覆盖确认可走，那要填理由、要打覆盖标记进效能统计，
        与「问题都已消解」不是一回事，账目会失真。留痕措辞另由 confirm_item 分口径处理。
        """
        standing = self._standing_round_of(item)
        if standing is None or standing.verdict_kind != VerdictKind.REVISE.value:
            return None
        return standing if self._marks_of_round(standing).blocking_cleared else None

    def confirm_item(self, command: ItemConfirmationCommand) -> ItemConfirmationResult:
        """确认条目的两条直写通道：覆盖确认（override=True，理由必填），以及否决消解后的
        直接确认（AEP-116：站立的「建议修订」结论所报问题已被用户逐条裁定为不是问题）。

        采纳「建议通过」的常规确认仍走 AEP-034，不经本端点。
        """
        replay = self._reviews.find_confirmation_by_idempotency(command.idempotency_key)
        if replay is not None:
            item = self._items.get_item(command.item_ref)
            return ItemConfirmationResult(
                status="confirmed", item_ref=command.item_ref,
                item_status=IS(item.status) if item else IS.CONFIRMED,
                next_action="条目已确认（幂等重放）",
            )
        item = self._items.get_item(command.item_ref)
        if item is None or item.project_ref != command.project_ref:
            raise NotFound("需求条目不存在")  # C38：条目必须属于命令里的项目
        # C9：诊断进行中这一条要排在最前面。它排在后面时，正在跑的那一轮不满足「已收束」，
        # _veto_cleared_round 返回 None，用户在下面那条早退里拿到的是一段解释端点分工的
        # 开发者语言，读不出「等一下就好」。三条拒绝理由里这条最可行动，故先判。
        if self._reviews.has_running_round(item.id):
            return ItemConfirmationResult(
                status="rejected_precheck", item_ref=item.id, item_status=IS(item.status),
                next_action="诊断进行中，不能确认；请等待本轮结束",
            )
        cleared_round = None if command.override else self._veto_cleared_round(item)
        if not command.override and cleared_round is None:
            return ItemConfirmationResult(
                status="rejected_precheck", item_ref=item.id, item_status=IS(item.status),
                next_action="确认经由采纳「建议通过」结论完成（AEP-034）；"
                            "覆盖确认与「问题已被逐条裁定为非问题」可直写本端点",
            )
        reason = (command.reason or "").strip()
        if command.override and not reason:
            raise InvalidInput("覆盖确认必须填写理由")
        parse_context = self._source_assets.parse_context_of(item.parse_result_ref)
        current = str(self._process_records.read_workspace_version(parse_context)) if parse_context else command.workspace_version
        if command.workspace_version != current:
            return ItemConfirmationResult(
                status="rejected_precheck", item_ref=item.id, item_status=IS(item.status),
                next_action="工作区已更新（版本不一致），请刷新后重试",
            )
        nxt = item_transition(ItemState(item.status), ItemEvent.CONFIRM)
        assert nxt is ItemState.CONFIRMED
        # P2：否决消解通道下复用已取到的站立轮，不二次读——两次 _standing_round_of 之间隔着版本读取
        # 与 has_running_round，READ COMMITTED 下并发提交可见，二次读理论上可能落到另一轮。
        standing = cleared_round or self._standing_round_of(item)
        # 两条通道的留痕分开：覆盖确认是「用户推翻了 AI 的判断」，要打覆盖标记进效能统计；
        # 否决消解确认是「用户逐条裁定后没有问题剩下」，结论同样按拒绝收口（AI 建议确实没被采纳，
        # 如实记账），但不打覆盖标记——它不是覆盖。
        # C1 次生：条数取「命中阻断问题的去重否决行数」＝用户实际裁定过的条数。此前取命中
        # 的发现项条数，一条否决命中多条时账面会大于界面上出现过的问题数。
        cleared_marks = self._marks_of_round(cleared_round) if cleared_round else None
        vetoed_count = cleared_marks.blocking_veto_count if cleared_marks else 0
        # 只数「降格且用户没裁定过」的那些：同一条发现项既被用户裁定又被降格时，两句话
        # 各报一遍会读成两条问题，而界面上只有一条（计数须与用户可见输入自洽）。
        attested_only = (
            len(cleared_marks.attested_source_refs - set(cleared_marks.by_finding_ref))
            if cleared_marks else 0
        )
        # 留痕如实分口径（K5/K10(c)）：「已被逐条裁定（N 条）」只由用户真实点过的裁定驱动，
        # 人工确认降格不许顶开它——否则一条都没裁定过的条目会留下「已被逐条裁定（0 条）」。
        # 两件事都发生过就两句都写，一件都没有不该走到这条通道（blocking_cleared 的前提）。
        if command.override:
            collapse_reason = f"覆盖确认：{reason}"
        else:
            if vetoed_count and attested_only:
                collapse_reason = (
                    f"本轮建议已被逐条裁定为不是问题（{vetoed_count} 条）；"
                    f"另有 {attested_only} 条因来源已由人工确认而降为提示"
                )
            elif vetoed_count:
                collapse_reason = f"本轮建议已被逐条裁定为不是问题（{vetoed_count} 条）"
            elif attested_only:
                collapse_reason = (
                    f"本轮已无需要你处理的问题（因来源已由人工确认而降为提示 {attested_only} 条）"
                )
            else:
                collapse_reason = "本轮已无需要你处理的问题"
            if reason:
                collapse_reason += f"；确认说明：{reason}"
        if standing is not None:
            self._reviews.record_adjudication(
                standing.id, VerdictDecision.REJECTED.value, None, None,
                collapse_reason, command.operator_ref,
                f"{command.idempotency_key}:override",
            )
            if command.override:
                self._reviews.mark_round_overridden(standing.id)
            self._record_verdict_adoption(standing, "rejected", command.operator_ref, command.idempotency_key)
        self._abandon_inflight_draft(item.id)
        self._items.set_item_status(item.id, IS.CONFIRMED.value)
        self._promote_supporting_basis(item.id)  # P7：预建立支撑依据边随确认转有效
        basis_round = standing or self._reviews.latest_round_of_item(item.id)
        if basis_round is not None:
            self._reviews.record_confirmation(
                basis_round.id, "confirmed",
                f"覆盖确认（理由：{reason}）" if command.override
                else f"确认（{collapse_reason}）",
                command.operator_ref, command.idempotency_key,
            )
        if parse_context is not None:
            self._process_records.bump_workspace_version(parse_context)
        log_event(_COMPONENT, "item.status.transition", item_ref=item.id,
                  from_status=IS.PENDING_CONFIRMATION.value, to_status=IS.CONFIRMED.value,
                  sm_event=ItemEvent.CONFIRM.value, override=command.override,
                  veto_cleared=cleared_round is not None, ok=True)
        return ItemConfirmationResult(
            status="confirmed", item_ref=item.id, item_status=IS.CONFIRMED,
            next_action="条目已覆盖确认（理由与被覆盖结论已留痕）" if command.override
            else "条目已确认（你标为不是问题的那些建议已随结论一并留痕）" if vetoed_count
            else "条目已确认（本轮结论与它报过的问题已随确认一并留痕）",
        )

    def withdraw_item(self, command: ItemWithdrawCommand) -> ItemWithdrawResult:
        item = self._items.get_item(command.item_ref)
        if item is None or item.project_ref != command.project_ref:
            raise NotFound("需求条目不存在")  # C38：条目必须属于命令里的项目
        reason = (command.reason or "").strip()
        if not reason:
            raise InvalidInput("撤回必须填写理由")
        if item.status == IS.TERMINATED.value:
            return ItemWithdrawResult(
                status="terminated", item_ref=item.id, item_status=IS.TERMINATED,
                next_action="条目已终止（幂等重放）",
            )
        self._terminate_item(item, reason=reason, operator_ref=command.operator_ref, basis_round=None)
        return ItemWithdrawResult(
            status="terminated", item_ref=item.id, item_status=IS.TERMINATED,
            next_action="条目已撤回终止（理由留痕）",
        )

    # ------------------------------------------------------------------
    # AEP-033：评审工作区读视图（线程/会话条/动态流三投影的素材）
    # ------------------------------------------------------------------

    def _reconcile_stale_diagnosis_rounds(self, parse_result_ref: str) -> None:
        """读侧自愈（HK-2，仿 docx `_reconcile_stale_exports`）：悬轮联查 AgentRun 收尸。

        diagnosing 轮次的批次 run 已失败 / 缺失 / 超判死阈值 → 轮次落 FAILED＋通知，
        `has_running_round` 单飞守卫随之解锁。发现悬轮才顺手收尸，不引定时器；
        在飞未超龄的轮次不动（防误杀）。仅落脱敏归因文案（硬规则 8）。
        """
        session = getattr(self._reviews, "session", None)
        if session is None:  # 非 SQL 装配（测试替身）无从联查 run，跳过
            return
        from app.repositories.agent_run import SqlAgentRunRepository
        from app.services.notification import notify_agent_run_lost

        agent_runs = SqlAgentRunRepository(session)
        now = datetime.now(timezone.utc)
        for batch in self._reviews.batches_of_parse_result(parse_result_ref):
            stuck = [r for r in self._reviews.rounds_of_batch(batch.id)
                     if r.processing_status == DPS.DIAGNOSING.value]
            if not stuck:
                continue
            run = agent_runs.find_by_context(batch.id, "item_diagnosis")
            verdict = dead_run_verdict(_DIAGNOSIS_LANE, run, now=now)
            if verdict is None:  # run 在飞未超龄（或已收束成功）：不收尸
                continue
            if verdict == "run_stale":
                # 僵尸 run 判死：run 落 failed（repo 内联动既有 AI 任务失败通知，防静默）
                agent_runs.mark_failed(str(run.id), "执行进程失联，读侧对账判死（HK-2）")
            elif verdict == "run_missing":
                notify_agent_run_lost(session, "item_diagnosis", batch.id)
            for r in stuck:
                self._reviews.finish_round(
                    r.id, DPS.FAILED.value,
                    reason="执行进程失联，已自动对账；可重新发起诊断",
                )
                log_event(_COMPONENT, "review.diagnosis.round_reconciled", level="WARN",
                          ok=False, round_ref=r.id, batch_ref=batch.id,
                          run_id=str(run.id) if run is not None else None,
                          verdict=verdict)

    def read_item_review_workspace(self, review_context_ref: str) -> ItemReviewWorkspaceRead:
        req = self._formation_process.get_formation_request(review_context_ref)
        if req is None:
            raise NotFound("评审工作区不存在（需先完成条目形成批次）")

        try:  # HK-2 读侧自愈：对账失败只记 WARN，不得阻塞读主流程
            self._reconcile_stale_diagnosis_rounds(req.parse_result_ref)
        except Exception as exc:  # noqa: BLE001
            log_event(_COMPONENT, "review.diagnosis.reconcile_error", level="WARN",
                      ok=False, error_code=type(exc).__name__)

        version = str(self._process_records.read_workspace_version(req.parse_context_ref))
        canvas = build_material_canvas(self._process_records, self._source_assets, req.parse_context_ref)
        source_elements = [
            project_element(row)
            for row in self._source_assets.elements_of(req.parse_result_ref)
            if not row.superseded
        ]

        items = self._items.items_of_parse_result(req.parse_result_ref)
        review_items = [self._project_review_item(item) for item in items]

        runs: list[DiagnosisRunProgressRead] = []
        # 契约守卫（issue #10 B2a ④）：批次收束后**不清除**，作为已完成 run 持续可查——
        # run 级进度与 failed_count 均派生自本处保留的批次事实，删批次即无声破坏该契约。
        # 失败按 run 直接归因：failed_count=本批 processing_status∈_FAILED_ROUND_STATUSES 的轮次数，
        # 与条目全局最新态解耦（结算窗口内被新批重诊/跨批遗留失败均不影响本 run 计数）。
        for batch in self._reviews.batches_of_parse_result(req.parse_result_ref):
            rounds = self._reviews.rounds_of_batch(batch.id)
            terminal = [r for r in rounds if r.processing_status != DPS.DIAGNOSING.value]
            failed = sum(1 for r in rounds if r.processing_status in _FAILED_ROUND_STATUSES)
            running = len(terminal) < len(rounds)
            runs.append(DiagnosisRunProgressRead(
                run_ref=batch.id,
                item_refs=list(json.loads(batch.item_refs or "[]")),
                diagnosis_mode=DiagnosisMode(batch.diagnosis_mode),
                status="running" if running else "completed",
                completed_count=len(terminal),
                total_count=len(rounds),
                failed_count=failed,
                next_action="诊断进行中，逐条目实时返回" if running else "诊断批次已收束",
            ))

        selectable = any(i.review_status is RIS.NO_VERDICT for i in review_items)
        any_running = any(run.status == "running" for run in runs)
        awaiting = sum(1 for i in review_items if i.review_status is RIS.AWAITING_ADJUDICATION)
        confirmed = sum(1 for i in review_items if i.review_status is RIS.CONFIRMED)
        next_action = None
        if any_running:
            next_action = "诊断进行中：单条目结论实时入流，可先裁决已产出的结论"
        elif awaiting:
            next_action = f"有 {awaiting} 个条目的结论待你裁决（采纳或拒绝）"
        elif selectable:
            next_action = "勾选可诊断条目后发起诊断"
        elif review_items:
            next_action = "本阶段条目已收束，可返回维护视图"

        return ItemReviewWorkspaceRead(
            review_context_ref=review_context_ref,
            formation_context_ref=review_context_ref,
            workspace_version=version,
            material_canvas=canvas,
            source_elements=source_elements,
            review_items=review_items,
            diagnosis_options=list(DiagnosisMode),
            diagnosis_runs=runs,
            available_operations=[
                ActionFact(key="start_diagnosis", enabled=selectable and not any_running,
                           disabled_reason=None if selectable and not any_running
                           else ("诊断进行中" if any_running else "没有可诊断的条目")),
                ActionFact(key="refresh_review_view", enabled=True),
                ActionFact(key="back_to_maintenance", enabled=True),
            ],
            confirmed_count=confirmed,
            total_count=len(review_items),
            next_action=next_action,
        )

    def _workspace_of_item(self, item_ref: str) -> ItemReviewWorkspaceRead:
        item = self._items.get_item(item_ref)
        if item is None:
            raise NotFound("需求条目不存在")
        return self.read_item_review_workspace(item.formation_context_ref)

    # ---- 逐条目读视图派生（派生显示态 + 结论卡/收折历史/对话消息）----

    def _is_standing_verdict(self, item: ItemRow, round_: DiagnosisRoundRow) -> bool:
        """站立结论谓词：已收束产出结论 ∧ 未失效 ∧ 未被替代 ∧ 未被裁决 ∧ 条目待确认。"""
        return (
            item.status == IS.PENDING_CONFIRMATION.value
            and round_.processing_status == DPS.COMPLETED.value
            and bool(round_.verdict_kind)
            and not round_.invalidated
            and not round_.superseded_by
            and round_.adjudication_decision is None
        )

    def _standing_round_of(self, item: ItemRow) -> Optional[DiagnosisRoundRow]:
        round_ = self._reviews.latest_round_of_item(item.id)
        if round_ is not None and self._is_standing_verdict(item, round_):
            return round_
        return None

    def _open_supplement_gaps(self, item_ref: str) -> list[str]:
        """未闭合来源缺口：最新轮次为已采纳的 supplement 结论且未失效。"""
        round_ = self._reviews.latest_round_of_item(item_ref)
        if (
            round_ is not None
            and round_.verdict_kind == VerdictKind.SUPPLEMENT.value
            and round_.adjudication_decision == VerdictDecision.ADOPTED.value
            and not round_.invalidated
        ):
            return list(json.loads(round_.supplement_gaps or "[]"))
        return []

    def _derive_display(
        self, item: ItemRow, rounds: list[DiagnosisRoundRow],
        gaps: Optional[list[str]] = None, veto_cleared: bool = False,
        cleared_by_veto: bool = False,
    ) -> tuple[RIS, str]:
        if item.status == IS.CONFIRMED.value:
            return RIS.CONFIRMED, "条目已确认。"
        if item.status == IS.TERMINATED.value:
            return RIS.TERMINATED, "条目已终止。"
        latest = rounds[0] if rounds else None
        if latest is None:
            return RIS.NO_VERDICT, "尚未形成当前版本有效结论。"
        if latest.processing_status == DPS.DIAGNOSING.value:
            note = "增量诊断进行中（修订后自动发起）。" if latest.trigger == DiagnosisTrigger.REVISION_CHAINED.value else "诊断进行中。"
            return RIS.DIAGNOSING, note
        if self._is_standing_verdict(item, latest):
            if veto_cleared:
                # 显示态仍是「待裁决」（不新增封闭集成员），只改说明句：结论还在，
                # 但它报的问题此刻一条都不用用户处理，所以下一步不是改表达而是确认。
                # 两句分开是因为原因不同，说错就是对用户说假话（K5）：一条都没裁定过的
                # 用户读到「你都标成了不是问题」会以为自己做过一件没做过的事。
                return RIS.AWAITING_ADJUDICATION, (
                    "本轮提的问题你都标成了不是问题，可以直接确认这个条目。"
                    if cleared_by_veto
                    else "本轮提的问题都不用你处理了（来源已由人工确认），可以直接确认这个条目。"
                )
            kind = _VERDICT_TEXT.get(latest.verdict_kind or "", latest.verdict_kind or "")
            return RIS.AWAITING_ADJUDICATION, f"当前结论：{kind}，待你裁决。"
        gaps = self._open_supplement_gaps(item.id) if gaps is None else gaps
        if gaps:
            return RIS.NO_VERDICT, f"来源缺口未闭合（{len(gaps)} 项），补充来源或修订表达后可再诊断。"
        if latest.invalidated:
            return RIS.NO_VERDICT, latest.invalidated_reason or "旧结论随修订失效。"
        if latest.adjudication_decision == VerdictDecision.REJECTED.value:
            return RIS.NO_VERDICT, "结论已被拒绝作废；可重新诊断、人工修订、覆盖确认或撤回。"
        if latest.processing_status in _FAILED_ROUND_STATUSES:
            return RIS.NO_VERDICT, latest.reason or "诊断未完成，可重试。"
        return RIS.NO_VERDICT, "尚未形成当前版本有效结论。"

    def _derive_display_code(
        self, item: ItemRow, rounds: list[DiagnosisRoundRow],
        display: RIS, status_note: str,
        gaps: Optional[list[str]] = None,
    ) -> tuple[RDC, str]:
        """用户可见显示态封闭集 + 说明句单点（issue #10 B2a）。

        把 `_derive_display` 的粗粒 NO_VERDICT 按最近轮次事实细分为待诊断/诊断失败/
        结论已拒绝/待补充来源，分支语义逐条对齐前端 deriveReviewDisplay（状态机文档 §3 口径），
        使「进行过诊断的条目永不回到待诊断」成为后端单点；B2b 消费后前端派生退役。
        非 NO_VERDICT 四态：码=显示态本身，说明句沿用 _derive_display 单点（含待裁决说明句，
        统一区1/区5 未来同源）。
        """
        if display is not RIS.NO_VERDICT:
            return RDC(display.value), status_note
        latest = rounds[0] if rounds else None
        # 失效优先（与 _derive_display 同序）：修订使旧轮失效后，失败/被拒事实属旧版本作用域，
        # 不得再以陈旧标签呈现——失效瞬态归待诊断＋已修订说明句。
        if latest is not None and not latest.invalidated:
            if latest.processing_status in _FAILED_ROUND_STATUSES:
                streak = 0
                for r in rounds:  # 当前版本作用域内最新连续失败轮数（失效轮/非失败轮即断）
                    if r.processing_status not in _FAILED_ROUND_STATUSES or r.invalidated:
                        break
                    streak += 1
                note = (
                    f"诊断已连续失败 {streak} 次（原因见对话线程），可重试或改人工处理。"
                    if streak > 1
                    else "最近一次诊断未完成（原因见对话线程），可重试或改人工处理。"
                )
                return RDC.DIAGNOSIS_FAILED, note
            if latest.adjudication_decision == VerdictDecision.REJECTED.value:
                return RDC.VERDICT_REJECTED, "上一轮结论已被拒绝，可重新诊断、人工修订、覆盖确认或撤回。"
        gaps = self._open_supplement_gaps(item.id) if gaps is None else gaps
        if gaps:
            return RDC.SUPPLEMENT_PENDING, "来源缺口未闭合，补充来源或修订表达后可再诊断。"
        # 待诊断到达路径副语：人工确认 / 已修订失效 / 有过非失效旧轮 / 从未诊断四分。
        # 人工确认单列一支，是因为它一个字都没改条目——说成「条目已修订」就是假话。
        # 判据见 _attestation_just_closed_gap（与前端醒目横幅共用同一个）。
        note = (
            "来源缺口已由人工确认闭合（材料未记载该需求）；可重新诊断。"
            if self._attestation_just_closed_gap(rounds)
            else "条目已修订，旧结论已失效；可重新诊断。" if latest is not None and latest.invalidated
            else "可发起诊断。" if latest is not None
            else "尚未发起过诊断。"
        )
        return RDC.PENDING_DIAGNOSIS, note

    def _attestation_just_closed_gap(self, rounds: list[DiagnosisRoundRow]) -> bool:
        """本次「旧结论失效」正是人工确认造成的（＝刚刚闭合了来源缺口）。

        判据不是「这个条目背书过」：背书是粘性事实，背书之后的任何一次普通内容修订也会让
        最新轮失效，只看「背书过」会把那次普通修订误说成来源缺口刚闭合（C5）。故比对最新
        失效轮的失效理由是否等于背书路径写入的那句常量。

        说明句与前端的醒目横幅共用这一个判据：说明句取哪一句、横幅显不显示，问的本就是
        同一个问题，各自派生迟早会对不上。
        """
        latest = rounds[0] if rounds else None
        return bool(
            latest is not None and latest.invalidated
            and latest.invalidated_reason == _SOURCE_ATTESTATION_INVALIDATE_REASON
        )

    def _project_round(
        self, round_: DiagnosisRoundRow, effective: bool,
        vetoes: Optional[list[FindingVetoRow]] = None,
        attested: Optional[bool] = None,
    ) -> VerdictRead:
        # "failed" 桶与 _FAILED_ROUND_STATUSES 单一来源；diagnosing→running，completed→completed
        status = "running" if round_.processing_status == DPS.DIAGNOSING.value else (
            "failed" if round_.processing_status in _FAILED_ROUND_STATUSES else "completed"
        )
        adjudication = None
        if round_.adjudication_decision is not None:
            adjudication = VerdictAdjudicationRead(
                decision=VerdictDecision(round_.adjudication_decision),
                selected_point_refs=list(json.loads(round_.adjudication_selected_points or "[]")),
                excluded_point_refs=list(json.loads(round_.excluded_point_refs or "[]")),
                point_edits=dict(json.loads(round_.adjudication_point_edits or "{}")),
                reason=round_.adjudication_reason,
                operator_ref=round_.adjudication_operator or "",
                at=round_.adjudicated_at or "",
            )
        # v2 质量元数据（旁路，降级不呈现）。配对口径：新轮次的每条元数据带 finding_ref，
        # 按引用查表配回；存量轮次没有引用，退回原来的下标配对维持现状（不猜）。
        # 不能只按下标：同事务写入的发现项 created_at 全相同，读侧 (created_at, id) 排序
        # 退化为随机 UUID 序，与写入序无关（REQ-101 实证）。
        quality = json.loads(round_.quality_meta) if round_.quality_meta else {}
        fmeta = quality.get("findings") or []
        meta_by_ref = {str(m.get("finding_ref")): m for m in fmeta if m.get("finding_ref")}
        rows = self._reviews.findings_of_round(round_.id)

        def _meta_of(index: int, finding_ref: str) -> dict:
            """该发现项的质量元数据：优先按引用，存量轮次回退下标，都没有则空。"""
            hit = meta_by_ref.get(str(finding_ref))
            if hit is not None:
                return hit
            return fmeta[index] if not meta_by_ref and index < len(fmeta) else {}

        # 修订点绑定：写入的 finding_index 是模型输出序，与读出序不是一回事。
        # 经元数据把它翻译成发现项引用输出，前端据此配对（存量轮次翻不出来就留空，
        # 前端回退下标，与改前行为一致）。
        def _point_finding_ref(index: int) -> Optional[str]:
            if 0 <= index < len(fmeta):
                ref = fmeta[index].get("finding_ref")
                return str(ref) if ref else None
            return None

        # 问题否决（AEP-116）：读时现算，随撤销自动恢复计入（不落库的理由见 _VetoMarks）
        marks = self._mark_vetoes(
            round_, rows,
            self._reviews.vetoes_of_item(round_.item_ref) if vetoes is None else vetoes,
            # 背书事实由调用方给（工作区投影逐条目只查一次修订记录）；未给时自取，
            # 与否决集合同样的口径，不让调用方各自派生。
            attested=self._is_attested(round_.item_ref) if attested is None else attested,
        )

        def _point_vetoed(point: dict, finding_ref: Optional[str]) -> bool:
            """该点所针对的问题是否已被裁定为不是问题（存量轮次无引用时回退下标）。"""
            if finding_ref is not None:
                return finding_ref in marks.by_finding_ref
            return int(point.get("finding_index") or 0) in marks.by_meta_index

        return VerdictRead(
            round_ref=round_.id,
            round_no=round_.round_no,
            batch_ref=round_.batch_ref,
            item_ref=round_.item_ref,
            diagnosis_mode=DiagnosisMode(round_.diagnosis_mode),
            trigger=DiagnosisTrigger(round_.trigger),
            status=status,
            verdict_kind=VerdictKind(round_.verdict_kind) if round_.verdict_kind else None,
            verdict_summary=round_.verdict_summary,
            findings=[
                ReviewFindingRead(
                    finding_ref=f.id, finding_type=f.finding_type,
                    diagnosis_summary=f.diagnosis_summary, basis_summary=f.basis_summary,
                    rule_code=_meta_of(i, f.id).get("rule_code"),
                    dimension=_meta_of(i, f.id).get("dimension"),
                    severity=_meta_of(i, f.id).get("severity") or "medium",
                    evidence_span=_meta_of(i, f.id).get("evidence_span"),
                    vetoed=f.id in marks.by_finding_ref,
                    veto_ref=(marks.by_finding_ref[f.id].id if f.id in marks.by_finding_ref else None),
                    veto_reason=(
                        marks.by_finding_ref[f.id].reason if f.id in marks.by_finding_ref else None
                    ),
                    can_veto=f.id in marks.fingerprintable,
                    source_attested=f.id in marks.attested_source_refs,
                )
                for i, f in enumerate(rows)
            ],
            revision_points=[
                RevisionPointRead(
                    point_ref=str(p.get("point_ref")), label=str(p.get("label") or ""),
                    finding_index=int(p.get("finding_index") or 0),
                    finding_ref=_point_finding_ref(int(p.get("finding_index") or 0)),
                    find=str(p.get("find") or ""), replace=str(p.get("replace") or ""),
                    basis=str(p.get("basis") or ""), group=p.get("group"),
                    vetoed=_point_vetoed(p, _point_finding_ref(int(p.get("finding_index") or 0))),
                )
                for p in json.loads(round_.revision_points or "[]")
            ],
            supplement_gaps=list(json.loads(round_.supplement_gaps or "[]")),
            context_coverage=round_.context_coverage,
            model_result_refs=[round_.model_result_ref] if round_.model_result_ref else [],
            invalidated=round_.invalidated,
            invalidated_reason=round_.invalidated_reason,
            superseded_by=round_.superseded_by,
            adjudication=adjudication,
            overridden=round_.overridden,
            confirm_result=round_.confirm_result,
            effective=effective,
            reason=round_.reason,
            created_at=round_.created_at,
            quality_profile=quality.get("quality_profile"),
            ears_rewrite=quality.get("ears_rewrite"),
            blocking_finding_count=marks.blocking_open,
            all_blocking_findings_vetoed=marks.all_blocking_vetoed,
            blocking_findings_cleared=marks.blocking_cleared,
        )

    # ------------------------------------------------------------------
    # AEP-105：条目质量投影（v2 详情卡「质量诊断」页签数据源）
    # ------------------------------------------------------------------

    def read_item_quality(self, project_ref: str, item_ref: str) -> ItemQualityRead:
        """最新一轮诊断的质量投影（评分/发现项 span/EARS/逐源对齐分）；无诊断 → 空投影不伪造。"""
        item = self._items.get_item(item_ref)
        if item is None or item.project_ref != project_ref:
            raise NotFound("需求条目不存在")
        base = item.expression or ""
        rounds = self._rounds_of_item(item_ref)
        latest = rounds[0] if rounds else None
        if latest is None or latest.invalidated or not latest.verdict_kind:
            return ItemQualityRead(item_ref=item.id, req_no=item.req_no,
                                   base_expression=base, has_diagnosis=False)
        verdict = self._project_round(
            latest, effective=self._is_standing_verdict(item, latest),
            attested=self._is_attested(item.id),
        )
        return ItemQualityRead(
            item_ref=item.id, req_no=item.req_no, base_expression=base, has_diagnosis=True,
            round_ref=latest.id, round_no=latest.round_no, status=verdict.status,
            verdict_kind=verdict.verdict_kind, verdict_summary=verdict.verdict_summary,
            quality_profile=verdict.quality_profile, findings=verdict.findings,
            revision_points=verdict.revision_points, ears_rewrite=verdict.ears_rewrite,
            source_alignments=self._project_source_alignments(item, latest),
        )

    def _project_source_alignments(
        self, item: ItemRow, round_: DiagnosisRoundRow,
    ) -> list[SourceAlignmentRead]:
        """逐源对齐分：LLM alignment（quality_meta）+ 来源要素 anchor/wing + source_drift 派生 drift。"""
        quality = json.loads(round_.quality_meta) if round_.quality_meta else {}
        by_ref = {str(a.get("element_ref")): a for a in (quality.get("source_alignments") or [])}
        base = item.expression or ""
        out: list[SourceAlignmentRead] = []
        for ref in json.loads(item.source_element_refs or "[]"):
            element = self._source_assets.get_element(ref)
            if element is None:
                continue
            anchor = first_anchor_quote(element.source_anchor)
            a = by_ref.get(ref) or {}
            alignment = a.get("alignment")
            drift_tokens = _drift_tokens(base, element.content or "")
            out.append(SourceAlignmentRead(
                element_ref=ref,
                wing=knowledge_category_of(element.element_type),
                anchor=anchor,
                alignment=float(alignment) if alignment is not None else None,
                drift=bool(drift_tokens),
                drift_tokens=drift_tokens,
                note=(str(a.get("note")) if a.get("note") else None),
            ))
        return out

    def _latest_inflight_draft(self, item_ref: str) -> Optional[dict]:
        drafts = self._dialogue_payloads(item_ref, _STAGE_DRAFT)
        for body in reversed(drafts):
            suggestion_ref = body.get("suggestion_ref")
            if suggestion_ref:
                suggestion = self._formation_process.get_suggestion(str(suggestion_ref))
                if suggestion is not None and suggestion.status == "candidate":
                    return body
        return None

    def _dialogue_payloads(self, item_ref: str, stage: str) -> list[dict]:
        """该条目的对话类 LDM-015 载荷（旧→新；附 message_ref/suggestion_ref 解析）。"""
        rows = self._model_results.stage_payloads_of(stage, [item_ref])
        out: list[dict] = []
        for row in rows:
            try:
                body = json.loads(row.payload or "{}")
            except ValueError:
                continue
            body["message_ref"] = row.ref
            out.append(body)
        out.sort(key=lambda b: str(b.get("at") or ""))
        if stage == _STAGE_DRAFT:
            # 草案候选建议按 model_result_ref 反查（suggestion_ref 存于建议行）
            suggestions = self._formation_process.suggestions_of_items([item_ref])
            by_result = {s.model_result_ref: s for s in suggestions if s.model_result_ref}
            for body in out:
                s = by_result.get(str(body.get("message_ref")))
                if s is not None:
                    body["suggestion_ref"] = s.id
                    body["suggestion_status"] = s.status
        return out

    def _project_dialogue(self, item_ref: str) -> list[DialogueMessageRead]:
        messages: list[DialogueMessageRead] = []
        for body in self._dialogue_payloads(item_ref, _STAGE_EXPLAIN):
            messages.append(DialogueMessageRead(
                message_ref=str(body.get("message_ref")),
                kind=DialogueOutcomeType.EXPLANATION,
                user_message=str(body.get("user_message") or ""),
                text=str(body.get("explanation") or ""),
                created_at=str(body.get("at") or ""),
            ))
        for body in self._dialogue_payloads(item_ref, _STAGE_DRAFT):
            origin = str(body.get("origin") or "") or None
            in_flight = body.get("suggestion_status") == "candidate"
            # 形成页留下的历史交换不进本页对话——那是在另一页发生的对话，混进来读着像
            # 自己说过的话。唯一例外是仍在途的候选建议：跨页续稿是刻意保留的行为，用户
            # 可以在本页采纳它，所以它要显示，但必须标明来源。存量载荷没有 origin，
            # 无从判断来源，一律维持原有显示，不猜。
            if origin == _ORIGIN_FORMATION and not in_flight:
                continue
            messages.append(DialogueMessageRead(
                message_ref=str(body.get("message_ref")),
                kind=DialogueOutcomeType.DRAFT,
                user_message=str(body.get("user_message") or ""),
                draft_value=str(body.get("proposed_value") or ""),
                draft_note=str(body.get("note") or "") or None,
                draft_seq=int(body.get("draft_seq") or 1),
                suggestion_ref=str(body.get("suggestion_ref")) if body.get("suggestion_ref") else None,
                in_flight=in_flight,
                origin=origin,
                created_at=str(body.get("at") or ""),
            ))
        messages.sort(key=lambda m: m.created_at)
        return messages

    def _project_review_item(self, item: ItemRow) -> ReviewRequirementItemRead:
        rounds = self._rounds_of_item(item.id)
        gaps = self._open_supplement_gaps(item.id)  # 单次查询，供两级派生与投影共用（审查 O1）
        revisions = self._items.revisions_of(item.id)  # 单次查询，供修订记录与背书投影共用
        attestation = self._project_attestation(revisions)
        standing = self._standing_round_of(item)
        # 否决集合按条目取一次，逐轮投影共用（避免每轮一次查询）
        vetoes = self._reviews.vetoes_of_item(item.id)
        attested = attestation is not None  # 已查过修订记录，逐轮投影直接复用，不再各查一次
        current_verdict = (
            self._project_round(standing, effective=True, vetoes=vetoes, attested=attested)
            if standing else None
        )
        history = [
            self._project_round(r, effective=False, vetoes=vetoes, attested=attested)
            for r in rounds
            if standing is None or r.id != standing.id
        ]
        # 站立结论是「建议修订」，但它报的阻断问题此刻一条待处理的都不剩（K5：被用户逐条
        # 裁定，或因人工确认降格，两者都算）。cleared_by_veto 单独留一份，供说明句选措辞——
        # 通道开不开与「因为什么开的」是两个问题，合成一个布尔量就必然有一句话说错。
        cleared = bool(
            current_verdict is not None
            and current_verdict.verdict_kind is VerdictKind.REVISE
            and current_verdict.blocking_findings_cleared
        )
        cleared_by_veto = bool(
            current_verdict is not None and current_verdict.all_blocking_findings_vetoed
        )
        veto_cleared = cleared
        display, note = self._derive_display(item, rounds, gaps, cleared, cleared_by_veto)
        display_code, display_note = self._derive_display_code(
            item, rounds, display, note, gaps,
        )
        # 「照 AI 建议改了几次仍没通过」——只是给用户看的事实，不再是停发自动复诊的理由
        # （2026-07-20 熔断废除）。次数多说明这条大概率不是靠改表达能解决的，是否继续由用户定。
        adopted_revise_rounds = self._reviews.count_adopted_revise_rounds(item.id)

        pending = item.status == IS.PENDING_CONFIRMATION.value
        diagnosing = display is RIS.DIAGNOSING
        can_adjudicate = standing is not None
        can_dialogue = pending and not diagnosing
        can_manual = pending and not diagnosing
        can_diagnose = display is RIS.NO_VERDICT and not gaps
        can_override = pending and not diagnosing
        can_withdraw = pending and not diagnosing
        # 否决消解后的确认通道：与覆盖确认共用同一端点，但不是覆盖——用户已逐条裁定过，
        # 没有剩下任何成立的阻断问题。确认门禁的其余判据（诊断中/条目待确认）照旧把关。
        can_confirm_cleared = veto_cleared and pending and not diagnosing
        # 人工确认的可用性（与 attest_source 的准入同一套判据；界面读 affordance 不自算门禁）：
        # 待确认 + 有未闭合缺口 + 尚未确认过。第三条是走查加的——见 attest_source 的说明。
        # 理由按判据逐条落到不成立的那一条上（C14(a)：禁用理由说错就是对用户说假话）。
        # 顺序要紧：先说不在该状态/没有缺口，最后才说「已经确认过」——一个已确认且此刻没有
        # 缺口的条目，真正的原因是没有缺口可闭合，不是「本轮缺的是具体口径」。
        can_attest = pending and bool(gaps) and attestation is None
        if can_attest:
            attest_reason = None
        elif not pending:
            attest_reason = "条目不在待确认状态"
        elif not gaps:
            attest_reason = "这个条目当前没有未闭合的来源缺口"
        else:
            attest_reason = ("这条已经人工确认过来源了；本轮缺的是具体口径，"
                             "人工确认提供不了这些值")
        # C14(a)：禁用理由要说清楚是三条判据里的哪一条不成立。此前无论哪一条不成立都统一
        # 回「本轮还有你没处理的问题」——一个从未诊断过的条目也会拿到这句话，既没有「本轮」
        # 也没有任何「问题」。本仓约定禁用 title 直接渲染服务端理由，说错就是对用户说假话。
        if can_confirm_cleared:
            confirm_cleared_reason = None
        elif not pending:
            confirm_cleared_reason = "条目不在待确认状态"
        elif diagnosing:
            confirm_cleared_reason = "诊断进行中，请等待本轮结束"
        elif standing is None:
            confirm_cleared_reason = "当前没有待裁决的结论"
        elif current_verdict is not None and current_verdict.blocking_finding_count == 0:
            # 界面上一条待处理的问题都没有时，说「本轮还有你没处理的问题」就是假话（K5）。
            # 能走到这里说明站立结论不是「建议修订」——是的话上面 cleared 已经把通道打开了。
            # 直接确认通道本就只对「建议修订」开，真正的下一步是裁决这条结论本身。
            confirm_cleared_reason = "本轮没有待你处理的问题；当前这条结论请直接采纳或拒绝"
        else:
            confirm_cleared_reason = "本轮还有你没处理的问题"

        return ReviewRequirementItemRead(
            item_ref=item.id,
            req_no=item.req_no,
            expression=item.expression,
            req_type=item.req_type,
            status=item.status,
            version_no=str(item.version_no),
            source_element_refs=list(json.loads(item.source_element_refs or "[]")),
            formation_basis_ref=item.formation_basis_ref,
            verification_method=split_verification_methods(item.verification_method),
            verification_note=item.verification_note,
            priority=item.priority,
            revision_records=[
                ItemRevisionRecordRead(
                    record_ref=rev.id, field_key=rev.field_key,
                    before_value=rev.before_value, after_value=rev.after_value,
                    revision_mode=rev.revision_mode,
                    selected_point_refs=list(json.loads(rev.selected_point_refs or "[]")),
                    operator_ref=rev.operator_ref,
                    reason=rev.reason, created_at=rev.at,
                )
                for rev in revisions
            ],
            review_status=display,
            status_note=note,
            display_code=display_code,
            display_note=display_note,
            current_verdict=current_verdict,
            verdict_history=history,
            dialogue_messages=self._project_dialogue(item.id),
            supplement_gaps_open=gaps,
            adopted_revise_rounds=adopted_revise_rounds,
            finding_vetoes=[
                self._project_veto(v)
                for v in reversed(self._reviews.vetoes_of_item(item.id, include_revoked=True))
            ],
            source_attestation=attestation,
            # 显示态守卫（K7）：这个标志位说的是「此刻正处在『来源缺口刚由人工确认闭合』
            # 这一步」，它必须与说明句同生共死。说明句只在显示态落到「待诊断」时才取那一句
            # （见 _derive_display_code），而人工确认与撤回都不动轮次，最新轮永远停在「被
            # 人工确认判失效」的那一轮上——不加这层守卫，条目确认之后区5 仍会挂着「人工确认」
            # 标签配「条目已确认。」这句话，标签断言的事与正文讲的事对不上。
            attestation_closed_gap=(
                display_code is RDC.PENDING_DIAGNOSIS
                and self._attestation_just_closed_gap(rounds)
            ),
            available_actions=[
                ActionFact(key="confirm_without_override", enabled=can_confirm_cleared,
                           disabled_reason=confirm_cleared_reason),
                ActionFact(key="adjudicate_verdict", enabled=can_adjudicate,
                           disabled_reason=None if can_adjudicate else "当前没有待裁决的结论"),
                ActionFact(key="review_dialogue", enabled=can_dialogue,
                           disabled_reason=None if can_dialogue else "诊断中或已收束，不可对话"),
                ActionFact(key="apply_manual_revision", enabled=can_manual,
                           disabled_reason=None if can_manual else "当前状态不可修订"),
                ActionFact(key="request_diagnosis", enabled=can_diagnose,
                           disabled_reason=None if can_diagnose else
                           ("来源缺口未闭合" if gaps else "当前状态不可发起诊断")),
                ActionFact(key="override_confirm", enabled=can_override,
                           disabled_reason=None if can_override else "当前状态不可覆盖确认"),
                ActionFact(key="withdraw_item", enabled=can_withdraw,
                           disabled_reason=None if can_withdraw else "当前状态不可撤回"),
                ActionFact(key="attest_source", enabled=can_attest,
                           disabled_reason=attest_reason),
            ],
        )
