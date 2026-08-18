import {
  Alert,
  Badge,
  Button,
  Drawer,
  Empty,
  Input,
  Modal,
  Pagination,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { BookOutlined, RobotOutlined, SearchOutlined } from '@ant-design/icons';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { DragEvent } from 'react';
import type { TextAreaRef } from 'antd/es/input/TextArea';
import type {
  CandidatePreviewRead,
  DocIndexEntryRead,
  PublicationWorkspaceRead,
  SectionDraftBasisRead,
  SlotAssetType,
  TemplateSectionRead,
} from '../api/publication';
import { ManuscriptDeclinedError } from '../api/publication';
import { settingsApi, type ReferenceStandardRead } from '../api/settings';
import {
  buildArrangedSlotGroups,
  buildCandidateRows,
  buildCandidateTabs,
  buildIndexHeader,
  buildSlotTree,
  draftBasisHint,
  filterCandidateRows,
  formatCitationLines,
  insertCitations,
  nextCitationNumber,
  type ArrangedSlotGroupVM,
  type CandidateRowVM,
  type CandidateTabKey,
  type FooterSummaryVM,
  type MissingRowVM,
} from '../view-models/publication';
import { requirementItemTypeText } from '../view-models/requirement-item-formation';
import type { BadgeTone } from '../view-models/common';
import { MarkdownPreview } from '../ui/MarkdownPreview';

const { Text } = Typography;

const TONE_COLOR: Record<BadgeTone, string> = {
  success: 'green',
  processing: 'blue',
  warning: 'orange',
  danger: 'red',
  neutral: 'default',
};

const ITEM_TYPE_FILTERS = ['functional', 'quality', 'constraint', 'data', 'interface'] as const;

const PAGE_SIZE = 20;

interface DragPayload {
  sectionKey: string;
  assetRef: string;
  assetType: SlotAssetType;
  reqType: string | null;
}

export interface PublicationIndexViewProps {
  workspace: PublicationWorkspaceRead;
  draftEntries: DocIndexEntryRead[];
  currentTemplateText: string;
  missingList: MissingRowVM[];
  footer: FooterSummaryVM | null;
  coverageScope: string;
  busy: boolean;
  canCancel: boolean;
  onCoverageScopeChange: (value: string) => void;
  onTemplatePreview: () => void;
  onChangeTemplate: () => void;
  /** 勾选/取消候选资产；sectionKey 指定目标槽位（「+添加到此槽位」联动） */
  onToggleAsset: (
    assetType: 'requirement_item' | 'material' | 'chart',
    ref: string,
    reqType?: string,
    sectionKey?: string,
  ) => void;
  onMove: (sectionKey: string, ref: string, delta: number) => void;
  onRemove: (ref: string) => void;
  onChangeSlot: (ref: string, sectionKey: string) => void;
  onReorderTo: (sectionKey: string, ref: string, targetIndex: number) => void;
  onClear: () => void;
  onSave: () => void;
  onSaveAndEnterMarkdown: () => void;
  onCancel: () => void;
  /** 章节撰稿（AEP-098）：保存后宿主刷新工作区 */
  onSaveManuscript: (sectionKey: string, content: string) => Promise<void>;
  /** 章节 AI 起草初稿（AEP-110）：返回初稿正文供人工完善（authored_text 章节） */
  onDraftManuscript: (sectionKey: string) => Promise<string>;
  /** 候选资产最终渲染预览（AEP-099，只读） */
  onLoadPreview: (
    kind: 'requirement_item' | 'chart' | 'material',
    ref: string,
  ) => Promise<CandidatePreviewRead>;
}

// ---- P01 索引编排页（04A §8 原型高保真：信息条 + 三栏 + 底栏）----

export function PublicationIndexView(props: PublicationIndexViewProps) {
  const {
    workspace, draftEntries, currentTemplateText, missingList, footer,
    coverageScope, busy, canCancel,
    onCoverageScopeChange, onTemplatePreview, onChangeTemplate,
    onToggleAsset, onMove, onRemove, onChangeSlot, onReorderTo, onClear,
    onSave, onSaveAndEnterMarkdown, onCancel, onSaveManuscript, onDraftManuscript, onLoadPreview,
  } = props;

  const [activeTab, setActiveTab] = useState<CandidateTabKey>('items');
  const [keyword, setKeyword] = useState('');
  const [typeFilter, setTypeFilter] = useState<string | 'all'>('all');
  const [page, setPage] = useState(1);
  const [activeSlotKey, setActiveSlotKey] = useState<string | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [manuscriptSectionKey, setManuscriptSectionKey] = useState<string | null>(null);
  const [previewTarget, setPreviewTarget] = useState<CandidateRowVM | null>(null);

  const manuscriptKeys = useMemo(
    () => new Set((workspace.manuscripts ?? []).map((m) => m.section_key)),
    [workspace],
  );
  const header = useMemo(() => buildIndexHeader(workspace, draftEntries), [workspace, draftEntries]);
  const slotTree = useMemo(
    () => buildSlotTree(workspace.template.sections, draftEntries, manuscriptKeys),
    [workspace, draftEntries, manuscriptKeys],
  );
  const tabs = useMemo(() => buildCandidateTabs(workspace), [workspace]);
  const allRows = useMemo(() => buildCandidateRows(workspace, activeTab), [workspace, activeTab]);
  const paged = useMemo(
    () => filterCandidateRows(allRows, { keyword, typeFilter }, page, PAGE_SIZE),
    [allRows, keyword, typeFilter, page],
  );
  const arrangedGroups = useMemo(
    () => buildArrangedSlotGroups(draftEntries, workspace),
    [draftEntries, workspace],
  );
  const selectedRefs = useMemo(
    () => new Set(draftEntries.map((e) => e.asset_ref ?? '')),
    [draftEntries],
  );
  const activeSlotGroup = activeSlotKey
    ? arrangedGroups.find((g) => g.sectionKey === activeSlotKey) ?? null
    : null;

  const switchTab = (key: CandidateTabKey) => {
    setActiveTab(key);
    setKeyword('');
    setTypeFilter('all');
    setPage(1);
  };

  const focusSlot = (group: ArrangedSlotGroupVM) => {
    setActiveSlotKey(group.sectionKey);
    switchTab(group.addTab);
  };

  const toggleRow = (row: CandidateRowVM) => {
    onToggleAsset(row.kind, row.ref, row.reqType ?? undefined, activeSlotKey ?? undefined);
  };

  return (
    <div className="pub-index" data-testid="index-orchestration">
      <IndexInfoBar
        header={header}
        templateText={currentTemplateText}
        coverageScope={coverageScope}
        onCoverageScopeChange={onCoverageScopeChange}
      />

      <div className="pub-index__grid">
        <SlotTreePanel
          tree={slotTree}
          collapsedGroups={collapsedGroups}
          onToggleGroup={(key) =>
            setCollapsedGroups((current) => {
              const next = new Set(current);
              if (next.has(key)) next.delete(key);
              else next.add(key);
              return next;
            })
          }
          onTemplatePreview={onTemplatePreview}
          onChangeTemplate={onChangeTemplate}
          onAuthor={(key) => setManuscriptSectionKey(key)}
        />

        <section className="panel pub-pool" data-testid="candidate-pool">
          <div className="panel__header pub-pool__header">
            <h2 className="panel__title">候选资产池</h2>
            <Text type="secondary" className="pub-pool__note">仅确认态/受控资产可入索引</Text>
          </div>
          {activeSlotGroup ? (
            <Alert
              type="info"
              showIcon
              closable
              onClose={() => setActiveSlotKey(null)}
              title={`正在为「${activeSlotGroup.number} ${activeSlotGroup.title}」添加：勾选即写入该槽位`}
              className="pub-pool__slot-hint"
            />
          ) : null}
          <div className="pub-pool__tabs" role="tablist">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                type="button"
                role="tab"
                aria-selected={activeTab === tab.key}
                className={
                  activeTab === tab.key ? 'pub-pool__tab pub-pool__tab--active' : 'pub-pool__tab'
                }
                onClick={() => switchTab(tab.key)}
              >
                {tab.label}
                <span className="pub-pool__tab-count">{tab.count}</span>
              </button>
            ))}
          </div>
          {activeTab === 'traces' ? (
            <TraceReadonlyCard summary={workspace.candidates.trace_summary} />
          ) : (
            <>
              <div className="pub-pool__toolbar">
                <Input
                  allowClear
                  size="small"
                  prefix={<SearchOutlined />}
                  placeholder="搜索 ID / 标题 / 关键词"
                  value={keyword}
                  onChange={(e) => {
                    setKeyword(e.target.value);
                    setPage(1);
                  }}
                  className="pub-pool__search"
                />
                {activeTab === 'items' ? (
                  <Select
                    size="small"
                    value={typeFilter}
                    onChange={(value) => {
                      setTypeFilter(value);
                      setPage(1);
                    }}
                    options={[
                      { value: 'all', label: '类型：全部' },
                      ...ITEM_TYPE_FILTERS.map((t) => ({
                        value: t,
                        label: requirementItemTypeText(t),
                      })),
                    ]}
                    className="pub-pool__filter"
                  />
                ) : null}
              </div>
              {activeTab === 'items' && workspace.candidates.pending_item_count > 0 ? (
                <Alert
                  type="warning"
                  showIcon
                  title={`另有 ${workspace.candidates.pending_item_count} 条待确认条目不在候选池（未确认资产不得入文档索引）`}
                  className="pub-pool__pending"
                />
              ) : null}
              <CandidateTable
                rows={paged.rows}
                selectedRefs={selectedRefs}
                emptyText={
                  allRows.length === 0
                    ? activeTab === 'charts'
                      ? '暂无受控图表：图表须在图表设计工作台完成核对与确认后进入候选'
                      : '暂无确认态候选资产'
                    : '当前筛选无结果'
                }
                onToggle={toggleRow}
                onPreview={setPreviewTarget}
              />
              <div className="pub-pool__pager">
                <Text type="secondary">
                  已选中 {footer?.selectedCount ?? 0} 项 · 共 {paged.total} 项
                </Text>
                <Pagination
                  simple
                  size="small"
                  current={page}
                  pageSize={PAGE_SIZE}
                  total={paged.total}
                  onChange={setPage}
                />
              </div>
            </>
          )}
        </section>

        <ArrangedIndexPanel
          groups={arrangedGroups}
          missingList={missingList}
          activeSlotKey={activeSlotKey}
          onFocusSlot={focusSlot}
          onMove={onMove}
          onRemove={onRemove}
          onChangeSlot={onChangeSlot}
          onReorderTo={onReorderTo}
          onClear={onClear}
        />
      </div>

      <div className="panel pub-index__footer" data-testid="index-footer">
        {footer ? (
          <>
            <span className="pub-index__footer-stat">
              <Badge status={footer.templateTone === 'success' ? 'success' : 'error'} text={footer.templateText} />
              <Text type="secondary" className="pub-index__footer-sub">索引结构与模板规则一致</Text>
            </span>
            <span className="pub-index__footer-stat">
              <Text type="secondary">必填覆盖</Text>
              <Text strong>{footer.requiredCoverageText.replace('必填覆盖 ', '')}</Text>
            </span>
            <span className="pub-index__footer-stat">
              <Text type="secondary">缺失</Text>
              <Text strong type={footer.missingCount > 0 ? 'danger' : undefined}>{footer.missingCount}</Text>
            </span>
            <span className="pub-index__footer-stat">
              <Text type="secondary">已选资产</Text>
              <Text strong>{footer.selectedCount} 项</Text>
            </span>
            <span className="pub-index__footer-stat">
              <Text type="secondary">准入校验</Text>
              <Tag color={TONE_COLOR[footer.admissionTone]}>{footer.admissionText}</Tag>
            </span>
            <span className="pub-index__footer-actions">
              {canCancel ? <Button onClick={onCancel} disabled={busy}>取消</Button> : null}
              <Button loading={busy} onClick={onSave}>保存索引</Button>
              <Tooltip
                title={footer.canEnterMarkdown ? null : '必填槽位未覆盖：先按缺失清单补齐或确认资产'}
              >
                <Button
                  type="primary"
                  loading={busy}
                  disabled={!footer.canEnterMarkdown}
                  onClick={onSaveAndEnterMarkdown}
                >
                  保存索引，进入 Markdown
                </Button>
              </Tooltip>
            </span>
          </>
        ) : null}
      </div>

      <ManuscriptDrawer
        sectionKey={manuscriptSectionKey}
        sections={workspace.template.sections}
        manuscripts={workspace.manuscripts ?? []}
        draftBasis={workspace.draft_basis ?? []}
        busy={busy}
        onClose={() => setManuscriptSectionKey(null)}
        onSave={async (key, content) => {
          await onSaveManuscript(key, content);
          setManuscriptSectionKey(null);
        }}
        onDraft={onDraftManuscript}
      />
      <CandidatePreviewDrawer
        target={previewTarget}
        onClose={() => setPreviewTarget(null)}
        onLoad={onLoadPreview}
      />
    </div>
  );
}

