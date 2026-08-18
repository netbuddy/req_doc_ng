import { describe, expect, it } from 'vitest';
import type { DocIndexEntryRead, PublicationWorkspaceRead } from '../src/api/publication';
import {
  buildArrangedEntries,
  buildArrangedSlotGroups,
  buildCandidateGroups,
  buildCandidateRows,
  buildCandidateTabs,
  buildDraftMissingList,
  buildEditImpactGroups,
  buildFooterSummary,
  buildIndexHeader,
  buildMarkdownState,
  buildSlotSections,
  buildSlotTree,
  compatibleSlotOptions,
  diffMarkdownLines,
  documentStatusMeta,
  editImpactMeta,
  filterCandidateRows,
  nextCoverageAnnouncement,
  renderMarkdownHtml,
  shouldAnnounceFullCoverage,
} from '../src/view-models/publication';
import type { MarkdownPatchRead, TemplateSectionRead } from '../src/api/publication';

function workspaceFixture(): PublicationWorkspaceRead {
  return {
    project_ref: 'p1',
    document: {
      document_ref: 'd1',
      doc_type: 'srs',
      title: '需求规格说明',
      template_ref: 'srs-iso29148-v1',
      template_schema_version: '1.0',
      coverage_scope: 'release-v0.1',
      status: 'index_ready',
      blocked_reason: null,
      index_version: 1,
    },
    template: {
      template_ref: 'srs-iso29148-v1',
      schema_version: '1.0',
      title: '需求规格说明',
      sections: [
        {
          key: 'intro', number: '1', title: '引言', level: 1, purpose: '',
          content_types: [], required: false, repeatable: false, missing_policy: 'skip',
        },
        {
          key: 'intro.purpose', number: '1.1', title: '编写目的', level: 2, purpose: '',
          content_types: ['boilerplate'], required: true, repeatable: false,
          missing_policy: 'block', boilerplate: '本文档定义…',
        },
        {
          key: 'requirements.functional', number: '3.1', title: '功能需求', level: 2, purpose: '',
          content_types: ['requirement_item:functional'], required: true, repeatable: true,
          missing_policy: 'block',
        },
        {
          key: 'requirements.quality', number: '3.4', title: '质量属性', level: 2, purpose: '',
          content_types: ['requirement_item:quality'], required: true, repeatable: true,
          missing_policy: 'block',
        },
        {
          key: 'requirements.charts', number: '3.5', title: '需求图表', level: 2, purpose: '',
          content_types: ['chart'], required: false, repeatable: true, missing_policy: 'skip',
        },
        {
          key: 'appendix.materials', number: '附录A', title: '支撑材料', level: 1, purpose: '',
          content_types: ['material'], required: false, repeatable: true, missing_policy: 'skip',
        },
      ],
      error: null,
    },
    candidates: {
      items: [
        { item_ref: 'i-f1', req_no: 'FR-001', expression: '系统应导出 docx', req_type: 'functional', status: 'confirmed', version_no: '1' },
        { item_ref: 'i-q1', req_no: 'NFR-001', expression: '响应不超两秒', req_type: 'quality', status: 'confirmed', version_no: '1' },
      ],
      materials: [
        { material_ref: 'm1', source_note: '评审纪要', excerpt: '……', source_version: 1 },
      ],
      charts: [
        {
          chart_ref: 'c1', title: '订单处理流程图', chart_type: 'flowchart', format: 'mermaid',
          status: 'confirmed', draft_version: 2, source_count: 2, confirmed_at: '2026-07-01T00:00:00Z',
        },
      ],
      traces: [],
      trace_summary: { effective: 3, pre_established: 1, suspect: 1 },
      pending_item_count: 2,
    },
    manuscripts: [],
    index_entries: [
      { section_key: 'requirements.functional', asset_type: 'requirement_item', asset_ref: 'i-f1', order_no: 0 },
    ],
    slot_status: [
      { section_key: 'intro.purpose', required: true, satisfied: true, filled_count: 1 },
      { section_key: 'requirements.functional', required: true, satisfied: true, filled_count: 1 },
      { section_key: 'requirements.quality', required: true, satisfied: false, filled_count: 0, missing_reason: '已有确认态候选资产但尚未编排到该槽位', rebuild_entry: '回到需求管理工作台…' },
      { section_key: 'appendix.materials', required: false, satisfied: true, filled_count: 0 },
    ],
    missing_list: [],
    markdown: null,
    exports: [],
    baseline: null,
    next_action: null,
  };
}

