import { Alert, Button, Checkbox, Collapse, ConfigProvider, Input, Modal, Popconfirm, Segmented, Select, Spin, Switch, Table, Tag, message } from 'antd';
import {
  ApiOutlined,
  BarChartOutlined,
  BookOutlined,
  BgColorsOutlined,
  CheckCircleFilled,
  CloseCircleFilled,
  ExportOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  InfoCircleFilled,
  SafetyOutlined,
  SnippetsOutlined,
  UserOutlined,
  WarningFilled,
} from '@ant-design/icons';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { WorkbenchFrame } from './WorkbenchFrame';
import '../styles-settings-providers.css';
import '../styles-capability.css';
import { DocumentTemplatePanel } from './DocumentTemplatePanel';
import {
  settingsApi,
  type ConfigDomainRead,
  type ConfigDomainStatusRead,
  type ConnectionTestLevel,
  type LlmProviderListRead,
  type ModelCapabilityProbeResult,
  type ReferenceStandardCatalogRead,
} from '../api/settings';
import { projectsApi, type ProjectDetailRead } from '../api/projects';
import { templatesApi } from '../api/templates';
import {
  baseUrlHint,
  buildCapabilityRows,
  buildConventionCatalog,
  buildDomainForm,
  buildExportReadiness,
  buildSettingsMenu,
  buildStandardRows,
  buildThinkingMode,
  capabilityProbeStamp,
  catalogStamp,
  connectionResultText,
  emptyProviderDraft,
  emptyStandardDraft,
  providerDraftFrom,
  providerDraftToWrite,
  providerListStamp,
  resolveTestKeyUsage,
  thinkingFactsFromItems,
  thinkingFactsFromProfile,
  standardDraftFrom,
  standardDraftToWrite,
  toSaveValues,
  formatUpdatedStamp,
  validateDomainValues,
  validateProviderDrafts,
  validateStandardDrafts,
  type ConnectionResultVM,
  type ConventionCatalogVM,
  type ExportReadinessRowVM,
  type ExportReadinessVM,
  type ProviderDraftVM,
  type StandardDraftVM,
  type StandardRowVM,
  type SettingsDomainFormVM,
  type SettingsDomainKey,
} from '../view-models/settings';
import { FONT_SCALE_OPTIONS, THEME_SCHEMES, antdThemeFor, useTheme, type FontScale, type ThemeKey } from '../ui/theme';

/**
 * 设置工作台（配置管理入口，04A §9 / UINV-19）：左配置域菜单 / 右配置面板两区。
 * 支撑能力配置只写配置、不写治理事实，保存经下游单元生效并留痕；
 * 外观域（§9.1 / UINV-26）为浏览器本地偏好，选择即时生效、无保存动作、无后端调用。
 */

const DOMAIN_ICONS: Record<SettingsDomainKey, ReactNode> = {
  users: <UserOutlined aria-hidden="true" />,
  model_service: <ApiOutlined aria-hidden="true" />,
  export: <ExportOutlined aria-hidden="true" />,
  chart_rendering: <BarChartOutlined aria-hidden="true" />,
  document_template: <SnippetsOutlined aria-hidden="true" />,
  requirement_convention: <FileTextOutlined aria-hidden="true" />,
  reference_standards: <BookOutlined aria-hidden="true" />,
  project: <FolderOpenOutlined aria-hidden="true" />,
  appearance: <BgColorsOutlined aria-hidden="true" />,
};

// 公共写作规范折叠面（方案无关静态摘要，来自选型文档 §4；非规约方案文案，故不走 AEP-102）。
const COMMON_WRITING_NORMS: { title: string; lines: string[] }[] = [
  {
    title: '模态词规范（GB/T 1.1-2020）',
    lines: [
      '应 / 不应：强制性要求；宜 / 不宜：推荐性要求；可：允许性要求；不得：禁止性要求',
      '禁止混用：必须、应该、需要、最好、尽量、原则上、一般情况下',
    ],
  },
  {
    title: '质量规则 Q1–Q7',
    lines: [
      'Q1 主体明确 · Q2 动作具体（禁「支持/处理/管理/优化」类泛动词） · Q3 对象明确',
      'Q4 条件明确 · Q5 质量量化 · Q6 一条一动作（禁复合需求） · Q7 模态词统一',
    ],
  },
];

interface SettingsWorkbenchProps {
  operatorRef: string;
  /** 深链初始域（如发布空态「前往设置 › 文档模板」）；缺省落模型服务域。 */
  initialDomain?: SettingsDomainKey;
  /** 当前项目（项目危险区展示与删除对象，AEP-113）。 */
  selectedProject?: ProjectDetailRead | null;
  /** 删除成功回调：App 刷新项目列表并切换到剩余项目/空态。 */
  onProjectDeleted?: (deletedProjectId: string) => Promise<void> | void;
}