// ---- 章节撰稿 Drawer（AEP-098：人工正文第一类来源；默认文本仅为预填稿）----

function ManuscriptDrawer({
  sectionKey,
  sections,
  manuscripts,
  draftBasis,
  busy,
  onClose,
  onSave,
  onDraft,
}: {
  sectionKey: string | null;
  sections: TemplateSectionRead[];
  manuscripts: PublicationWorkspaceRead['manuscripts'];
  draftBasis: SectionDraftBasisRead[];
  busy: boolean;
  onClose: () => void;
  onSave: (sectionKey: string, content: string) => Promise<void>;
  onDraft: (sectionKey: string) => Promise<string>;
}) {
  const section = sections.find((s) => s.key === sectionKey) ?? null;
  const manuscript = manuscripts.find((m) => m.section_key === sectionKey) ?? null;
  const [text, setText] = useState('');
  const [saving, setSaving] = useState(false);
  const [drafting, setDrafting] = useState(false);
  // AI 起草初稿态（前端派生，不持久化）：草稿产自 AEP-110 响应即标「待完善/确认」。
  const [aiDrafted, setAiDrafted] = useState(false);
  // 模型拒绝起草时的理由（一等回执，不是报错气泡）；再次起草或换章节即清空。
  const [declinedReason, setDeclinedReason] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const editorRef = useRef<TextAreaRef>(null);
  // authored_text 章节支持 AI 起草（纯 boilerplate/条目装配章节不支持）。
  const canAiDraft = (section?.content_types ?? []).includes('authored_text');
  // 是否给「从目录选取」入口：判定在后端单点完成（标题像参考资料类 ∧ 支持人工撰稿），
  // 这里只读标志——前端再判一次就成了第二个口径。
  const canPickStandards = section?.standards_pickable === true;
  // 零依据提示：把模型多半会拒绝这件事提到点击之前。计数由后端给，口径同起草服务。
  const basisHint = canAiDraft
    ? draftBasisHint(draftBasis.find((b) => b.section_key === sectionKey))
    : null;
  // 打开时按当前撰稿（无撰稿则模板默认文本）预填
  const prefill = manuscript?.content ?? section?.boilerplate ?? '';
  useEffect(() => {
    if (sectionKey !== null) {
      setText(prefill);
      setAiDrafted(false);
      setDeclinedReason(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sectionKey]);

  const aiDraft = async () => {
    if (!sectionKey) return;
    setDrafting(true);
    setDeclinedReason(null);
    try {
      const draft = await onDraft(sectionKey);
      setText(draft);
      setAiDrafted(true);
      void message.success('AI 起草初稿已生成，请完善后保存并确认');
    } catch (error) {
      // 模型拒绝起草：理由原样进一等回执，不套通用报错气泡（气泡会在理由前拼上请求地址与
      // HTTP 状态码，把理由挤出视野）。其余错误仍走气泡。
      if (error instanceof ManuscriptDeclinedError) {
        setDeclinedReason(error.reason || '模型未给出具体理由');
      } else {
        void message.error(error instanceof Error ? error.message : String(error));
      }
    } finally {
      setDrafting(false);
    }
  };

  // 从目录选取的引用条目 → 按统一格式插进光标处，序号接着已有的往下排。
  const insertStandards = (picked: ReferenceStandardRead[]) => {
    const cursor = editorRef.current?.resizableTextArea?.textArea?.selectionStart ?? null;
    const lines = formatCitationLines(picked, nextCitationNumber(text));
    const next = insertCitations(text, lines, cursor);
    setText(next.text);
    setPickerOpen(false);
    // 焦点与光标回到插入内容之后：接着写不会顶开刚插入的引用行。
    window.requestAnimationFrame(() => {
      const area = editorRef.current?.resizableTextArea?.textArea;
      if (area) {
        area.focus();
        area.setSelectionRange(next.caret, next.caret);
      }
    });
    void message.success(`已插入 ${picked.length} 条引用`);
  };

  const save = async (content: string) => {
    if (!sectionKey) return;
    setSaving(true);
    try {
      await onSave(sectionKey, content);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Drawer
      title={section ? `撰稿：${section.number} ${section.title}` : '撰稿'}
      open={sectionKey !== null}
      width={560}
      onClose={onClose}
      destroyOnHidden
      data-testid="manuscript-drawer"
    >
      {section ? (
        <div className="pub-manuscript">
          {section.purpose ? (
            <Alert type="info" showIcon title={section.purpose} style={{ marginBottom: 12 }} />
          ) : null}
          <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
            此章节正文由人撰写并随文档生成（模板文本仅为默认预填稿）。支持占位符：
            <Typography.Text code>{'{project_name}'}</Typography.Text>
            <Typography.Text code>{'{coverage_scope}'}</Typography.Text>
            ；保存后点「重新生成」进入下一稿。
          </Typography.Paragraph>
          {aiDrafted ? (
            <Tag color="purple" style={{ marginBottom: 8 }} data-testid="manuscript-ai-draft-tag">
              AI 起草初稿 · 待完善/确认（保存后才成为撰稿正文）
            </Tag>
          ) : null}
          {declinedReason ? (
            <Alert
              closable
              showIcon
              type="warning"
              style={{ marginBottom: 8 }}
              data-testid="manuscript-draft-declined"
              title="AI 无法起草"
              description={declinedReason}
              onClose={() => setDeclinedReason(null)}
            />
          ) : null}
          <Input.TextArea
            ref={editorRef}
            rows={14}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="撰写本章节正文…"
            data-testid="manuscript-editor"
          />
          <Space style={{ marginTop: 12 }} wrap>
            {canPickStandards ? (
              <Tooltip title="从设置里的引用标准目录挑条目，按统一格式插入光标处；序号接着已有的往下排">
                <Button
                  icon={<BookOutlined />}
                  disabled={saving || busy}
                  onClick={() => setPickerOpen(true)}
                  data-testid="manuscript-pick-standards"
                >
                  从目录选取
                </Button>
              </Tooltip>
            ) : null}
            {canAiDraft ? (
              <Tooltip title="AI 依据章节说明＋关联确认态需求资产＋章节样例起草初稿，供人工完善确认">
                <Button
                  icon={<RobotOutlined />}
                  loading={drafting}
                  disabled={saving || busy}
                  onClick={() => void aiDraft()}
                  data-testid="manuscript-ai-draft"
                >
                  AI 起草
                </Button>
              </Tooltip>
            ) : null}
            {basisHint ? (
              <Text type="warning" style={{ fontSize: 12 }} data-testid="manuscript-basis-hint">
                {basisHint}
              </Text>
            ) : null}
            <Button type="primary" loading={saving || busy} onClick={() => void save(text)}>
              保存撰稿
            </Button>
            {manuscript ? (
              <Tooltip title="删除撰稿，回落模板默认文本">
                <Button danger loading={saving || busy} onClick={() => void save('')}>
                  恢复默认文本
                </Button>
              </Tooltip>
            ) : null}
            {manuscript ? (
              <Text type="secondary" style={{ fontSize: 12 }}>
                第 {manuscript.revision_no} 次修订 · {manuscript.updated_by}
              </Text>
            ) : null}
          </Space>
          <StandardPickerModal
            open={pickerOpen}
            onCancel={() => setPickerOpen(false)}
            onPick={insertStandards}
          />
        </div>
      ) : null}
    </Drawer>
  );
}

// ---- 引用标准选取器（T20260721：目录 → 撰稿正文的统一引用行）----

function StandardPickerModal({
  open,
  onCancel,
  onPick,
}: {
  open: boolean;
  onCancel: () => void;
  onPick: (picked: ReferenceStandardRead[]) => void;
}) {
  const [entries, setEntries] = useState<ReferenceStandardRead[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [keyword, setKeyword] = useState('');
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setSelectedKeys([]);
    setKeyword('');
    settingsApi
      .listReferenceStandards()
      .then((body) => {
        // 只取启用中的条目：被停用的内置条目仍在目录响应里（供设置页恢复），但不该能被引用。
        setEntries(body.entries.filter((e) => e.enabled));
        setLoadError(null);
      })
      .catch((error: unknown) => {
        setLoadError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => setLoading(false));
  }, [open]);

  const visible = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((e) =>
      `${e.code} ${e.title} ${e.issuer} ${e.note}`.toLowerCase().includes(needle),
    );
  }, [entries, keyword]);

  const confirm = () => {
    // 按目录顺序插入，而不是按勾选先后：目录顺序稳定（类别→标准号），两次选同样几条得到的
    // 引用清单一致。
    const picked = entries.filter((e) => selectedKeys.includes(e.key));
    if (picked.length > 0) {
      onPick(picked);
    }
  };

  return (
    <Modal
      destroyOnHidden
      open={open}
      width={720}
      title="从引用标准目录选取"
      okText={`插入 ${selectedKeys.length} 条`}
      cancelText="取消"
      okButtonProps={{ disabled: selectedKeys.length === 0, 'data-testid': 'standard-picker-ok' }}
      onCancel={onCancel}
      onOk={confirm}
    >
      {loadError ? (
        <Alert showIcon type="warning" title="引用标准目录加载失败" description={loadError} />
      ) : null}
      <Input
        allowClear
        prefix={<SearchOutlined />}
        placeholder="搜标准号、名称、发布机构"
        style={{ marginBottom: 12 }}
        value={keyword}
        data-testid="standard-picker-search"
        onChange={(e) => setKeyword(e.target.value)}
      />
      <Table<ReferenceStandardRead>
        columns={[
          {
            title: '标准号',
            dataIndex: 'code',
            width: '30%',
            render: (code: string) => <Text strong style={{ fontSize: 12 }}>{code}</Text>,
          },
          {
            title: '名称',
            dataIndex: 'title',
            render: (title: string, row) => (
              <div>
                <div style={{ fontSize: 12 }}>{title}</div>
                {row.note ? (
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {row.note}
                  </Text>
                ) : null}
              </div>
            ),
          },
          {
            title: '类别',
            dataIndex: 'category_label',
            width: '16%',
            render: (label: string) => <Tag>{label}</Tag>,
          },
        ]}
        dataSource={visible}
        data-testid="standard-picker-table"
        loading={loading}
        locale={{ emptyText: <Empty description="目录里没有可选的条目（可到设置 · 引用标准目录添加）" /> }}
        pagination={false}
        rowKey="key"
        scroll={{ y: 320 }}
        size="small"
        rowSelection={{
          selectedRowKeys: selectedKeys,
          onChange: (keys) => setSelectedKeys(keys as string[]),
        }}
      />
    </Modal>
  );
}

// ---- 候选预览 Drawer（AEP-099：预览即最终渲染）----

function CandidatePreviewDrawer({
  target,
  onClose,
  onLoad,
}: {
  target: CandidateRowVM | null;
  onClose: () => void;
  onLoad: (
    kind: 'requirement_item' | 'chart' | 'material',
    ref: string,
  ) => Promise<CandidatePreviewRead>;
}) {
  const [preview, setPreview] = useState<CandidatePreviewRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!target) return;
    let cancelled = false;
    setPreview(null);
    setError(null);
    onLoad(target.kind, target.ref)
      .then((read) => {
        if (!cancelled) setPreview(read);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.ref]);

  return (
    <Drawer
      title={target ? `预览：${target.no} ${target.title}` : '预览'}
      open={target !== null}
      width={560}
      onClose={onClose}
      destroyOnHidden
      data-testid="candidate-preview-drawer"
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        以下内容与生成稿同一确定性渲染器产出——预览即插入文档后的最终样子。
      </Typography.Paragraph>
      {error ? <Alert type="error" showIcon title={error} /> : null}
      {!preview && !error ? <Spin /> : null}
      {preview ? (
        <div className="md-render">
          <MarkdownPreview markdown={preview.markdown} />
        </div>
      ) : null}
    </Drawer>
  );
}