describe('publication view-model', () => {
  it('槽位视图：必填/可选、满足状态与缺失原因（UINV-16 索引编排页左栏）', () => {
    const ws = workspaceFixture();
    const slots = buildSlotSections(ws.template.sections, ws.slot_status);
    const functional = slots.find((s) => s.key === 'requirements.functional')!;
    expect(functional.requiredText).toBe('必填');
    expect(functional.statusText).toBe('已满足 1 条');
    expect(functional.statusTone).toBe('success');
    const quality = slots.find((s) => s.key === 'requirements.quality')!;
    expect(quality.statusText).toBe('缺失');
    expect(quality.statusTone).toBe('danger');
    expect(quality.missingReason).toContain('尚未编排');
    const boilerplate = slots.find((s) => s.key === 'intro.purpose')!;
    expect(boilerplate.statusText).toBe('模板自带');
    expect(boilerplate.acceptsAssets).toBe(false);
  });

  it('候选池：按类型分组、只含确认态、受控图表入候选、追溯只读', () => {
    const groups = buildCandidateGroups(workspaceFixture());
    const keys = groups.map((g) => g.key);
    expect(keys).toContain('item:functional');
    expect(keys).toContain('item:quality');
    expect(keys).toContain('material');
    expect(keys).toContain('chart');
    expect(keys).toContain('trace');
    expect(groups.find((g) => g.key === 'chart')!.items).toHaveLength(1);
    expect(groups.find((g) => g.key === 'trace')!.items).toHaveLength(0);
    expect(groups.find((g) => g.key === 'item:functional')!.items[0].label).toContain('FR-001');
  });

  it('信息条：文档/状态与需求统计（已阻塞无事实来源 → 缺失槽位替代）', () => {
    const ws = workspaceFixture();
    const header = buildIndexHeader(ws, ws.index_entries);
    expect(header.docTitle).toBe('需求规格说明');
    expect(header.statusText).toBe('索引就绪');
    expect(header.stats.total).toBe(4); // 确认 2 + 待确认 2
    expect(header.stats.confirmed).toBe(2);
    expect(header.stats.pending).toBe(2);
    expect(header.stats.missingSlots).toBe(1); // 质量必填未编排
  });

  it('槽位树：level-1 分组、覆盖状态按草稿实时计算、必填进度', () => {
    const ws = workspaceFixture();
    const tree = buildSlotTree(ws.template.sections, ws.index_entries);
    const intro = tree.groups.find((g) => g.key === 'intro')!;
    expect(intro.rows.map((r) => r.key)).toContain('intro.purpose');
    // level-1 自身是槽位（附录A 支撑材料）→ 作为该组唯一行
    const appendix = tree.groups.find((g) => g.key === 'appendix.materials')!;
    expect(appendix.rows).toHaveLength(1);
    const rows = tree.groups.flatMap((g) => g.rows);
    expect(rows.find((r) => r.key === 'requirements.functional')!.coverageText).toBe('已满足 · 1 项');
    expect(rows.find((r) => r.key === 'requirements.quality')!.coverageText).toBe('缺失');
    expect(rows.find((r) => r.key === 'requirements.charts')!.coverageText).toBe('未编排');
    expect(rows.find((r) => r.key === 'requirements.charts')!.acceptTypeText).toBe('图表槽位');
    // 必填 = 编写目的(boilerplate 已满足) + 功能(已编排) + 质量(缺失)
    expect(tree.requiredProgress).toMatchObject({ covered: 2, total: 3, missing: 1 });
  });

  it('槽位树：章节撰稿口径（可撰稿标记 + 已撰稿覆盖文案）', () => {
    const ws = workspaceFixture();
    // 无撰稿：boilerplate 章节 = 已满足，可撰稿
    const tree = buildSlotTree(ws.template.sections, ws.index_entries);
    const purpose = tree.groups.flatMap((g) => g.rows).find((r) => r.key === 'intro.purpose')!;
    expect(purpose.authorable).toBe(true);
    expect(purpose.coverageText).toBe('已满足');
    // 有撰稿：覆盖文案 = 已撰稿，仍计入必填覆盖
    const authoredTree = buildSlotTree(ws.template.sections, ws.index_entries, new Set(['intro.purpose']));
    const authoredRow = authoredTree.groups.flatMap((g) => g.rows).find((r) => r.key === 'intro.purpose')!;
    expect(authoredRow.coverageText).toBe('已撰稿');
    expect(authoredTree.requiredProgress).toMatchObject({ covered: 2, total: 3 });
    // 条目槽位不可撰稿
    const fn = tree.groups.flatMap((g) => g.rows).find((r) => r.key === 'requirements.functional')!;
    expect(fn.authorable).toBe(false);
  });

  it('槽位树：纯撰稿章节（authored_text）需撰稿/已撰稿裁定', () => {
    const ws = workspaceFixture();
    ws.template.sections = [
      ...ws.template.sections,
      {
        key: 'intro.background', number: '1.9', title: '编写背景', level: 2, purpose: '',
        content_types: ['authored_text'], required: true, repeatable: false,
        missing_policy: 'block', boilerplate: null,
      },
    ];
    const tree = buildSlotTree(ws.template.sections, ws.index_entries);
    const row = tree.groups.flatMap((g) => g.rows).find((r) => r.key === 'intro.background')!;
    expect(row.authorable).toBe(true);
    expect(row.coverageText).toBe('需撰稿');
    expect(row.requiredText).toBe('必填');
    const authored = buildSlotTree(ws.template.sections, ws.index_entries, new Set(['intro.background']));
    expect(
      authored.groups.flatMap((g) => g.rows).find((r) => r.key === 'intro.background')!.coverageText,
    ).toBe('已撰稿');
  });

  it('候选池 tabs 与行投影：四 tab 计数、图表行含来源数与受控准入', () => {
    const ws = workspaceFixture();
    const tabs = buildCandidateTabs(ws);
    expect(tabs.map((t) => [t.key, t.count])).toEqual([
      ['items', 2],
      ['charts', 1],
      ['traces', 0],
      ['materials', 1],
    ]);
    const chartRows = buildCandidateRows(ws, 'charts');
    expect(chartRows[0]).toMatchObject({
      ref: 'c1',
      kind: 'chart',
      sourceCount: 2,
      admissionText: '受控图表',
    });
    expect(buildCandidateRows(ws, 'traces')).toHaveLength(0);
  });

  it('候选池筛选分页：关键词/类型过滤 + 分页边界（纯函数）', () => {
    const ws = workspaceFixture();
    const rows = buildCandidateRows(ws, 'items');
    expect(filterCandidateRows(rows, { keyword: 'docx', typeFilter: 'all' }, 1, 20).total).toBe(1);
    // 'fr-001' 同时命中 NFR-001（子串匹配口径）
    expect(filterCandidateRows(rows, { keyword: 'fr-001', typeFilter: 'all' }, 1, 20).total).toBe(2);
    expect(filterCandidateRows(rows, { keyword: '', typeFilter: 'quality' }, 1, 20).rows[0].ref).toBe('i-q1');
    expect(filterCandidateRows(rows, { keyword: '不存在', typeFilter: 'all' }, 1, 20).total).toBe(0);
    const page2 = filterCandidateRows(rows, { keyword: '', typeFilter: 'all' }, 2, 1);
    expect(page2.total).toBe(2);
    expect(page2.rows).toHaveLength(1);
  });

  it('已编排槽位分组：含空槽位、徽标语义、换槽位候选（分母无事实来源 → N 项徽标）', () => {
    const ws = workspaceFixture();
    const groups = buildArrangedSlotGroups(
      [
        ...ws.index_entries,
        { section_key: 'requirements.charts', asset_type: 'chart', asset_ref: 'c1', order_no: 0 },
      ],
      ws,
    );
    const functional = groups.find((g) => g.sectionKey === 'requirements.functional')!;
    expect(functional.badgeText).toBe('1 项 · 已满足');
    expect(functional.entries[0].no).toBe('FR-001');
    const quality = groups.find((g) => g.sectionKey === 'requirements.quality')!;
    expect(quality.entries).toHaveLength(0);
    expect(quality.badgeText).toBe('缺失');
    const charts = groups.find((g) => g.sectionKey === 'requirements.charts')!;
    expect(charts.addTab).toBe('charts');
    expect(charts.entries[0]).toMatchObject({ title: '订单处理流程图', statusText: '受控图表' });
    // 模板仅一个图表槽位 → 无处可换
    expect(charts.entries[0].slotOptions).toHaveLength(0);
    expect(compatibleSlotOptions(ws.template.sections, 'chart')).toEqual([
      { key: 'requirements.charts', label: '3.5 需求图表' },
    ]);
  });

  it('已编排索引按章节分组并保序', () => {
    const ws = workspaceFixture();
    const arranged = buildArrangedEntries(ws.index_entries, ws.template.sections, ws);
    expect(arranged).toHaveLength(1);
    expect(arranged[0].sectionTitle).toBe('3.1 功能需求');
    expect(arranged[0].entries[0].label).toContain('FR-001');
  });

  it('底栏：必填覆盖统计与进入 Markdown 门禁', () => {
    const ws = workspaceFixture();
    // 草稿只编排了功能槽位 → 质量必填未覆盖；分母与槽位树同源（含 boilerplate 必填章节，恒由模板满足）
    const footer = buildFooterSummary(ws, ws.index_entries);
    expect(footer.templateText).toBe('模板校验通过');
    expect(footer.requiredCoverageText).toBe('必填覆盖 2/3');
    expect(footer.missingCount).toBe(1);
    expect(footer.selectedCount).toBe(1);
    expect(footer.admissionText).toBe('全部通过');
    expect(footer.canEnterMarkdown).toBe(false);
    // 补上质量条目 → 全覆盖
    const full = buildFooterSummary(ws, [
      ...ws.index_entries,
      { section_key: 'requirements.quality', asset_type: 'requirement_item', asset_ref: 'i-q1', order_no: 0 },
    ]);
    expect(full.canEnterMarkdown).toBe(true);
  });

  it('缺失清单：草稿派生实时闭合，与底栏/槽位树同源（issue #14）', () => {
    const ws = workspaceFixture();
    const qualityEntry: DocIndexEntryRead = {
      section_key: 'requirements.quality', asset_type: 'requirement_item', asset_ref: 'i-q1', order_no: 0,
    };

    // 空草稿：功能与质量两个必填资产槽位均缺失
    const empty = buildDraftMissingList(ws, []);
    expect(empty.map((m) => m.section_key)).toEqual(['requirements.functional', 'requirements.quality']);
    expect(empty[0].section_title).toBe('3.1 功能需求');
    expect(empty[0].reason).toContain('必填槽位缺失');
    expect(empty[0].rebuild_entry).toContain('需求管理工作台');
    expect(empty.every((m) => m.blocking)).toBe(true);

    // 部分覆盖：已编排功能槽位 → 只余质量；与底栏缺失计数一致
    const partial = buildDraftMissingList(ws, ws.index_entries);
    expect(partial.map((m) => m.section_key)).toEqual(['requirements.quality']);
    expect(partial).toHaveLength(buildFooterSummary(ws, ws.index_entries).missingCount);

    // 全覆盖：勾选补齐即清单闭合，无需保存往返。
    // issue #14 缺陷态回归：服务端 missing_list 仍停留在上次读取口径（质量槽位缺失，用户勾选后未刷新），
    // 草稿已补齐 → 派生清单必须抑制该服务端已知行，证明「读侧草稿派生」而非透传 missing_list。
    ws.missing_list = [{
      section_key: 'requirements.quality', section_title: '3.4 质量属性',
      reason: '必填槽位缺失：已有确认态候选资产但尚未编排到该槽位',
      rebuild_entry: '回到需求管理工作台：材料接入 → 知识抽取 → 条目形成 → 条目确认后重新编排',
    }];
    const full = [...ws.index_entries, qualityEntry];
    expect(buildDraftMissingList(ws, full)).toEqual([]);
    expect(buildFooterSummary(ws, full).missingCount).toBe(0);

    // 可逆：取消勾选 → 该行实时恢复（服务端此时判已满足、不下发文案，由前端补建入口兜底）
    expect(buildDraftMissingList(ws, [qualityEntry]).map((m) => m.section_key))
      .toEqual(['requirements.functional']);
  });

  it('缺失清单：非资产槽位沿用服务端原行，保存后回落服务端口径', () => {
    const ws = workspaceFixture();
    // 知识整表投影等非草稿相关槽位：原样透传，不由前端另造判据
    ws.template.sections.push({
      key: 'appendix.glossary', number: '附录B', title: '术语表', level: 1, purpose: '',
      content_types: ['glossary'], required: false, repeatable: false, missing_policy: 'skip',
    } as never);
    const serverRow = {
      section_key: 'appendix.glossary', section_title: '附录B 术语表',
      reason: '本项目暂无已确认术语（整表投影为空，非阻断）', rebuild_entry: '回到知识抽取页确认业务领域知识…',
    };
    ws.missing_list = [serverRow];
    const rows = buildDraftMissingList(ws, ws.index_entries);
    expect(rows.find((m) => m.section_key === 'appendix.glossary')).toEqual({ ...serverRow, blocking: false });
    // 非阻断行不冒充必填缺失：阻断行数恒等于底栏「缺失槽位」读数（A1 两处读数一致）
    expect(rows.filter((m) => m.blocking)).toHaveLength(
      buildFooterSummary(ws, ws.index_entries).missingCount,
    );

    // 保存后草稿=服务端条目：资产槽位派生结果与服务端 missing_list 同值
    const savedServerRow = {
      section_key: 'requirements.quality', section_title: '3.4 质量属性',
      reason: '必填槽位缺失：已有确认态候选资产但尚未编排到该槽位',
      rebuild_entry: '回到需求管理工作台：材料接入 → 知识抽取 → 条目形成 → 条目确认后重新编排',
    };
    ws.missing_list = [savedServerRow];
    expect(buildDraftMissingList(ws, ws.index_entries)).toContainEqual({ ...savedServerRow, blocking: true });
  });

  it('成功提示去重：仅「有缺失 → 无缺失」跃迁提示一次（冷审查 F3）', () => {
    // 首次渲染无基线（含 StrictMode 双调用、初始即全覆盖）→ 不提示
    expect(shouldAnnounceFullCoverage(null, 0)).toBe(false);
    expect(shouldAnnounceFullCoverage(null, 2)).toBe(false);
    // 连续勾选补齐两个槽位：2→1 不提示，1→0 提示一次
    expect(shouldAnnounceFullCoverage(2, 1)).toBe(false);
    expect(shouldAnnounceFullCoverage(1, 0)).toBe(true);
    // 批处理直落 2→0 同样只提示一次；已归零后重复渲染不再提示
    expect(shouldAnnounceFullCoverage(2, 0)).toBe(true);
    expect(shouldAnnounceFullCoverage(0, 0)).toBe(false);
    // 取消勾选重新缺失后再次补齐 → 允许再次提示（每次闭合各一次）
    expect(shouldAnnounceFullCoverage(0, 1)).toBe(false);
    expect(shouldAnnounceFullCoverage(1, 0)).toBe(true);
  });

  it('全覆盖提示跃迁纯函数：首帧只记录、>0→0 恰一次、服务端刷新落 0 与模板错误不宣布（T4/P1）', () => {
    // 首帧无基线（含 StrictMode 双调用、初始即全覆盖）：只记录不宣布，基线落当前值
    expect(nextCoverageAnnouncement(null, 2, false)).toEqual({ announce: false, nextBaseline: 2 });
    expect(nextCoverageAnnouncement(null, 0, true)).toEqual({ announce: false, nextBaseline: 0 });
    // >0 → 0 且可进入 Markdown：宣布恰一次
    expect(nextCoverageAnnouncement(1, 0, true)).toEqual({ announce: true, nextBaseline: 0 });
    expect(nextCoverageAnnouncement(2, 0, true)).toEqual({ announce: true, nextBaseline: 0 });
    // 0 → 0 不宣布（已归零后重复渲染静默）
    expect(nextCoverageAnnouncement(0, 0, true)).toEqual({ announce: false, nextBaseline: 0 });
    // C1：服务端刷新把基线置 null 后落 0（如撰稿保存/导出轮询覆盖草稿）→ 只记录不宣布
    expect(nextCoverageAnnouncement(null, 0, true).announce).toBe(false);
    // P1：模板校验失败时 missingCount 因空章节集为 0，但 canEnterMarkdown=false → 即使 1→0 也不宣布
    expect(nextCoverageAnnouncement(1, 0, false)).toEqual({ announce: false, nextBaseline: 0 });
  });

  it('Markdown 状态：草稿可编辑、阻断项禁定稿、失效需重生成（UINV-17）', () => {
    expect(buildMarkdownState(null).canEdit).toBe(false);
    const draft = buildMarkdownState({
      draft_ref: 'md1', version_no: 1, index_version: 1, status: 'draft', can_export: false,
      content: '# 1 引言', source_bindings: [], block_reasons: [], patches: [],
    });
    expect(draft.canEdit).toBe(true);
    expect(draft.canFinalize).toBe(true);
    const blocked = buildMarkdownState({
      draft_ref: 'md1', version_no: 1, index_version: 1, status: 'draft', can_export: false,
      content: '', source_bindings: [], block_reasons: ['存在来源材料无法支撑的新事实'], patches: [],
    });
    expect(blocked.canFinalize).toBe(false);
    const awaiting = buildMarkdownState({
      draft_ref: 'md1', version_no: 2, index_version: 1, status: 'awaiting_item_revision', can_export: false,
      content: '', source_bindings: [], block_reasons: [], patches: [],
    });
    expect(awaiting.needsRegenerate).toBe(true);
  });

  it('编辑影响与文档状态元数据齐备（UINV-04 不混写：类型/状态明确标识）', () => {
    expect(editImpactMeta('doc_expression').tone).toBe('success');
    expect(editImpactMeta('confirmed_item').label).toContain('待修订确认态条目');
    expect(editImpactMeta('no_source_fact').tone).toBe('danger');
    expect(documentStatusMeta('baseline_published').label).toContain('发布基线');
    expect(documentStatusMeta(undefined).label).toBe('未编排');
  });

  it('Markdown 预览渲染：标题/加粗/HTML 转义', () => {
    const html = renderMarkdownHtml('# 1 引言\n**FR-001** 系统应<b>导出</b>\n- 来源：材料');
    expect(html).toContain('<h1 data-line="0">1 引言</h1>');
    expect(html).toContain('<strong>FR-001</strong>');
    expect(html).toContain('&lt;b&gt;');
    expect(html).toContain('<li data-line="2">来源：材料</li>');
  });

  it('Markdown 预览渲染：条目属性表 → table（分隔行过滤、单元格转义）', () => {
    const html = renderMarkdownHtml(
      '**FR-001**（功能需求 · v1 · 已确认）\n\n系统应导出 docx\n\n| 属性 | 说明 |\n| --- | --- |\n| 内容整理说明 | 合并<同义>表述 |\n| 关联图表 | 流程图 |',
    );
    expect(html).toContain('<table data-line="4"><thead><tr><th>属性</th><th>说明</th></tr></thead>');
    expect(html).toContain('<td>内容整理说明</td><td>合并&lt;同义&gt;表述</td>');
    expect(html).toContain('<td>关联图表</td><td>流程图</td>');
    expect(html).not.toContain('---'); // 分隔行不输出
    expect(html).toContain('<strong>FR-001</strong>');
  });

  it('Markdown 预览渲染：围栏 → pre/code（纯文本渲染器；真图由 MarkdownPreview 组件承接）', () => {
    const html = renderMarkdownHtml('## 3.5 需求图表\n```mermaid\nflowchart TD\n  A --> B\n```\n尾段');
    expect(html).toContain('<h2 data-line="0">3.5 需求图表</h2>');
    expect(html).toContain('<pre data-line="1"><code>flowchart TD\n  A --&gt; B</code></pre>');
    expect(html).toContain('<p data-line="5">尾段</p>');
  });
});