export function SettingsWorkbench({
  operatorRef,
  initialDomain,
  selectedProject,
  onProjectDeleted,
}: SettingsWorkbenchProps) {
  const [statuses, setStatuses] = useState<ConfigDomainStatusRead[] | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [selectedDomain, setSelectedDomain] = useState<SettingsDomainKey>(initialDomain ?? 'model_service');
  // 文档模板域状态签由已启用模板数派生（非 config_registry）；随定制/登记/停用变化刷新。
  const [templateCount, setTemplateCount] = useState<number | undefined>();

  const refreshStatuses = useCallback(async () => {
    try {
      setStatuses(await settingsApi.listDomains());
      setStatusError(null);
    } catch (error) {
      setStatuses(null);
      setStatusError(error instanceof Error ? error.message : String(error));
    }
  }, []);

  const refreshTemplateCount = useCallback(async () => {
    try {
      const rows = await templatesApi.list();
      // 每个 template_key 取最新 active 计一个「可用」。
      const activeKeys = new Set(rows.filter((r) => r.status === 'active').map((r) => r.template_key));
      setTemplateCount(activeKeys.size);
    } catch {
      setTemplateCount(undefined);
    }
  }, []);

  useEffect(() => {
    void refreshStatuses();
    void refreshTemplateCount();
  }, [refreshStatuses, refreshTemplateCount]);

  const menuGroups = useMemo(
    () => buildSettingsMenu(statuses, { documentTemplateCount: templateCount }),
    [statuses, templateCount],
  );

  return (
    <WorkbenchFrame title="设置工作台">
      <div className="settings-pill-row">
        <span className="settings-pill">只写配置 + 本地偏好，不写治理事实</span>
        <span className="settings-head-note">
          <InfoCircleFilled aria-hidden="true" />
          配置变更经下游单元生效并留痕；外观仅影响本浏览器显示
        </span>
      </div>
      <div className="settings-layout">
        <section aria-label="配置域菜单" className="panel settings-menu-panel">
          <div className="panel__header">
            <h2 className="panel__title">配置域菜单</h2>
          </div>
          <div className="panel__body">
            {statusError ? (
              <Alert showIcon title="配置域状态加载失败" description={statusError} type="warning" />
            ) : null}
            {menuGroups.map((group) => (
              <div key={group.title}>
                <div className="settings-menu-group">{group.title}</div>
                {group.items.map((item) => (
                  <button
                    key={item.key}
                    className={
                      item.key === selectedDomain
                        ? 'settings-menu-item settings-menu-item--selected'
                        : 'settings-menu-item'
                    }
                    data-testid={`settings-domain-${item.key}`}
                    type="button"
                    onClick={() => setSelectedDomain(item.key)}
                  >
                    <span className="settings-menu-item__icon">{DOMAIN_ICONS[item.key]}</span>
                    <span className="settings-menu-item__label">{item.label}</span>
                    <span className={`settings-status-chip settings-status-chip--${item.statusTone}`}>
                      {item.statusText}
                    </span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        </section>
        {selectedDomain === 'appearance' ? (
          <AppearancePanel />
        ) : selectedDomain === 'project' ? (
          <ProjectDangerPanel
            operatorRef={operatorRef}
            selectedProject={selectedProject ?? null}
            onProjectDeleted={onProjectDeleted}
          />
        ) : selectedDomain === 'users' ? (
          <UsersPanel />
        ) : selectedDomain === 'document_template' ? (
          <DocumentTemplatePanel key={selectedDomain} operatorRef={operatorRef} onTemplatesChanged={refreshTemplateCount} />
        ) : selectedDomain === 'requirement_convention' ? (
          <RequirementConventionPanel key={selectedDomain} operatorRef={operatorRef} onSaved={refreshStatuses} />
        ) : selectedDomain === 'model_service' ? (
          <ModelProviderPanel key={selectedDomain} operatorRef={operatorRef} onSaved={refreshStatuses} />
        ) : selectedDomain === 'reference_standards' ? (
          <ReferenceStandardsPanel key={selectedDomain} operatorRef={operatorRef} onSaved={refreshStatuses} />
        ) : (
          <CapabilityDomainPanel
            key={selectedDomain}
            domain={selectedDomain}
            operatorRef={operatorRef}
            onSaved={refreshStatuses}
          />
        )}
      </div>
    </WorkbenchFrame>
  );
}

// ---- 用户与权限域：03/10 范围内最小呈现，明示待接入，不显示假数据 ----

function UsersPanel() {
  return (
    <section aria-label="用户与权限配置" className="panel settings-domain-panel">
      <div className="panel__header">
        <h2 className="panel__title">用户与权限</h2>
        <span className="settings-status-chip settings-status-chip--pending">待接入</span>
      </div>
      <div className="panel__body">
        <div className="settings-hint-bar">
          <InfoCircleFilled aria-hidden="true" />
          <span>
            本域承接用户、角色与权限的配置维护，下游单元为 <b>身份权限</b>；按 03/10
            的范围口径为配置维护入口，非完整企业权限体系。当前 release 尚未接入用户与权限数据，本面板不展示演示数据。
          </span>
        </div>
        <div className="settings-note">
          <SafetyOutlined aria-hidden="true" />
          <span>
            <b>边界：</b>权限配置只约束“谁能修改配置”，不写治理事实；接入后所有变更经 <b>身份权限</b>
            生效并由审计留痕记录。
          </span>
        </div>
      </div>
    </section>
  );
}

// ---- 项目危险区（AEP-113）：删除当前项目 = 级联删净 + 跨项目零误删；GitHub 式输入项目名确认 ----

interface ProjectDangerPanelProps {
  operatorRef: string;
  selectedProject: ProjectDetailRead | null;
  onProjectDeleted?: (deletedProjectId: string) => Promise<void> | void;
}

function ProjectDangerPanel({ operatorRef, selectedProject, onProjectDeleted }: ProjectDangerPanelProps) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [nameInput, setNameInput] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [messageApi, messageHolder] = message.useMessage();

  const nameMatches = selectedProject != null && nameInput === selectedProject.name;

  const handleDelete = async () => {
    if (!selectedProject || !nameMatches) {
      return;
    }
    setDeleting(true);
    try {
      const result = await projectsApi.deleteProject(selectedProject.id, operatorRef);
      messageApi.success(`项目「${result.project_name}」已删除（清除 ${result.deleted_rows} 行关联数据）`);
      setConfirmOpen(false);
      setNameInput('');
      await onProjectDeleted?.(selectedProject.id);
    } catch (error) {
      // 409（在飞 AI 任务）/404 就地展示后端口径，不关弹层。
      messageApi.error(`删除失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <section aria-label="项目管理" className="panel settings-domain-panel" data-testid="settings-project-panel">
      {messageHolder}
      <div className="panel__header">
        <h2 className="panel__title">项目管理</h2>
      </div>
      <div className="panel__body">
        {selectedProject ? (
          <>
            <div className="settings-form-sec">
              <div className="settings-form-sec-title">当前项目</div>
              <div className="settings-hint-bar">
                <InfoCircleFilled aria-hidden="true" />
                <span>
                  <b>{selectedProject.name}</b>
                  {selectedProject.scope ? ` · ${selectedProject.scope}` : ''}
                  {selectedProject.domain_profile_label ? ` · 领域：${selectedProject.domain_profile_label}` : ''}
                </span>
              </div>
            </div>
            <div className="settings-form-sec">
              <div className="settings-form-sec-title">危险区</div>
              <Alert
                showIcon
                type="error"
                title="删除项目（不可恢复）"
                description="删除后该项目的材料、要素、条目、评审记录、图表、追溯关系、文档与导出件等全部关联数据将被清除；其它项目与全局配置不受影响。项目内有进行中的 AI 任务时将拒绝删除。"
              />
              <div className="settings-form-actions">
                <Button
                  danger
                  data-testid="project-delete-open"
                  type="primary"
                  onClick={() => {
                    setNameInput('');
                    setConfirmOpen(true);
                  }}
                >
                  删除此项目
                </Button>
              </div>
            </div>
            <Modal
              destroyOnHidden
              okButtonProps={{ danger: true, disabled: !nameMatches }}
              okText="我已知晓后果，删除"
              cancelText="取消"
              confirmLoading={deleting}
              open={confirmOpen}
              title={
                <span>
                  <WarningFilled aria-hidden="true" style={{ color: '#cf1322', marginRight: 8 }} />
                  确认删除项目
                </span>
              }
              onCancel={() => setConfirmOpen(false)}
              onOk={() => void handleDelete()}
            >
              <p>
                此操作<b>不可恢复</b>：将级联清除项目「{selectedProject.name}」的全部关联数据（含落盘导出件）。
              </p>
              <p>
                请输入项目名 <b>{selectedProject.name}</b> 以确认：
              </p>
              <Input
                autoFocus
                data-testid="project-delete-name-input"
                placeholder={selectedProject.name}
                status={nameInput && !nameMatches ? 'error' : undefined}
                value={nameInput}
                onChange={(event) => setNameInput(event.target.value)}
              />
            </Modal>
          </>
        ) : (
          <div className="settings-hint-bar">
            <InfoCircleFilled aria-hidden="true" />
            <span>暂无选中项目：请先在顶栏选择或创建项目。</span>
          </div>
        )}
        <div className="settings-note">
          <SafetyOutlined aria-hidden="true" />
          <span>
            <b>边界：</b>删除按应用层删除计划在单事务内执行并留结构化日志；全局配置、模板库与其它项目数据一行不动。
          </span>
        </div>
      </div>
    </section>
  );
}

// ---- 模型服务域：多 provider 列表 + 启用指针 + 两级连通测试 ----

interface ModelProviderPanelProps {
  operatorRef: string;
  onSaved: () => Promise<void> | void;
}

function ModelProviderPanel({ operatorRef, onSaved }: ModelProviderPanelProps) {
  const [read, setRead] = useState<LlmProviderListRead | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<ProviderDraftVM[]>([]);
  const [activeId, setActiveId] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testingLevel, setTestingLevel] = useState<ConnectionTestLevel | null>(null);
  const [testResult, setTestResult] = useState<ConnectionResultVM | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<ModelCapabilityProbeResult | null>(null);
  // 测试是异步的：发起时记下这条 provider 的 id，回来时只有当前仍选中同一条才写结果，
  // 否则切到别的 provider 后，前一条的测试结果会错误地显示在后一条表单下（结果错归属）。
  const inFlightProviderId = useRef<string | null>(null);
  const [messageApi, messageHolder] = message.useMessage();

  const load = useCallback(async () => {
    try {
      const body = await settingsApi.listProviders();
      setRead(body);
      setDrafts(body.providers.map(providerDraftFrom));
      setActiveId(body.active_provider_id);
      setSelectedIndex(0);
      setDirty(false);
      setLoadError(null);
    } catch (error) {
      setRead(null);
      setLoadError(error instanceof Error ? error.message : String(error));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const typeOptions = useMemo(
    () => (read?.provider_types ?? []).map((t) => ({ value: t.key, label: t.label })),
    [read],
  );
  const typeLabel = useCallback(
    (key: string) => read?.provider_types.find((t) => t.key === key)?.label ?? key,
    [read],
  );
  const typeHint = useCallback(
    (key: string) => read?.provider_types.find((t) => t.key === key)?.description ?? '',
    [read],
  );

  const selected = drafts[selectedIndex] ?? null;

  // 能力清单与思考模式说明的取值口径：这一轮刚探的结论优先，没探过就读已保存的档案——
  // 两个来源同一套字段，说明文字只写一份（buildCapabilityRows / buildThinkingMode）。
  const capabilityRows = useMemo(
    () => (probeResult
      ? buildCapabilityRows(probeResult.items, selected?.model.trim() ?? '',
                            selected?.providerType ?? '')
      : []),
    [probeResult, selected],
  );
  const thinkingFacts = useMemo(
    () => (probeResult
      ? thinkingFactsFromItems(probeResult.items)
      : thinkingFactsFromProfile(selected?.capabilityProfile)),
    [probeResult, selected],
  );
  const thinkingMode = useMemo(
    () => buildThinkingMode(thinkingFacts, selected?.thinkingEnabled ?? false,
                            selected?.providerType ?? ''),
    [thinkingFacts, selected],
  );
  // 这一轮探测有没有问出可用的结论。没有的话，「应用」只会把上次探明的档案冲成一份全「没探明」。
  const probeConclusive = probeResult?.ok === true;
  const probeStamp = capabilityProbeStamp(probeResult?.probed_at);

  // 切换选中项、编辑字段、增删条目都视为「放弃当前在途测试」：把在途标记清空，让还没回来的那次
  // 测试结果被丢弃（不会错误地显示到另一条 provider 下），同时清掉转圈与已显示的结果。
  const abandonInFlightTest = () => {
    inFlightProviderId.current = null;
    setTestingLevel(null);
    // 转圈标志也要一起清：探测函数的收尾只在「在途标记仍是本次」时才复位它，而这里刚把在途标记
    // 清空，那个守卫此后恒不成立。漏了这一行，探测按钮会一直转圈、两个连通测试按钮一直置灰，
    // 只能靠切走配置域再切回来重挂面板才能恢复，代价是当前未保存的编辑全丢。
    setProbing(false);
    setTestResult(null);
    // 探测结论是对「这个地址上的这个模型」下的：地址或模型一改，上一轮结论就不再成立，
    // 留在屏幕上会让人以为它还算数。已经保存进配置的档案不受影响，只清屏幕上这一份。
    setProbeResult(null);
  };

  // 测试与探测的结论是对「这个地址上的这个模型」下的，改了这几个字段结论才失效。
  // 改名字、超时、并发、思考开关都不影响「探到了什么」——那些编辑不该把清单清掉，
  // 否则用户刚探完、顺手拨一下开关，探测结果就没了，只能重探一遍。
  const PROBE_TARGET_KEYS: (keyof ProviderDraftVM)[] = [
    'baseUrl', 'model', 'providerType', 'apiKeyInput', 'clearApiKey',
  ];

  const patchSelected = (patch: Partial<ProviderDraftVM>) => {
    setDrafts((prev) => prev.map((d, i) => (i === selectedIndex ? { ...d, ...patch } : d)));
    setDirty(true);
    if (Object.keys(patch).some((key) => PROBE_TARGET_KEYS.includes(key as keyof ProviderDraftVM))) {
      abandonInFlightTest();
    }
  };

  const handleAdd = () => {
    setDrafts((prev) => [...prev, emptyProviderDraft(read?.provider_types[0]?.key ?? 'llama_cpp')]);
    setSelectedIndex(drafts.length);
    setDirty(true);
    abandonInFlightTest();
  };

  const handleRemove = (index: number) => {
    const removed = drafts[index];
    setDrafts((prev) => prev.filter((_, i) => i !== index));
    if (removed && removed.id === activeId) {
      const next = drafts.filter((_, i) => i !== index)[0];
      setActiveId(next?.id ?? '');
    }
    setSelectedIndex((prev) => (prev >= index && prev > 0 ? prev - 1 : prev));
    setDirty(true);
    abandonInFlightTest();
  };

  const handleActivate = (draft: ProviderDraftVM) => {
    // 新增的一条在草稿里就有 id，因此不必先保存再来设——保存时一并生效。
    setActiveId(draft.id);
    setDirty(true);
  };

  const handleSave = async () => {
    const problem = validateProviderDrafts(drafts);
    if (problem) {
      messageApi.warning(problem);
      return;
    }
    setSaving(true);
    try {
      const body = await settingsApi.saveProviders({
        providers: drafts.map(providerDraftToWrite),
        active_provider_id: activeId || null,
        operator_ref: operatorRef,
      });
      setRead(body);
      setDrafts(body.providers.map(providerDraftFrom));
      setActiveId(body.active_provider_id);
      setSelectedIndex((prev) => Math.min(prev, Math.max(body.providers.length - 1, 0)));
      setDirty(false);
      messageApi.success('已保存，下一次 AI 调用即使用启用中的模型服务');
      await onSaved();
    } catch (error) {
      messageApi.error(`保存失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSaving(false);
    }
  };

  /** 逐能力探测：一次问完六项，结果只显示不落库——要落库得点「应用探测结果」。 */
  const handleProbe = async () => {
    if (!selected || probing || testingLevel !== null) {
      return;
    }
    if (!selected.baseUrl.trim()) {
      messageApi.warning('请先填服务地址');
      return;
    }
    if (!selected.model.trim()) {
      messageApi.warning('请先填模型标识');
      return;
    }
    const startedId = selected.id;
    inFlightProviderId.current = startedId;
    const stillCurrent = () => inFlightProviderId.current === startedId;

    const savedBaseUrl = read?.providers.find((p) => p.id === selected.id)?.base_url ?? null;
    const typed = selected.apiKeyInput.trim();
    const keyUsage = resolveTestKeyUsage({
      typedKey: typed,
      apiKeySet: selected.apiKeySet,
      draftBaseUrl: selected.baseUrl,
      savedBaseUrl,
    });
    if (keyUsage.savedKeyBlockedHint) {
      messageApi.info(keyUsage.savedKeyBlockedHint);
    }

    setProbing(true);
    setProbeResult(null);
    try {
      const result = await settingsApi.probeModelCapabilities({
        base_url: selected.baseUrl.trim(),
        model: selected.model.trim(),
        provider_type: selected.providerType,
        provider_id: selected.id || null,
        api_key: typed || null,
        use_saved_key: keyUsage.useSavedKey,
        // 探测要发六七条请求，其中关思考那两条对着思考模型可能各跑数十秒：给足预算，
        // 后端对单条请求另有 20 秒上限，超时的那一项按「没探明」记，不会把整轮拖死。
        timeout_seconds: 60,
      });
      if (stillCurrent()) {
        setProbeResult(result);
      }
    } catch (error) {
      if (stillCurrent()) {
        messageApi.error(`探测没能完成：${error instanceof Error ? error.message : String(error)}`);
      }
    } finally {
      // 转圈与在途标记是两件事，不共用一个守卫：转圈说的是「这次请求跑完没有」，跑完就该停，
      // 与结果还认不认无关；在途标记说的是「这次请求的结果还算不算数」，只有仍是本次才清。
      setProbing(false);
      if (inFlightProviderId.current === startedId) {
        inFlightProviderId.current = null;
      }
    }
  };

  /** 应用探测结果：把能力档案并入这条模型服务并立即保存（会一并保存当前未保存的编辑）。 */
  const handleApplyProfile = async () => {
    // probeResult.ok 为假=这一轮连「能不能回话」都没过，六项全是「没探明」。此时应用等于把上次
    // 探明的档案清成一份空结论，上下文钳制随之失效、结构化输出档位回落运行时试探。
    if (!selected || !probeResult || !probeConclusive) {
      return;
    }
    const next = drafts.map((d, i) =>
      (i === selectedIndex
        ? { ...d, capabilityProfile: probeResult.profile, capabilityProfileChanged: true }
        : d));
    const problem = validateProviderDrafts(next);
    if (problem) {
      messageApi.warning(problem);
      return;
    }
    setDrafts(next);
    setSaving(true);
    try {
      const body = await settingsApi.saveProviders({
        providers: next.map(providerDraftToWrite),
        active_provider_id: activeId || null,
        operator_ref: operatorRef,
      });
      setRead(body);
      setDrafts(body.providers.map(providerDraftFrom));
      setActiveId(body.active_provider_id);
      setDirty(false);
      messageApi.success('已应用并保存，下一次 AI 调用即按探到的参数走');
      await onSaved();
    } catch (error) {
      messageApi.error(`应用失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async (level: ConnectionTestLevel) => {
    if (!selected) {
      return;
    }
    // 已有一次测试在途就不再发第二次：两个测试按钮共用一个在途门（testingLevel），
    // 否则两次并发会互相清对方的转圈、后回来的抢走结果显示。
    if (testingLevel !== null || probing) {
      return;
    }
    if (!selected.baseUrl.trim()) {
      messageApi.warning('请先填服务地址');
      return;
    }
    if (level === 'generation' && !selected.model.trim()) {
      messageApi.warning('请先填模型标识');
      return;
    }
    const startedId = selected.id;
    inFlightProviderId.current = startedId;
    // 结果回来时只认在途标记：切走 / 编辑 / 增删都会把它清掉或改成别的 id，从而丢弃过期结果。
    const stillCurrent = () => inFlightProviderId.current === startedId;

    // 已存密钥只对保存时的地址有效：草稿地址被改过时不带已存密钥（否则后端 400），
    // 并提示用户为何没用上已存密钥。已存地址取自服务端返回的 provider 列表。
    const savedBaseUrl = read?.providers.find((p) => p.id === selected.id)?.base_url ?? null;
    const typed = selected.apiKeyInput.trim();
    const keyUsage = resolveTestKeyUsage({
      typedKey: typed,
      apiKeySet: selected.apiKeySet,
      draftBaseUrl: selected.baseUrl,
      savedBaseUrl,
    });
    if (keyUsage.savedKeyBlockedHint) {
      messageApi.info(keyUsage.savedKeyBlockedHint);
    }

    setTestingLevel(level);
    setTestResult(null);
    try {
      const result = await settingsApi.testModelConnection({
        base_url: selected.baseUrl.trim(),
        model: selected.model.trim(),
        provider_type: selected.providerType,
        provider_id: selected.id || null,
        // 现输的密钥优先；没现输且这条已存过密钥、且地址没改，才用存着的那把
        api_key: typed || null,
        use_saved_key: keyUsage.useSavedKey,
        level,
        timeout_seconds: level === 'generation' ? 30 : 8,
      });
      if (stillCurrent()) {
        setTestResult(connectionResultText(result, selected.model.trim()));
      }
    } catch (error) {
      if (stillCurrent()) {
        setTestResult({
          tone: 'error',
          title: '没能完成测试',
          detail: error instanceof Error ? error.message : String(error),
        });
      }
    } finally {
      // 与探测同一套写法：转圈无条件停，在途标记才走守卫。这里原先只是因为放弃函数恰好显式清了
      // testingLevel 才没暴露同一个缺陷，两处统一，免得日后删掉那一行又复发。
      setTestingLevel(null);
      if (inFlightProviderId.current === startedId) {
        inFlightProviderId.current = null;
      }
    }
  };

  if (loadError) {
    return (
      <section className="panel settings-domain-panel">
        <div className="panel__body">
          <Alert showIcon title="模型服务配置加载失败" description={loadError} type="error" />
        </div>
      </section>
    );
  }
  if (!read) {
    return (
      <section className="panel settings-domain-panel">
        <div className="panel__body settings-domain-loading">
          <Spin />
        </div>
      </section>
    );
  }

  const urlHint = selected ? baseUrlHint(selected.baseUrl) : null;

  return (
    <section aria-label="模型服务配置" className="panel settings-domain-panel" data-testid="settings-provider-panel">
      {messageHolder}
      <div className="panel__header">
        <h2 className="panel__title">模型服务</h2>
        <span className="settings-domain-meta">{providerListStamp(read)}</span>
      </div>
      <div className="panel__body sp-body">
        <div className="sp-layout">
          <div className="sp-list" aria-label="模型服务列表">
            <div className="sp-list__head">
              <span>已配置 {drafts.length} 个</span>
              <Button size="small" data-testid="provider-add" onClick={handleAdd}>
                新增
              </Button>
            </div>
            {drafts.map((draft, index) => (
              <div
                key={draft.id || `new-${index}`}
                className={
                  index === selectedIndex ? 'sp-card sp-card--selected' : 'sp-card'
                }
                data-testid="provider-card"
                onClick={() => {
                  setSelectedIndex(index);
                  abandonInFlightTest();
                }}
              >
                <div className="sp-card__top">
                  <span className="sp-card__name">{draft.name.trim() || '未命名的模型服务'}</span>
                  {draft.id && draft.id === activeId ? (
                    <Tag color="green" data-testid="provider-active-tag">
                      使用中
                    </Tag>
                  ) : null}
                </div>
                <div className="sp-card__meta">
                  <Tag>{typeLabel(draft.providerType)}</Tag>
                  <span className="sp-card__url">{draft.baseUrl || '未填地址'}</span>
                </div>
                <div className="sp-card__meta sp-card__meta--sub">{draft.model || '未填模型标识'}</div>
                <div className="sp-card__actions">
                  {draft.id && draft.id === activeId ? null : (
                    <Button
                      size="small"
                      type="link"
                      data-testid="provider-activate"
                      onClick={(event) => {
                        event.stopPropagation();
                        handleActivate(draft);
                      }}
                    >
                      设为使用中
                    </Button>
                  )}
                  <Popconfirm
                    title="删除这个模型服务？"
                    description="保存后即从列表移除，它的 API Key 也会一并清除。"
                    okText="删除"
                    cancelText="取消"
                    onConfirm={() => handleRemove(index)}
                  >
                    <Button
                      danger
                      size="small"
                      type="link"
                      data-testid="provider-remove"
                      onClick={(event) => event.stopPropagation()}
                    >
                      删除
                    </Button>
                  </Popconfirm>
                </div>
              </div>
            ))}
            {drafts.length === 0 ? (
              <div className="sp-empty">还没有模型服务，点「新增」加一个。</div>
            ) : null}
          </div>

          <div className="sp-form" aria-label="模型服务详情">
            {selected ? (
              <>
                <div className="settings-form-sec">
                  <div className="settings-form-sec-title">基础连接</div>
                  <div className="settings-field-row">
                    <label className="settings-field">
                      <span className="settings-field__label">名称</span>
                      <Input
                        data-testid="provider-name"
                        placeholder="给这个服务起个好认的名字"
                        value={selected.name}
                        onChange={(event) => patchSelected({ name: event.target.value })}
                      />
                    </label>
                    <label className="settings-field">
                      <span className="settings-field__label">服务类型</span>
                      <Select
                        data-testid="provider-type"
                        options={typeOptions}
                        style={{ width: '100%' }}
                        value={selected.providerType}
                        onChange={(value) => patchSelected({ providerType: value })}
                      />
                    </label>
                    <label className="settings-field settings-field--wide">
                      <span className="settings-field__label">服务地址</span>
                      <Input
                        data-testid="provider-base-url"
                        placeholder="http://主机:端口/v1"
                        value={selected.baseUrl}
                        onChange={(event) => patchSelected({ baseUrl: event.target.value })}
                      />
                    </label>
                    <label className="settings-field">
                      <span className="settings-field__label">模型标识</span>
                      <Input
                        data-testid="provider-model"
                        placeholder="如 qwen2.5"
                        value={selected.model}
                        onChange={(event) => patchSelected({ model: event.target.value })}
                      />
                    </label>
                    <label className="settings-field">
                      <span className="settings-field__label">
                        API Key
                        <em className="settings-field__source">
                          {selected.apiKeySet ? '已设置（只写不回显）' : '未设置'}
                        </em>
                      </span>
                      <Input.Password
                        data-testid="provider-api-key"
                        disabled={selected.clearApiKey}
                        placeholder={selected.apiKeySet ? '留空则保留原值' : '本地服务通常不需要填'}
                        value={selected.apiKeyInput}
                        onChange={(event) => patchSelected({ apiKeyInput: event.target.value })}
                      />
                      {selected.apiKeySet ? (
                        <Checkbox
                          checked={selected.clearApiKey}
                          data-testid="provider-clear-key"
                          onChange={(event) =>
                            patchSelected({ clearApiKey: event.target.checked, apiKeyInput: '' })
                          }
                        >
                          保存时清除已存的 API Key
                        </Checkbox>
                      ) : null}
                    </label>
                  </div>
                  <div className="sp-type-hint">{typeHint(selected.providerType)}</div>
                  {urlHint ? (
                    <Alert showIcon type="warning" description={urlHint} data-testid="provider-url-hint" />
                  ) : null}
                </div>

                <div className="settings-form-sec">
                  <div className="settings-form-sec-title">调用参数</div>
                  <div className="settings-field-row">
                    <label className="settings-field">
                      <span className="settings-field__label">超时时间</span>
                      <Input
                        data-testid="provider-timeout"
                        suffix={<span className="settings-field__unit">秒</span>}
                        value={selected.timeoutSeconds}
                        onChange={(event) => patchSelected({ timeoutSeconds: event.target.value })}
                      />
                    </label>
                    <label className="settings-field">
                      <span className="settings-field__label">最大重试</span>
                      <Input
                        suffix={<span className="settings-field__unit">次</span>}
                        value={selected.maxRetries}
                        onChange={(event) => patchSelected({ maxRetries: event.target.value })}
                      />
                    </label>
                    <label className="settings-field">
                      <span className="settings-field__label">并发上限</span>
                      <Input
                        suffix={<span className="settings-field__unit">个</span>}
                        value={selected.concurrencyLimit}
                        onChange={(event) => patchSelected({ concurrencyLimit: event.target.value })}
                      />
                    </label>
                  </div>
                </div>

                <div className="settings-form-sec">
                  <div className="settings-form-sec-title">思考模式</div>
                  <div className="cap-thinking">
                    <Switch
                      data-testid="provider-thinking-switch"
                      checked={selected.thinkingEnabled}
                      onChange={(checked) => patchSelected({ thinkingEnabled: checked })}
                    />
                    <span className="cap-thinking__label">让这个模型服务带着思考过程运行</span>
                  </div>
                  <div className="cap-thinking__status" data-testid="provider-thinking-status">
                    {thinkingMode.statusText}
                  </div>
                  {thinkingMode.warning ? (
                    <Alert
                      showIcon
                      data-testid="provider-thinking-warning"
                      description={thinkingMode.warning}
                      title={thinkingMode.warningTitle}
                      type={thinkingMode.warningTone === 'warn' ? 'warning' : 'info'}
                    />
                  ) : null}
                </div>

                <div className="settings-form-sec">
                  <div className="settings-form-sec-title">连通测试与能力探测</div>
                  <div className="sp-test-row">
                    <Button
                      data-testid="provider-test-reachability"
                      loading={testingLevel === 'reachability'}
                      disabled={probing || (testingLevel !== null && testingLevel !== 'reachability')}
                      onClick={() => void handleTest('reachability')}
                    >
                      测「连得上」
                    </Button>
                    <Button
                      data-testid="provider-test-generation"
                      loading={testingLevel === 'generation'}
                      disabled={probing || (testingLevel !== null && testingLevel !== 'generation')}
                      onClick={() => void handleTest('generation')}
                    >
                      测「能正常回答」
                    </Button>
                    <Button
                      ghost
                      type="primary"
                      data-testid="provider-probe"
                      loading={probing}
                      disabled={testingLevel !== null}
                      onClick={() => void handleProbe()}
                    >
                      逐项探测能力
                    </Button>
                    <span className="sp-test-note">不用先保存也能测，测试不会改动已保存的配置</span>
                  </div>
                  {testResult ? (
                    <Alert
                      showIcon
                      data-testid="provider-test-result"
                      description={testResult.detail || undefined}
                      title={testResult.title}
                      type={testResult.tone === 'success' ? 'success' : 'error'}
                    />
                  ) : null}
                  {probing ? (
                    <div className="cap-pending">
                      正在逐项探测：会发几条很短的试探请求。思考模型可能要跑上一分钟。
                    </div>
                  ) : null}
                  {capabilityRows.length > 0 ? (
                    <>
                      <ul className="cap-list" data-testid="provider-capability-list">
                        {capabilityRows.map((row) => (
                          <li key={row.key} className={`cap-row cap-row--${row.tone}`}>
                            <span className="cap-row__icon" aria-hidden="true">
                              {row.tone === 'ok' ? <CheckCircleFilled /> : null}
                              {row.tone === 'warn' ? <WarningFilled /> : null}
                              {row.tone === 'bad' ? <CloseCircleFilled /> : null}
                            </span>
                            <span className="cap-row__label">{row.label}</span>
                            <span className="cap-row__body">
                              <span className="cap-row__summary">{row.summary}</span>
                              {row.detail ? <span className="cap-row__detail">{row.detail}</span> : null}
                            </span>
                          </li>
                        ))}
                      </ul>
                      <div className="cap-apply">
                        <Button
                          type="primary"
                          loading={saving}
                          disabled={!probeConclusive}
                          data-testid="provider-apply-profile"
                          onClick={() => void handleApplyProfile()}
                        >
                          应用探测结果
                        </Button>
                        <span className="cap-apply__note">
                          {probeStamp ? `${probeStamp}。` : ''}
                          {probeConclusive
                            ? '应用后这个服务的所有 AI 调用都按探到的参数走；会一并保存当前未保存的编辑。'
                            : '这轮没探出结论，应用会清掉上次探到的结果，所以先不让应用。请按上面的提示处理后再探一次。'}
                        </span>
                      </div>
                    </>
                  ) : null}
                </div>
              </>
            ) : (
              <div className="sp-empty">左侧选一个模型服务，或点「新增」。</div>
            )}

            <div className="settings-form-sec">
              <div className="settings-form-sec-title">生效范围</div>
              <div className="settings-hint-bar">
                <InfoCircleFilled aria-hidden="true" />
                <span>
                  全站 AI 功能都用<b>使用中</b>的那一个模型服务；保存后下一次 AI 调用即生效，不必重启。
                </span>
              </div>
            </div>
            <div className="settings-form-actions">
              {dirty ? <span className="sp-dirty">有未保存的改动</span> : null}
              <Button loading={saving} type="primary" data-testid="provider-save" onClick={() => void handleSave()}>
                保存
              </Button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

// ---- 文档资源域：引用标准目录（AEP-118；内置清单只可停用，自有条目可增可改可删）----

interface ReferenceStandardsPanelProps {
  operatorRef: string;
  onSaved: () => void;
}

function ReferenceStandardsPanel({ operatorRef, onSaved }: ReferenceStandardsPanelProps) {
  const [catalog, setCatalog] = useState<ReferenceStandardCatalogRead | null>(null);
  const [drafts, setDrafts] = useState<StandardDraftVM[]>([]);
  const [disabledKeys, setDisabledKeys] = useState<string[]>([]);
  const [baseline, setBaseline] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [keyword, setKeyword] = useState('');
  const [categoryFilter, setCategoryFilter] = useState<string>('all');
  // 编辑中的自有条目：draft 为表单值，index 为它在 drafts 里的下标（null=新增，保存时追加）。
  const [editing, setEditing] = useState<{ index: number | null; draft: StandardDraftVM } | null>(null);
  const [messageApi, messageHolder] = message.useMessage();

  // 未保存判定用「草稿 + 停用清单」的序列化快照与加载时的基线比对：逐字段比对写起来长，
  // 且漏掉任何一个字段都会让「有未保存的改动」提示失真。
  const stamp = (rows: StandardDraftVM[], disabled: string[]) =>
    JSON.stringify({ rows, disabled: [...disabled].sort() });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const body = await settingsApi.listReferenceStandards();
      const nextDrafts = body.entries.filter((e) => !e.builtin).map(standardDraftFrom);
      const nextDisabled = body.entries.filter((e) => e.builtin && !e.enabled).map((e) => e.key);
      setCatalog(body);
      setDrafts(nextDrafts);
      setDisabledKeys(nextDisabled);
      setBaseline(stamp(nextDrafts, nextDisabled));
      setLoadError(null);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const disabledSet = useMemo(() => new Set(disabledKeys), [disabledKeys]);
  const rows = useMemo(
    () => buildStandardRows(catalog, drafts, disabledSet),
    [catalog, drafts, disabledSet],
  );
  const visibleRows = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    return rows.filter((row) => {
      if (categoryFilter !== 'all' && row.categoryKey !== categoryFilter) {
        return false;
      }
      if (!needle) {
        return true;
      }
      return `${row.code} ${row.title} ${row.issuer} ${row.note}`.toLowerCase().includes(needle);
    });
  }, [rows, keyword, categoryFilter]);

  const dirty = baseline !== '' && stamp(drafts, disabledKeys) !== baseline;
  const enabledCount = rows.filter((row) => row.enabled).length;
  const categoryOptions = useMemo(
    () => [
      { value: 'all', label: '全部类别' },
      ...(catalog?.categories ?? []).map((c) => ({ value: c.key, label: c.label })),
    ],
    [catalog],
  );
  const editorCategoryOptions = useMemo(
    () => (catalog?.categories ?? []).map((c) => ({ value: c.key, label: c.label })),
    [catalog],
  );

  const toggleBuiltin = (key: string, enabled: boolean) => {
    setDisabledKeys((prev) => (enabled ? prev.filter((k) => k !== key) : [...prev, key]));
  };

  const removeDraft = (index: number) => {
    setDrafts((prev) => prev.filter((_, at) => at !== index));
  };

  const submitEditor = () => {
    if (!editing) {
      return;
    }
    const problem = validateStandardDrafts([editing.draft]);
    if (problem) {
      void messageApi.warning(problem);
      return;
    }
    setDrafts((prev) => {
      if (editing.index === null) {
        return [...prev, editing.draft];
      }
      return prev.map((row, at) => (at === editing.index ? editing.draft : row));
    });
    setEditing(null);
  };

  const handleSave = async () => {
    const problem = validateStandardDrafts(drafts);
    if (problem) {
      void messageApi.warning(problem);
      return;
    }
    setSaving(true);
    try {
      const body = await settingsApi.saveReferenceStandards({
        custom_entries: drafts.map(standardDraftToWrite),
        disabled_builtin_keys: disabledKeys,
        operator_ref: operatorRef,
      });
      const nextDrafts = body.entries.filter((e) => !e.builtin).map(standardDraftFrom);
      const nextDisabled = body.entries.filter((e) => e.builtin && !e.enabled).map((e) => e.key);
      setCatalog(body);
      setDrafts(nextDrafts);
      setDisabledKeys(nextDisabled);
      setBaseline(stamp(nextDrafts, nextDisabled));
      void messageApi.success('引用标准目录已保存');
      onSaved();
    } catch (error) {
      void messageApi.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  const columns = [
    {
      title: '标准号',
      dataIndex: 'code',
      width: '15%',
      render: (_: unknown, row: StandardRowVM) => (
        <div className="rs-code">
          <span className={row.enabled ? 'rs-code__text' : 'rs-code__text rs-code__text--off'}>
            {row.code || '（未填标准号）'}
          </span>
          {row.url ? (
            <a className="rs-code__link" href={row.url} rel="noreferrer" target="_blank">
              查看出处
            </a>
          ) : null}
        </div>
      ),
    },
    {
      title: '名称与适用说明',
      dataIndex: 'title',
      render: (_: unknown, row: StandardRowVM) => (
        <div className="rs-title">
          <span className="rs-title__name">{row.title || '（未填名称）'}</span>
          {row.note ? <span className="rs-title__note">{row.note}</span> : null}
        </div>
      ),
    },
    { title: '年份', dataIndex: 'year', width: '8%' },
    { title: '发布机构', dataIndex: 'issuer', width: '18%' },
    {
      title: '类别',
      dataIndex: 'categoryLabel',
      width: '10%',
      render: (label: string) => <Tag>{label}</Tag>,
    },
    {
      title: '来源',
      dataIndex: 'sourceText',
      width: '11%',
      render: (text: string, row: StandardRowVM) =>
        row.builtin && !row.enabled ? <Tag color="default">{text}</Tag> : <span>{text}</span>,
    },
    {
      title: '操作',
      dataIndex: 'key',
      width: '14%',
      render: (_: unknown, row: StandardRowVM) =>
        row.builtin ? (
          <Switch
            checked={row.enabled}
            checkedChildren="启用"
            data-testid={`rs-toggle-${row.key}`}
            size="small"
            unCheckedChildren="停用"
            onChange={(checked) => toggleBuiltin(row.key, checked)}
          />
        ) : (
          <span className="rs-actions">
            <Button
              size="small"
              type="link"
              data-testid={`rs-edit-${row.key}`}
              onClick={() => setEditing({ index: row.draftIndex, draft: drafts[row.draftIndex] })}
            >
              编辑
            </Button>
            <Popconfirm
              title="删除这条自有条目？"
              description="保存后即从目录移除，撰稿选取器里也不再出现。"
              okText="删除"
              cancelText="取消"
              onConfirm={() => removeDraft(row.draftIndex)}
            >
              <Button danger size="small" type="link" data-testid={`rs-remove-${row.key}`}>
                删除
              </Button>
            </Popconfirm>
          </span>
        ),
    },
  ];

  if (loading) {
    return (
      <section className="panel settings-domain-panel">
        <div className="panel__body settings-domain-loading">
          <Spin />
        </div>
      </section>
    );
  }

  return (
    <section
      aria-label="引用标准目录"
      className="panel settings-domain-panel"
      data-testid="settings-reference-standards-panel"
    >
      {messageHolder}
      <div className="panel__header">
        <h2 className="panel__title">引用标准目录</h2>
        <span className="settings-domain-meta">{catalogStamp(catalog)}</span>
      </div>
      <div className="panel__body sp-body">
        {loadError ? (
          <Alert showIcon title="引用标准目录加载失败" description={loadError} type="warning" />
        ) : null}
        <div className="settings-hint-bar">
          <InfoCircleFilled aria-hidden="true" />
          <span>
            这里登记写文档时可引用的标准条目（标准号、名称、年份、发布机构等信息），供
            <b>参考资料类章节撰稿时直接选取插入</b>。
            此处<b>只登记条目信息，不保存标准全文、也不上传文件</b>；要把某份标准当作分析材料使用，请走
            <b>材料接入</b>。
          </span>
        </div>
        <div className="rs-toolbar">
          <Input
            allowClear
            data-testid="rs-search"
            placeholder="搜标准号、名称、发布机构"
            style={{ width: '18rem' }}
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          />
          <Select
            data-testid="rs-category-filter"
            options={categoryOptions}
            style={{ width: '10rem' }}
            value={categoryFilter}
            onChange={setCategoryFilter}
          />
          <span className="rs-toolbar__count">
            共 {rows.length} 条 · 启用 {enabledCount} 条 · 自有 {drafts.length} 条
          </span>
          <Button
            data-testid="rs-add"
            onClick={() =>
              // 新增条目默认归「指南」：内置清单已经收齐常引的国际标准与国家标准，用户手工加的
              // 多是内部规范、行业指南这类系统不认识的东西。类别在表单里可改。
              setEditing({ index: null, draft: emptyStandardDraft('guide') })
            }
          >
            新增条目
          </Button>
        </div>
        <Table<StandardRowVM>
          columns={columns}
          dataSource={visibleRows}
          data-testid="rs-table"
          pagination={false}
          rowKey="key"
          size="small"
        />
        <div className="settings-note">
          <SafetyOutlined aria-hidden="true" />
          <span>
            <b>内置条目</b>随系统版本维护，只能停用或恢复、不能改写——停用后撰稿选取器里不再出现它；
            <b>自有条目</b>由你增删改。停用与增删改都要点「保存」才生效。
          </span>
        </div>
        <div className="settings-form-actions">
          {dirty ? <span className="sp-dirty">有未保存的改动</span> : null}
          <Button
            loading={saving}
            type="primary"
            data-testid="rs-save"
            onClick={() => void handleSave()}
          >
            保存
          </Button>
        </div>
      </div>
      <Modal
        destroyOnHidden
        open={editing !== null}
        title={editing?.index === null ? '新增引用标准条目' : '编辑引用标准条目'}
        okText="确定"
        cancelText="取消"
        onCancel={() => setEditing(null)}
        onOk={submitEditor}
      >
        {editing ? (
          <div className="rs-editor">
            <label className="settings-field">
              <span className="settings-field__label">标准号</span>
              <Input
                data-testid="rs-editor-code"
                placeholder="如 GB/T 8567-2006"
                value={editing.draft.code}
                onChange={(event) =>
                  setEditing({ ...editing, draft: { ...editing.draft, code: event.target.value } })
                }
              />
            </label>
            <label className="settings-field">
              <span className="settings-field__label">名称</span>
              <Input
                data-testid="rs-editor-title"
                placeholder="标准的正式名称"
                value={editing.draft.title}
                onChange={(event) =>
                  setEditing({ ...editing, draft: { ...editing.draft, title: event.target.value } })
                }
              />
            </label>
            <div className="rs-editor__row">
              <label className="settings-field">
                <span className="settings-field__label">年份</span>
                <Input
                  data-testid="rs-editor-year"
                  placeholder="如 2006"
                  value={editing.draft.year}
                  onChange={(event) =>
                    setEditing({ ...editing, draft: { ...editing.draft, year: event.target.value } })
                  }
                />
              </label>
              <label className="settings-field">
                <span className="settings-field__label">类别</span>
                <Select
                  data-testid="rs-editor-category"
                  options={editorCategoryOptions}
                  style={{ width: '100%' }}
                  value={editing.draft.category}
                  onChange={(value) =>
                    setEditing({ ...editing, draft: { ...editing.draft, category: value } })
                  }
                />
              </label>
            </div>
            <label className="settings-field">
              <span className="settings-field__label">发布机构</span>
              <Input
                data-testid="rs-editor-issuer"
                placeholder="如 国家标准化管理委员会"
                value={editing.draft.issuer}
                onChange={(event) =>
                  setEditing({ ...editing, draft: { ...editing.draft, issuer: event.target.value } })
                }
              />
            </label>
            <label className="settings-field">
              <span className="settings-field__label">适用说明</span>
              <Input
                data-testid="rs-editor-note"
                placeholder="一句话说明什么场景引用它"
                value={editing.draft.note}
                onChange={(event) =>
                  setEditing({ ...editing, draft: { ...editing.draft, note: event.target.value } })
                }
              />
            </label>
            <label className="settings-field">
              <span className="settings-field__label">链接（可不填）</span>
              <Input
                data-testid="rs-editor-url"
                placeholder="https://…"
                value={editing.draft.url}
                onChange={(event) =>
                  setEditing({ ...editing, draft: { ...editing.draft, url: event.target.value } })
                }
              />
            </label>
          </div>
        ) : null}
      </Modal>
    </section>
  );
}

// ---- 支撑能力域（导出能力 / 图表渲染）：表单按主原型结构 ----

// ---- 导出域专属操作（04A §9「按域提供专属操作」）：docx 导出依赖的本地工具链就绪清单 ----
// 探测要在服务端起子进程取版本，所以由用户点动作触发，不在进入页面时自动跑。

const READINESS_COLUMNS = [
  { title: '能力', dataIndex: 'capability', key: 'capability', width: '11rem' },
  {
    title: '状态',
    key: 'status',
    width: '6.5rem',
    render: (_: unknown, row: ExportReadinessRowVM) =>
      row.ready ? (
        <Tag color="success" icon={<CheckCircleFilled />}>
          {row.statusText}
        </Tag>
      ) : (
        <Tag color="error" icon={<WarningFilled />}>
          {row.statusText}
        </Tag>
      ),
  },
  { title: '说明', dataIndex: 'detail', key: 'detail' },
];

function ExportReadinessSection() {
  const [readiness, setReadiness] = useState<ExportReadinessVM | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);

  const probe = async () => {
    setProbing(true);
    setProbeError(null);
    try {
      setReadiness(buildExportReadiness(await settingsApi.getExportReadiness()));
    } catch (error) {
      setReadiness(null);
      setProbeError(error instanceof Error ? error.message : String(error));
    } finally {
      setProbing(false);
    }
  };

  return (
    <div className="settings-form-sec" data-testid="export-readiness">
      <div className="settings-form-sec-title">
        导出能力就绪
        <Button
          data-testid="export-readiness-probe"
          loading={probing}
          size="small"
          style={{ marginLeft: 'auto' }}
          onClick={() => void probe()}
        >
          {readiness ? '重新检测' : '检测导出能力'}
        </Button>
      </div>
      {probeError ? (
        <Alert showIcon title="检测失败" description={probeError} type="error" />
      ) : (
        <>
          <div className="settings-hint-bar">
            <InfoCircleFilled aria-hidden="true" />
            <span>
              {readiness
                ? `${readiness.summary}（${readiness.checkedText}）`
                : '导出 Word 文档时，图形渲染与 PDF 精确预览要用到本机装的几个工具。点右上角的按钮查看它们是否就绪。'}
            </span>
          </div>
          {readiness ? (
            <div style={{ marginTop: '0.75rem' }}>
              <Table<ExportReadinessRowVM>
                columns={READINESS_COLUMNS}
                dataSource={readiness.rows}
                data-testid="export-readiness-table"
                pagination={false}
                rowKey="key"
                size="small"
              />
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

interface CapabilityDomainPanelProps {
  domain: SettingsDomainKey;
  operatorRef: string;
  onSaved: () => Promise<void> | void;
}

function CapabilityDomainPanel({ domain, operatorRef, onSaved }: CapabilityDomainPanelProps) {
  const [form, setForm] = useState<SettingsDomainFormVM | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [edited, setEdited] = useState<Record<string, string>>({});
  const [secretInputs, setSecretInputs] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [messageApi, messageHolder] = message.useMessage();

  const load = useCallback(async () => {
    try {
      const read = await settingsApi.getDomain(domain);
      setForm(buildDomainForm(read));
      setLoadError(null);
      setEdited({});
      setSecretInputs({});
    } catch (error) {
      setForm(null);
      setLoadError(error instanceof Error ? error.message : String(error));
    }
  }, [domain]);

  useEffect(() => {
    void load();
  }, [load]);

  const fieldValue = (key: string, fallback: string) => edited[key] ?? fallback;

  const handleSave = async () => {
    if (!form) {
      return;
    }
    const values = toSaveValues(form, edited);
    const problem = validateDomainValues(values);
    if (problem) {
      messageApi.warning(problem);
      return;
    }
    setSaving(true);
    try {
      const secrets = Object.fromEntries(
        Object.entries(secretInputs).filter(([, value]) => value.trim() !== ''),
      );
      const result = await settingsApi.saveDomain(form.domain, {
        values,
        secrets,
        operator_ref: operatorRef,
      });
      messageApi.success(
        result.changed_keys.length > 0
          ? `已保存（${result.changed_keys.length} 项变更，审计已留痕）`
          : '已保存（无字段变更）',
      );
      await load();
      await onSaved();
    } catch (error) {
      messageApi.error(`保存失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSaving(false);
    }
  };

  if (loadError) {
    return (
      <section className="panel settings-domain-panel">
        <div className="panel__body">
          <Alert showIcon title="配置加载失败" description={loadError} type="error" />
        </div>
      </section>
    );
  }
  if (!form) {
    return (
      <section className="panel settings-domain-panel">
        <div className="panel__body settings-domain-loading">
          <Spin />
        </div>
      </section>
    );
  }

  return (
    <section aria-label={`${form.label}配置`} className="panel settings-domain-panel" data-testid="settings-domain-panel">
      {messageHolder}
      <div className="panel__header">
        <h2 className="panel__title">{form.label}配置</h2>
        <span className="settings-domain-meta">
          生效值来源：{form.sourceText} · 最近保存：{form.updatedText}
        </span>
      </div>
      <div className="panel__body">
        <div className="settings-form-sec">
          <div className="settings-form-sec-title">基础连接</div>
          <div className="settings-field-row">
            {form.connectionFields.map((field) => (
              <label key={field.key} className="settings-field">
                <span className="settings-field__label">
                  {field.label}
                  <em className="settings-field__source">{field.sourceText}</em>
                  {field.hint ? <em className="settings-field__source">{field.hint}</em> : null}
                </span>
                <Input
                  value={fieldValue(field.key, field.value)}
                  onChange={(event) => setEdited((prev) => ({ ...prev, [field.key]: event.target.value }))}
                />
              </label>
            ))}
            {form.secrets.map((secret) => (
              <label key={secret.key} className="settings-field">
                <span className="settings-field__label">
                  {secret.label}
                  <em className="settings-field__source">{secret.set ? '已设置（只写不回显）' : '未设置'}</em>
                </span>
                <Input.Password
                  placeholder={secret.set ? secret.placeholder : '输入后保存；留空保留原值'}
                  value={secretInputs[secret.key] ?? ''}
                  onChange={(event) =>
                    setSecretInputs((prev) => ({ ...prev, [secret.key]: event.target.value }))
                  }
                />
              </label>
            ))}
          </div>
        </div>
        {form.paramFields.length > 0 ? (
          <div className="settings-form-sec">
            <div className="settings-form-sec-title">调用参数</div>
            <div className="settings-field-row">
              {form.paramFields.map((field) => (
                <label key={field.key} className="settings-field">
                  <span className="settings-field__label">
                    {field.label}
                    <em className="settings-field__source">{field.sourceText}</em>
                  </span>
                  <Input
                    suffix={field.unit ? <span className="settings-field__unit">{field.unit}</span> : undefined}
                    value={fieldValue(field.key, field.value)}
                    onChange={(event) => setEdited((prev) => ({ ...prev, [field.key]: event.target.value }))}
                  />
                </label>
              ))}
            </div>
          </div>
        ) : null}
        {form.domain === 'export' ? <ExportReadinessSection /> : null}
        {/* 不设「生效范围」分区：保存即刻生效是默认，无需声明；只有需要重启才生效的配置才提示用户
            （2026-07-24 用户走查裁定）。带标题条的分区一律留给真正可操作的内容。 */}
        <div className="settings-form-actions">
          <Button loading={saving} type="primary" onClick={handleSave}>
            保存
          </Button>
        </div>
        <div className="settings-note">
          <SafetyOutlined aria-hidden="true" />
          <span>
            <b>边界：</b>配置只写配置；不形成确认结论、追溯关系或发布基线。保存经下游单元生效并由{' '}
            <b>审计留痕</b> 记录。
          </span>
        </div>
      </div>
    </section>
  );
}

// ---- 外观域（04A §9.1 / UINV-26）：本地偏好，选择即时生效，无保存按钮、无后端 ----

// ---- 生成治理 · 需求规约域：单选卡 + 详情卡（文案全部来自 AEP-102，前端禁硬编码规约文案）----

interface RequirementConventionPanelProps {
  operatorRef: string;
  onSaved: () => Promise<void>;
}

function RequirementConventionPanel({ operatorRef, onSaved }: RequirementConventionPanelProps) {
  const [catalog, setCatalog] = useState<ConventionCatalogVM | null>(null);
  const [read, setRead] = useState<ConfigDomainRead | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [messageApi, messageHolder] = message.useMessage();

  const load = useCallback(async () => {
    try {
      // 并行拉取方案目录（含 active_convention）与配置域读侧（留痕）。
      const [cat, dom] = await Promise.all([
        settingsApi.listRequirementConventions(),
        settingsApi.getDomain('requirement_convention'),
      ]);
      const vm = buildConventionCatalog(cat);
      setCatalog(vm);
      setRead(dom);
      setSelectedKey(vm.activeKey);
      setLoadError(null);
    } catch (error) {
      setCatalog(null);
      setLoadError(error instanceof Error ? error.message : String(error));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const dirty = Boolean(catalog && selectedKey && selectedKey !== catalog.activeKey);
  const detail = catalog && selectedKey ? catalog.detailByKey[selectedKey] : null;

  const handleSave = async () => {
    if (!selectedKey) {
      return;
    }
    setSaving(true);
    try {
      await settingsApi.saveDomain('requirement_convention', {
        values: { active_convention: selectedKey },
        secrets: {},
        operator_ref: operatorRef,
      });
      messageApi.success('已保存：仅对之后新生成的条目生效（存量条目不追溯）');
      await load();
      await onSaved();
    } catch (error) {
      // 白名单/网络失败就地展示，不清空预选。
      messageApi.error(`保存失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSaving(false);
    }
  };

  const updatedText = formatUpdatedStamp(
    read?.updated_at,
    read?.updated_by,
    '尚未保存过（默认生效：中文 EARS）',
  );

  return (
    <section
      aria-label="需求规约设置"
      className="panel settings-domain-panel"
      data-testid="settings-requirement-convention-panel"
    >
      {messageHolder}
      <div className="panel__header">
        <h2 className="panel__title">需求规约</h2>
      </div>
      <div className="panel__body">
        {loadError ? (
          <Alert showIcon title="需求规约方案加载失败" description={loadError} type="warning" />
        ) : null}
        <p className="settings-appearance-desc">
          条目形成时生成的需求条目陈述所遵循的规约方案；<b>切换仅对之后新生成的条目生效</b>，存量条目及其投影按各自记录的方案解读、不追溯重排。
        </p>

        {catalog ? (
          <>
            <div className="settings-form-sec">
              <div className="settings-form-sec-title">选择规约方案</div>
              <div className="rc-card-grid">
                {catalog.cards.map((card) => {
                  const selected = card.key === selectedKey;
                  return (
                    <button
                      key={card.key}
                      aria-pressed={selected}
                      className={selected ? 'rc-card rc-card--selected' : 'rc-card'}
                      data-testid={`rc-card-${card.key}`}
                      type="button"
                      onClick={() => setSelectedKey(card.key)}
                    >
                      <span className="rc-card__head">
                        <span className="rc-card__name">{card.displayName}</span>
                        {card.active ? (
                          <Tag color="green" data-testid={`rc-active-${card.key}`}>
                            当前生效
                          </Tag>
                        ) : null}
                        <span aria-hidden="true" className="rc-card__radio" />
                      </span>
                      <span className="rc-card__tagline">{card.tagline}</span>
                    </button>
                  );
                })}
              </div>
              {dirty ? (
                <div className="rc-dirty-hint" data-testid="rc-dirty-hint">
                  <InfoCircleFilled aria-hidden="true" /> 有未保存的方案变更，保存后方生效。
                </div>
              ) : null}
            </div>

            {detail ? (
              <div className="settings-form-sec" data-testid="rc-detail">
                <div className="settings-form-sec-title">{detail.displayName} · 方案详情</div>
                <p className="rc-positioning">{detail.positioning}</p>
                <p className="rc-blueprint">蓝本出处：{detail.blueprint}</p>

                <div className="rc-subtitle">句式模板</div>
                <div className="rc-table-wrap">
                  <table className="rc-table" data-testid="rc-pattern-table">
                    <thead>
                      <tr>
                        <th>模板名</th>
                        <th>句式</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.patterns.map((p, i) => (
                        <tr key={i}>
                          <td>{p.label}</td>
                          <td>{p.pattern}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="rc-subtitle">完整示例（五类需求）</div>
                <div className="rc-table-wrap">
                  <table className="rc-table" data-testid="rc-example-table">
                    <thead>
                      <tr>
                        <th>条目类型</th>
                        <th>规范陈述示例</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.examples.map((e, i) => (
                        <tr key={i}>
                          <td>{e.typeLabel}</td>
                          <td>{e.statement}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <Collapse
                  ghost
                  className="rc-common"
                  items={[
                    {
                      key: 'common',
                      label: '公共写作规范（方案无关，适用于全部方案）',
                      children: (
                        <div className="rc-common-body">
                          {COMMON_WRITING_NORMS.map((sec) => (
                            <div key={sec.title} className="rc-common-sec">
                              <b>{sec.title}</b>
                              {sec.lines.map((line, i) => (
                                <div key={i}>{line}</div>
                              ))}
                            </div>
                          ))}
                        </div>
                      ),
                    },
                  ]}
                />
              </div>
            ) : null}

            <div className="settings-apply-bar rc-save-bar">
              <Button
                data-testid="rc-save"
                disabled={!dirty}
                loading={saving}
                type="primary"
                onClick={() => void handleSave()}
              >
                保存方案
              </Button>
              <span className="settings-updated-note">最近保存：{updatedText}</span>
            </div>
          </>
        ) : loadError ? null : (
          <div className="settings-loading">
            <Spin />
          </div>
        )}
      </div>
    </section>
  );
}

/** 效果预览示例页导航项（结构跨主题一致，仅用于展示主题外观）。 */
const PREVIEW_NAV_ITEMS = ['总览台', '需求识别', '条目形成', '追溯分析', '发布管理'];

interface PreviewRow {
  key: string;
  id: string;
  name: string;
  status: string;
  tone: 'success' | 'processing' | 'warning';
}

const PREVIEW_ROWS: PreviewRow[] = [
  { key: '1', id: 'REQ-021', name: '登录鉴权流程', status: '已确认', tone: 'success' },
  { key: '2', id: 'REQ-022', name: '条目形成校验', status: '分析中', tone: 'processing' },
  { key: '3', id: 'REQ-023', name: '发布范围复核', status: '待确认', tone: 'warning' },
];

const PREVIEW_TONE_COLOR: Record<PreviewRow['tone'], string> = {
  success: 'green',
  processing: 'blue',
  warning: 'orange',
};

/**
 * 外观预览示例页：缩略应用外壳（顶栏 + 侧栏含激活项 + 内容区卡片/按钮/标签/表格）。
 * 双通道按草稿主题上色：嵌套 ConfigProvider 驱动 antd 组件令牌；.theme-scope[data-theme]
 * 驱动自绘外壳的 CSS 变量（据此真实呈现方案 C/E 深色侧栏、B 暗色算法）。仅静态假数据。
 */
function ThemePreviewSample({ themeKey, uiScale }: { themeKey: ThemeKey; uiScale: number }) {
  const columns = [
    { title: '编号', dataIndex: 'id', key: 'id', width: '30%' },
    { title: '名称', dataIndex: 'name', key: 'name' },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: '26%',
      render: (_: string, row: PreviewRow) => <Tag color={PREVIEW_TONE_COLOR[row.tone]}>{row.status}</Tag>,
    },
  ];

  return (
    <ConfigProvider theme={antdThemeFor(themeKey, uiScale)}>
      <div className="theme-scope tp-sample" data-testid="theme-preview-sample" data-theme={themeKey}>
        <div className="tp-sample__top">
          <span className="tp-sample__brand">
            <span aria-hidden="true" className="tp-sample__brand-dot" />
            需求文档协同平台
          </span>
          <span className="tp-sample__top-spacer" />
          <Button size="small" type="primary">
            新建条目
          </Button>
        </div>
        <div className="tp-sample__main">
          <nav aria-label="示例导航" className="tp-sample__nav">
            {PREVIEW_NAV_ITEMS.map((label, index) => (
              <span
                key={label}
                className={
                  index === 1 ? 'tp-sample__nav-item tp-sample__nav-item--active' : 'tp-sample__nav-item'
                }
              >
                <span aria-hidden="true" className="tp-sample__nav-dot" />
                {label}
              </span>
            ))}
          </nav>
          <div className="tp-sample__body">
            <div className="tp-sample__card">
              <div className="tp-sample__card-title">需求条目</div>
              <div className="tp-sample__row">
                <Button size="small" type="primary">
                  主操作
                </Button>
                <Button size="small">次操作</Button>
                <Tag color="blue">功能</Tag>
                <Tag color="green">已确认</Tag>
                <Tag color="orange">待确认</Tag>
              </div>
              <Table columns={columns} dataSource={PREVIEW_ROWS} pagination={false} size="small" />
            </div>
          </div>
        </div>
      </div>
    </ConfigProvider>
  );
}

function AppearancePanel() {
  const { themeKey, followSystem, selectTheme, setFollowSystem, uiScale, fontScale, selectFontScale } = useTheme();
  const [draftKey, setDraftKey] = useState<ThemeKey>(themeKey);

  // 已应用主题变化（点击应用后 / 跟随系统翻转）时，草稿基线随之同步。
  useEffect(() => {
    setDraftKey(themeKey);
  }, [themeKey]);

  const dirty = draftKey !== themeKey;
  const draftScheme = THEME_SCHEMES.find((scheme) => scheme.key === draftKey) ?? THEME_SCHEMES[0];

  return (
    <section aria-label="外观设置" className="panel settings-domain-panel" data-testid="settings-appearance-panel">
      <div className="panel__header">
        <h2 className="panel__title">外观设置</h2>
      </div>
      <div className="panel__body">
        <div className="settings-form-sec">
          <div className="settings-form-sec-title">界面风格</div>
          <p className="settings-appearance-desc">
            主题只改配色令牌；界面结构、图标与布局在所有主题下保持一致。选择方案后在下方预览最终效果，点击「应用」才实际生效。
          </p>
          <div className="theme-grid">
            {THEME_SCHEMES.map((scheme) => {
              const selected = scheme.key === draftKey;
              const current = scheme.key === themeKey;
              return (
                <button
                  key={scheme.key}
                  aria-pressed={selected}
                  className={selected ? 'theme-card theme-card--selected' : 'theme-card'}
                  data-testid={`theme-card-${scheme.key}`}
                  type="button"
                  onClick={() => setDraftKey(scheme.key)}
                >
                  <span aria-hidden="true" className="theme-preview" style={{ background: scheme.preview.page }}>
                    <span
                      className="theme-preview__top"
                      style={{ background: scheme.preview.top, borderBottom: `1px solid ${scheme.preview.border}` }}
                    />
                    <span
                      className="theme-preview__nav"
                      style={{ background: scheme.preview.nav, borderRight: `1px solid ${scheme.preview.border}` }}
                    />
                    <span className="theme-preview__body">
                      <span className="theme-preview__chip" style={{ background: scheme.preview.chip }} />
                      <span
                        className="theme-preview__card"
                        style={{ background: scheme.preview.card, border: `1px solid ${scheme.preview.border}` }}
                      />
                    </span>
                  </span>
                  <span className="theme-card__name">
                    {scheme.name}
                    {current ? <span className="theme-card__current">当前</span> : null}
                    <span aria-hidden="true" className="theme-card__radio" />
                  </span>
                  <span className="theme-card__desc">{scheme.description}</span>
                </button>
              );
            })}
          </div>
        </div>
        <div className="settings-form-sec">
          <div className="settings-form-sec-title">字体大小</div>
          <p className="settings-appearance-desc">
            调整全站字号与控件密度（与视口自适应缩放叠加生效），选择后立即生效；仅影响本浏览器显示。
          </p>
          <Segmented
            aria-label="字体大小"
            data-testid="font-scale-segmented"
            value={fontScale}
            options={FONT_SCALE_OPTIONS.map((option) => ({
              value: option.value,
              label: option.value === 100 ? option.label : `${option.label} ${option.value}%`,
            }))}
            onChange={(value) => selectFontScale(value as FontScale)}
          />
        </div>
        <div className="settings-appearance-preview" data-testid="settings-appearance-preview">
          <div className="settings-appearance-preview__head">
            <span className="settings-appearance-preview__title">
              效果预览<small>{draftScheme.name}</small>
            </span>
            <div className="settings-appearance-preview__actions">
              <Button data-testid="theme-reset" disabled={!dirty} onClick={() => setDraftKey(themeKey)}>
                还原
              </Button>
              <Button
                data-testid="theme-apply"
                disabled={!dirty}
                type="primary"
                onClick={() => selectTheme(draftKey)}
              >
                应用
              </Button>
            </div>
          </div>
          <div className="settings-appearance-preview__stage">
            <ThemePreviewSample themeKey={draftKey} uiScale={uiScale} />
          </div>
        </div>
        <div className="settings-switch-row">
          <Switch
            aria-label="跟随系统深浅色"
            checked={followSystem}
            data-testid="theme-follow-system"
            onChange={(checked) => setFollowSystem(checked)}
          />
          <b>跟随系统深浅色</b>
          <span>
            开启后仅在浅色基准（晴空蓝）与暗色（玄夜）之间自动切换；手动选择主题的优先级更高。
          </span>
        </div>
        <div className="settings-apply-bar">
          <CheckCircleFilled aria-hidden="true" />
          <span>
            <b>预览所选风格，点击「应用」后生效</b>；偏好仅保存在本浏览器（本地偏好），不写入配置存储，不产生治理事实，不影响统计与门禁口径。
          </span>
        </div>
      </div>
    </section>
  );
}