// ---- 顶部信息条 ----

function IndexInfoBar({
  header,
  templateText,
  coverageScope,
  onCoverageScopeChange,
}: {
  header: ReturnType<typeof buildIndexHeader>;
  templateText: string;
  coverageScope: string;
  onCoverageScopeChange: (value: string) => void;
}) {
  return (
    <div className="pub-infobar" data-testid="index-infobar">
      <span className="pub-infobar__field">
        <Text type="secondary">当前文档：</Text>
        <Text strong>{header.docTitle}</Text>
      </span>
      <span className="pub-infobar__field">
        <Text type="secondary">模板：</Text>
        <Text strong className="pub-infobar__template" title={templateText}>{templateText}</Text>
      </span>
      <span className="pub-infobar__field pub-infobar__scope">
        <Text type="secondary">发布范围：</Text>
        <Input
          size="small"
          placeholder="例如 release-v0.1 全部确认资产"
          value={coverageScope}
          onChange={(e) => onCoverageScopeChange(e.target.value)}
        />
      </span>
      <span className="pub-infobar__field">
        <Text type="secondary">当前状态：</Text>
        <Badge
          status={header.statusTone === 'danger' ? 'error' : header.statusTone === 'success' ? 'success' : 'processing'}
          text={header.statusText}
        />
      </span>
      <span className="pub-infobar__stats">
        <span className="pub-infobar__stat">条目总数 <b>{header.stats.total}</b></span>
        <span className="pub-infobar__stat pub-infobar__stat--ok">已确认 <b>{header.stats.confirmed}</b></span>
        <span className="pub-infobar__stat pub-infobar__stat--warn">待确认 <b>{header.stats.pending}</b></span>
        <span className="pub-infobar__stat pub-infobar__stat--danger">缺失槽位 <b>{header.stats.missingSlots}</b></span>
      </span>
    </div>
  );
}