describe('D2 行级 diff（生成稿 ↔ 当前稿）', () => {
  it('无改动：全 same，计数全 0，hunks 空', () => {
    const d = diffMarkdownLines('a\nb\nc', 'a\nb\nc');
    expect(d.lines.map((l) => l.status)).toEqual(['same', 'same', 'same']);
    expect([d.add, d.chg, d.del]).toEqual([0, 0, 0]);
    expect(d.hunks).toEqual([]);
  });

  it('纯新增：中间插入一行记 add，hunks 定位该行', () => {
    const d = diffMarkdownLines('a\nb', 'a\nX\nb');
    expect(d.lines.map((l) => l.status)).toEqual(['same', 'add', 'same']);
    expect(d.add).toBe(1);
    expect(d.hunks).toEqual([1]);
  });

  it('纯删除：不进 add/chg 计数、记 delBefore（F1 前提：内容≠baseline 但 add+chg=0）', () => {
    const d = diffMarkdownLines('a\nX\nb', 'a\nb');
    expect(d.lines.map((l) => l.status)).toEqual(['same', 'same']);
    expect([d.add, d.chg]).toEqual([0, 0]);
    expect(d.del).toBe(1);
    expect(d.lines[1].delBefore).toBe(1); // 删除记在后继行之前
    expect(d.hunks).toEqual([]); // 无 add/chg run
  });

  it('修改：删+增配对为 chg，删除被吸收不再单独计 del', () => {
    const d = diffMarkdownLines('a\nb\nc', 'a\nB\nc');
    expect(d.lines.map((l) => l.status)).toEqual(['same', 'chg', 'same']);
    expect([d.add, d.chg, d.del]).toEqual([0, 1, 0]);
    expect(d.hunks).toEqual([1]);
  });

  it('空 baseline：全部 add', () => {
    const d = diffMarkdownLines('', 'a\nb');
    expect(d.lines.map((l) => l.status)).toEqual(['add', 'add']);
    expect(d.add).toBe(2);
    expect(d.hunks).toEqual([0]);
  });
});