// ---- 左栏：模板章节与槽位（可折叠树 + 必填进度）----

function SlotTreePanel({
  tree,
  collapsedGroups,
  onToggleGroup,
  onTemplatePreview,
  onChangeTemplate,
  onAuthor,
}: {
  tree: ReturnType<typeof buildSlotTree>;
  collapsedGroups: Set<string>;
  onToggleGroup: (key: string) => void;
  onTemplatePreview: () => void;
  onChangeTemplate: () => void;
  onAuthor: (sectionKey: string) => void;
}) {
  return (
    <section className="panel pub-slots" data-testid="template-sections">
      <div className="panel__header pub-slots__header">
        <h2 className="panel__title">模板章节与槽位</h2>
        <span className="pub-slots__actions">
          <Button size="small" onClick={onTemplatePreview}>预览</Button>
          <Button size="small" onClick={onChangeTemplate}>模板管理</Button>
        </span>
      </div>
      <div className="pub-slots__cols">
        <span>章节 / 槽位</span>
        <span>属性</span>
        <span>覆盖状态</span>
      </div>
      <div className="pub-slots__body">
        {tree.groups.map((group) => {
          const collapsed = collapsedGroups.has(group.key);
          return (
            <div key={group.key} className="pub-slots__group">
              <button
                type="button"
                className="pub-slots__group-head"
                onClick={() => onToggleGroup(group.key)}
              >
                <span className={collapsed ? 'pub-slots__caret pub-slots__caret--closed' : 'pub-slots__caret'}>▾</span>
                <span className="pub-slots__group-title">{group.number} {group.title}</span>
              </button>
              {!collapsed
                ? group.rows.map((row) => (
                    <div key={row.key} className="pub-slots__row" data-section={row.key}>
                      <Tooltip title={row.acceptTypeText}>
                        <span className="pub-slots__row-title">{row.number} {row.title}</span>
                      </Tooltip>
                      <span>
                        {row.requiredText !== '—' ? (
                          <Tag
                            color={row.requiredText === '必填' ? 'volcano' : 'default'}
                            className="pub-slots__tag"
                          >
                            {row.requiredText}
                          </Tag>
                        ) : null}
                      </span>
                      <span className="pub-slots__coverage">
                        <Tag color={TONE_COLOR[row.coverageTone]} className="pub-slots__tag">
                          {row.coverageText}
                        </Tag>
                        {row.authorable ? (
                          <Button
                            size="small"
                            type="link"
                            className="pub-slots__author"
                            data-testid={`author-${row.key}`}
                            onClick={() => onAuthor(row.key)}
                          >
                            撰稿
                          </Button>
                        ) : null}
                      </span>
                    </div>
                  ))
                : null}
            </div>
          );
        })}
      </div>
      <div className="pub-slots__progress" data-testid="required-progress">
        <span className="pub-slots__progress-text">
          必填槽位 {tree.requiredProgress.covered}/{tree.requiredProgress.total} 已满足
        </span>
        <span className="pub-slots__progress-bar">
          <i style={{ width: `${tree.requiredProgress.percent}%` }} />
        </span>
        {tree.requiredProgress.missing > 0 ? (
          <Tag color="red" className="pub-slots__tag">{tree.requiredProgress.missing} 缺失</Tag>
        ) : null}
      </div>
    </section>
  );
}