describe('D4 编辑影响按对象分组', () => {
  const sections: TemplateSectionRead[] = [
    { key: 's3', number: '3', title: '功能需求', level: 1, purpose: '', content_types: [], required: true, repeatable: false, missing_policy: 'block' },
    { key: 's3.1', number: '3.1', title: '导出功能', level: 2, purpose: '', content_types: ['requirement_item'], required: true, repeatable: false, missing_policy: 'block' },
  ];
  const indexEntries: DocIndexEntryRead[] = [
    { section_key: 's3.1', asset_type: 'requirement_item', asset_ref: 'REQ-001' },
  ];
  const patch = (over: Partial<MarkdownPatchRead>): MarkdownPatchRead => ({
    patch_ref: 'p', impact: 'doc_expression', before_text: '', after_text: '', status: 'recorded', ...over,
  });

  it('五组齐备、概览句与判定 pill 随 patch 派生', () => {
    const vm = buildEditImpactGroups(
      [
        patch({ patch_ref: 'p1', impact: 'confirmed_item', bound_item_ref: 'REQ-001', before_text: '旧表述', after_text: '新表述' }),
        patch({ patch_ref: 'p2', impact: 'other_asset', bound_item_ref: 'CHT-01' }),
        patch({ patch_ref: 'p3', impact: 'no_source_fact' }),
      ],
      indexEntries,
      sections,
    );
    const byKey = Object.fromEntries(vm.groups.map((g) => [g.key, g]));
    expect(byKey.section.count).toBe(1); // REQ-001 → s3.1
    expect(byKey.section.items[0].label).toBe('3.1 导出功能');
    expect(byKey.item.count).toBe(1);
    expect(byKey.item.title).toBe('涉及的确认态条目'); // F-2：组标题中性化
    expect(byKey.item.items[0].detail).toContain('旧表述 → 新表述');
    expect(byKey.chart.count).toBe(1);
    expect(byKey.hint.count).toBe(1);
    expect(byKey.structure.count).toBe(0); // 无 index_structure 补丁时第五组留空
    expect(vm.groups).toHaveLength(5);
    expect(vm.needsReview).toBe(3);
    expect(vm.verdict.label).toBe('需复核');
    expect(vm.overviewText).toBe('3 处改动 · 1 章节 · 1 条目 · 1 图表 · 待复核 3');
  });

  it('无影响项：判定 pill = 可定稿、各组留空框', () => {
    const vm = buildEditImpactGroups([], indexEntries, sections);
    expect(vm.verdict.label).toBe('可定稿');
    expect(vm.needsReview).toBe(0);
    expect(vm.groups).toHaveLength(5);
    expect(vm.groups.every((g) => g.count === 0)).toBe(true);
  });

  it('F-1 ①：不带可 join ref 的 index_structure 补丁 → 只进第五组（章节结构），其余组 0', () => {
    const vm = buildEditImpactGroups(
      [patch({ patch_ref: 'ps', impact: 'index_structure', before_text: '旧标题', after_text: '新标题' })],
      indexEntries,
      sections,
    );
    const byKey = Object.fromEntries(vm.groups.map((g) => [g.key, g]));
    expect(byKey.structure.count).toBe(1);
    expect(byKey.structure.title).toBe('章节结构（回索引编排）');
    expect(byKey.structure.tone).toBe('danger');
    expect(byKey.structure.items[0].detail).toContain('回到索引编排');
    // 其余四组均 0
    expect(byKey.section.count).toBe(0);
    expect(byKey.item.count).toBe(0);
    expect(byKey.chart.count).toBe(0);
    expect(byKey.hint.count).toBe(0);
  });

  it('F-1 ②：带可 join 的 reflow_item_ref 的 index_structure 补丁 → 仍归第五组，不进章节组（专有分支优先）', () => {
    const vm = buildEditImpactGroups(
      // reflow_item_ref = REQ-001 可 join 到 s3.1，但 impact=index_structure 专有分支优先
      [patch({ patch_ref: 'ps2', impact: 'index_structure', reflow_item_ref: 'REQ-001' })],
      indexEntries,
      sections,
    );
    const byKey = Object.fromEntries(vm.groups.map((g) => [g.key, g]));
    expect(byKey.structure.count).toBe(1);
    expect(byKey.section.count).toBe(0); // 未并入章节组
    expect(byKey.item.count).toBe(0);
  });

  it('F-1 ③：需复核数与分组卡自洽——每一处待复核补丁都能在某张分组卡中找到', () => {
    const vm = buildEditImpactGroups(
      [
        patch({ patch_ref: 'q1', impact: 'confirmed_item', bound_item_ref: 'REQ-001' }),
        patch({ patch_ref: 'q2', impact: 'index_structure', reflow_item_ref: 'REQ-001' }),
        patch({ patch_ref: 'q3', impact: 'no_source_fact' }),
        patch({ patch_ref: 'q4', impact: 'other_asset', bound_item_ref: 'CHT-01' }),
      ],
      indexEntries,
      sections,
    );
    // 四处待复核补丁（confirmed_item / index_structure / no_source_fact / other_asset）
    expect(vm.needsReview).toBe(4);
    expect(vm.verdict.label).toBe('需复核');
    // 口径自洽：可复核态分组卡（条目/章节结构/图表/提示）可见项之和 ≥ 待复核数，
    // 即不再出现「待复核 W 但各分组卡全 0」的无卡可寻状态。
    const byKey = Object.fromEntries(vm.groups.map((g) => [g.key, g]));
    const reviewableVisible =
      byKey.item.count + byKey.structure.count + byKey.chart.count + byKey.hint.count;
    expect(reviewableVisible).toBeGreaterThanOrEqual(vm.needsReview);
    // 该 index_structure 补丁确实在第五组可寻
    expect(byKey.structure.count).toBe(1);
  });
});