// ---- 中栏：候选表格 ----

function CandidateTable({
  rows,
  selectedRefs,
  emptyText,
  onToggle,
  onPreview,
}: {
  rows: CandidateRowVM[];
  selectedRefs: Set<string>;
  emptyText: string;
  onToggle: (row: CandidateRowVM) => void;
  onPreview: (row: CandidateRowVM) => void;
}) {
  return (
    <Table<CandidateRowVM>
      size="small"
      rowKey="ref"
      className="pub-pool__table"
      pagination={false}
      locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyText} /> }}
      rowSelection={{
        selectedRowKeys: rows.filter((r) => selectedRefs.has(r.ref)).map((r) => r.ref),
        onSelect: (row) => onToggle(row),
        onSelectAll: (selected, _rows, changed) => {
          for (const row of changed) {
            if (row && selectedRefs.has(row.ref) !== selected) onToggle(row);
          }
        },
      }}
      columns={[
        {
          title: 'ID',
          dataIndex: 'no',
          width: 92,
          render: (no: string) => <Text code className="pub-pool__id">{no}</Text>,
        },
        {
          title: '标题',
          dataIndex: 'title',
          ellipsis: { showTitle: false },
          render: (title: string) => (
            <Tooltip title={title} placement="topLeft">
              <span>{title}</span>
            </Tooltip>
          ),
        },
        { title: '类型', dataIndex: 'typeText', width: 72 },
        {
          title: '确认状态',
          dataIndex: 'statusText',
          width: 80,
          render: (_: string, row) => (
            <Tag color={TONE_COLOR[row.statusTone]} className="pub-slots__tag">{row.statusText}</Tag>
          ),
        },
        {
          title: '来源数',
          dataIndex: 'sourceCount',
          width: 56,
          render: (value: number | null) => (value ?? '—'),
        },
        {
          title: '准入结果',
          dataIndex: 'admissionText',
          width: 80,
          render: (_: string, row) => (
            <Tag color={TONE_COLOR[row.admissionTone]} className="pub-slots__tag">{row.admissionText}</Tag>
          ),
        },
        {
          title: '预览',
          key: 'preview',
          width: 56,
          render: (_: unknown, row) => (
            <Button size="small" type="link" onClick={() => onPreview(row)}>
              预览
            </Button>
          ),
        },
      ]}
      dataSource={rows}
    />
  );
}

// ---- 中栏：追溯依据只读卡 ----

function TraceReadonlyCard({
  summary,
}: {
  summary: PublicationWorkspaceRead['candidates']['trace_summary'];
}) {
  return (
    <div className="pub-pool__trace" data-testid="trace-readonly">
      <Alert
        type="info"
        showIcon
        title="追溯依据不进入文档内容"
        description="资产与文档的追溯绑定在保存索引/定稿时由系统按索引自动建立（派生关系），不在此勾选；可到追溯分析工作台查看关系网并预览资产在文档中的片段。"
      />
      {summary ? (
        <div className="pub-pool__trace-stats">
          <span className="pub-infobar__stat pub-infobar__stat--ok">有效 <b>{summary.effective}</b></span>
          <span className="pub-infobar__stat">预建立 <b>{summary.pre_established}</b></span>
          <span className="pub-infobar__stat pub-infobar__stat--warn">可疑 <b>{summary.suspect}</b></span>
        </div>
      ) : null}
    </div>
  );
}

// ---- 右栏：已编排索引（槽位分组 + 拖拽/上移/下移/移除/换槽位 + 缺失清单）----

function ArrangedIndexPanel({
  groups,
  missingList,
  activeSlotKey,
  onFocusSlot,
  onMove,
  onRemove,
  onChangeSlot,
  onReorderTo,
  onClear,
}: {
  groups: ArrangedSlotGroupVM[];
  missingList: MissingRowVM[];
  activeSlotKey: string | null;
  onFocusSlot: (group: ArrangedSlotGroupVM) => void;
  onMove: (sectionKey: string, ref: string, delta: number) => void;
  onRemove: (ref: string) => void;
  onChangeSlot: (ref: string, sectionKey: string) => void;
  onReorderTo: (sectionKey: string, ref: string, targetIndex: number) => void;
  onClear: () => void;
}) {
  const [dragPayload, setDragPayload] = useState<DragPayload | null>(null);
  const [dropHint, setDropHint] = useState<string | null>(null);
  const hasEntries = groups.some((g) => g.entries.length > 0);
  // 阻断行计数恒等于底栏「缺失槽位」；非阻断提示（如知识整表投影为空）单列，不冒充必填缺失
  const blockingMissing = missingList.filter((m) => m.blocking);
  const nonBlockingMissing = missingList.filter((m) => !m.blocking);

  const acceptsDrag = (group: ArrangedSlotGroupVM): boolean => {
    if (!dragPayload) return false;
    if (group.sectionKey === dragPayload.sectionKey) return true;
    // 跨组拖拽 = 换槽位：目标槽位须兼容该资产类型
    const source = groups
      .find((g) => g.sectionKey === dragPayload.sectionKey)
      ?.entries.find((e) => e.assetRef === dragPayload.assetRef);
    return source?.slotOptions.some((o) => o.key === group.sectionKey) ?? false;
  };

  const handleDrop = (group: ArrangedSlotGroupVM, targetIndex: number) => {
    if (!dragPayload) return;
    if (group.sectionKey === dragPayload.sectionKey) {
      onReorderTo(group.sectionKey, dragPayload.assetRef, targetIndex);
    } else if (acceptsDrag(group)) {
      onChangeSlot(dragPayload.assetRef, group.sectionKey);
    }
    setDragPayload(null);
    setDropHint(null);
  };

  return (
    <section className="panel pub-arranged" data-testid="arranged-index">
      <div className="panel__header pub-arranged__header">
        <h2 className="panel__title">已编排索引</h2>
        <span className="pub-slots__actions">
          <Button
            size="small"
            disabled={!hasEntries}
            onClick={() =>
              Modal.confirm({
                title: '清空已编排索引？',
                content: '将移除当前草稿中的全部编排条目（未保存前不影响已保存索引）。',
                okText: '清空',
                okButtonProps: { danger: true },
                cancelText: '取消',
                onOk: onClear,
              })
            }
          >
            清空
          </Button>
        </span>
      </div>
      <div className="pub-arranged__hint">
        <Text type="secondary">按模板槽位编排（拖拽把手可调整顺序，跨槽位拖拽 = 换槽位）</Text>
      </div>
      <div className="pub-arranged__body">
        {groups.map((group) => (
          <div
            key={group.sectionKey}
            className={
              dropHint === group.sectionKey && acceptsDrag(group)
                ? 'pub-arranged__group pub-arranged__group--drop'
                : 'pub-arranged__group'
            }
            onDragOver={(e: DragEvent) => {
              if (acceptsDrag(group)) {
                e.preventDefault();
                setDropHint(group.sectionKey);
              }
            }}
            onDragLeave={() => setDropHint((h) => (h === group.sectionKey ? null : h))}
            onDrop={() => handleDrop(group, group.entries.length)}
          >
            <div className="pub-arranged__group-head">
              <span className="pub-arranged__group-title">
                {group.number} {group.title}
                <Tag
                  color={group.requiredText === '必填' ? 'volcano' : 'default'}
                  className="pub-slots__tag"
                >
                  {group.requiredText}
                </Tag>
              </span>
              <span className="pub-arranged__group-side">
                <Button
                  size="small"
                  type={activeSlotKey === group.sectionKey ? 'primary' : 'link'}
                  className="pub-arranged__add"
                  onClick={() => onFocusSlot(group)}
                >
                  + 添加到此槽位
                </Button>
                <Tag color={TONE_COLOR[group.badgeTone]} className="pub-slots__tag">
                  {group.badgeText}
                </Tag>
              </span>
            </div>
            {group.entries.map((entry, index) => (
              <div
                key={entry.assetRef}
                className="pub-arranged__row"
                draggable
                onDragStart={() =>
                  setDragPayload({
                    sectionKey: group.sectionKey,
                    assetRef: entry.assetRef,
                    assetType: entry.assetType,
                    reqType: null,
                  })
                }
                onDragEnd={() => {
                  setDragPayload(null);
                  setDropHint(null);
                }}
                onDragOver={(e: DragEvent) => {
                  if (dragPayload && dragPayload.sectionKey === group.sectionKey) {
                    e.preventDefault();
                    e.stopPropagation();
                  }
                }}
                onDrop={(e) => {
                  e.stopPropagation();
                  handleDrop(group, index);
                }}
              >
                <span className="pub-arranged__order">{index + 1}</span>
                <span className="pub-arranged__handle" title="拖拽调整顺序">⋮⋮</span>
                <span className="pub-arranged__main">
                  <Text code className="pub-pool__id">{entry.no}</Text>
                  <Tooltip title={entry.title} placement="topLeft">
                    <span className="pub-arranged__title">{entry.title}</span>
                  </Tooltip>
                </span>
                <span className="pub-arranged__meta">
                  <span className="pub-arranged__type">{entry.typeText}</span>
                  <Tag color={TONE_COLOR[entry.statusTone]} className="pub-slots__tag">
                    {entry.statusText}
                  </Tag>
                </span>
                <span className="pub-arranged__ops">
                  <Button
                    size="small"
                    type="text"
                    disabled={index === 0}
                    onClick={() => onMove(group.sectionKey, entry.assetRef, -1)}
                  >
                    上移
                  </Button>
                  <Button
                    size="small"
                    type="text"
                    disabled={index === group.entries.length - 1}
                    onClick={() => onMove(group.sectionKey, entry.assetRef, 1)}
                  >
                    下移
                  </Button>
                  <Button size="small" type="text" danger onClick={() => onRemove(entry.assetRef)}>
                    移除
                  </Button>
                  <Select
                    size="small"
                    variant="borderless"
                    value={null}
                    placeholder="换槽位"
                    disabled={entry.slotOptions.length === 0}
                    popupMatchSelectWidth={false}
                    className="pub-arranged__slot-select"
                    options={entry.slotOptions.map((o) => ({ value: o.key, label: o.label }))}
                    onChange={(key) => {
                      if (key) onChangeSlot(entry.assetRef, key);
                    }}
                  />
                </span>
              </div>
            ))}
            {group.entries.length === 0 ? (
              <div className="pub-arranged__empty">
                <Text type="secondary">尚未编排：点击「+ 添加到此槽位」或在候选池勾选</Text>
              </div>
            ) : null}
          </div>
        ))}
      </div>
      {blockingMissing.length > 0 ? (
        <div className="pub-missing-card" data-testid="missing-list">
          <div className="pub-missing-card__title">缺失清单（{blockingMissing.length}）</div>
          {blockingMissing.map((missing) => (
            <div key={missing.section_key} className="pub-missing-card__row">
              <div className="pub-missing-card__head">
                <Text strong className="pub-missing-card__section">{missing.section_title}</Text>
                <Tag color="red" className="pub-slots__tag">必填缺失</Tag>
              </div>
              <div className="pub-missing-card__reason">{missing.reason}</div>
              <div className="pub-missing-card__rebuild">补建入口：{missing.rebuild_entry}</div>
            </div>
          ))}
        </div>
      ) : null}
      {nonBlockingMissing.length > 0 ? (
        <div className="pub-missing-card pub-missing-card--muted" data-testid="non-blocking-list">
          <div className="pub-missing-card__title">非阻断提示（{nonBlockingMissing.length}）</div>
          {nonBlockingMissing.map((missing) => (
            <div key={missing.section_key} className="pub-missing-card__row">
              <div className="pub-missing-card__head">
                <Text strong className="pub-missing-card__section">{missing.section_title}</Text>
                <Tag className="pub-slots__tag">非阻断</Tag>
              </div>
              <div className="pub-missing-card__reason">{missing.reason}</div>
              <div className="pub-missing-card__rebuild">补建入口：{missing.rebuild_entry}</div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
