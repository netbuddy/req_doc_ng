import type { OverviewWorkbenchVM } from '../view-models/overview';

// 总览台基础投影：结构（label/tone/目标工作面）+ 占位值。
// 数值一律 `—`/0 —— 真实数值由 buildOverviewVM 从后端只读投影覆盖；
// 无数据源的组（覆盖度/追溯/AI 效能）保持占位并由 deferredNote 标注，不显示假数。
export const overviewWorkbenchFixture: OverviewWorkbenchVM = {
  projectList: [],
  selectedProject: {
    id: '',
    name: '—',
    scope: '—',
    goal: '—',
    domainProfileLabel: '通用',
    createdText: '—',
    deferredFacts: '成员/团队/创建人：待接入（项目上下文服务）',
  },
  assetMetrics: [
    { key: 'materials', label: '材料', value: '—', tone: 'blue', targetWorkbench: 'management' },
    { key: 'requirements', label: '条目', value: '—', tone: 'green', targetWorkbench: 'management' },
    { key: 'diagrams', label: '图表', value: '—', tone: 'orange', targetWorkbench: 'diagram' },
    { key: 'documents', label: '文档', value: '—', tone: 'purple', targetWorkbench: 'release' },
    { key: 'issues', label: '问题项', value: '—', tone: 'red', targetWorkbench: 'management' },
  ],
  requirementTypeMetrics: [
    { key: 'functional', label: '功能', value: '—', tone: 'blue', targetWorkbench: 'management' },
    { key: 'quality', label: '质量', value: '—', tone: 'green', targetWorkbench: 'management' },
    { key: 'constraint', label: '约束', value: '—', tone: 'orange', targetWorkbench: 'management' },
    { key: 'data', label: '数据', value: '—', tone: 'purple', targetWorkbench: 'management' },
    { key: 'interface', label: '接口', value: '—', tone: 'green', targetWorkbench: 'settings' },
  ],
  // 顺序按原型 v2：待确认 → 已确认 → 已了结（终态两状态合并为一块）
  requirementStatusMetrics: [
    { key: 'pending', label: '待确认', value: '—', tone: 'orange', targetWorkbench: 'management' },
    { key: 'confirmed', label: '已确认', value: '—', tone: 'green', targetWorkbench: 'management' },
    { key: 'closed', label: '已了结（终止/被替代）', value: '—', tone: 'gray', targetWorkbench: 'management' },
  ],
  coverageMetrics: [
    { key: 'source-coverage', label: '来源覆盖', value: '—', percent: 0, tone: 'green', targetWorkbench: 'traceability' },
    { key: 'diagram-coverage', label: '图表覆盖', value: '—', percent: 0, tone: 'blue', targetWorkbench: 'traceability' },
    { key: 'document-coverage', label: '文档覆盖', value: '—', percent: 0, tone: 'purple', targetWorkbench: 'release' },
  ],
  traceabilityMetrics: [
    { key: 'trace-gap', label: '缺口', value: '—', tone: 'gray', targetWorkbench: 'traceability' },
    { key: 'suspicious-links', label: '可疑', value: '—', tone: 'gray', targetWorkbench: 'traceability' },
    { key: 'issue-items', label: '问题项', value: '—', tone: 'gray', targetWorkbench: 'management' },
  ],
  aiStageMetrics: [
    { key: 'material-intake', stage: '材料接入', accepted: '—', revised: '—', rejected: '—', issue: '—', targetWorkbench: 'management' },
    { key: 'analysis', stage: '知识抽取', accepted: '—', revised: '—', rejected: '—', issue: '—', targetWorkbench: 'management' },
    { key: 'item-formation', stage: '条目形成', accepted: '—', revised: '—', rejected: '—', issue: '—', targetWorkbench: 'management' },
    { key: 'item-review', stage: '条目评审', accepted: '—', revised: '—', rejected: '—', issue: '—', targetWorkbench: 'management' },
  ],
  aiCoverage: {
    key: 'ai-coverage',
    label: 'AI 覆盖',
    value: '—',
    percent: 0,
    tone: 'gray',
    targetWorkbench: 'management',
  },
  aiRiskSignals: [
    { key: 'low-confidence', label: '低置信度集中（<60%）', level: '—', levelTone: 'gray', value: '—', targetWorkbench: 'management' },
    { key: 'rejection-rising', label: '拒绝率上升（>15%）', level: '—', levelTone: 'gray', value: '—', targetWorkbench: 'management' },
    { key: 'issue-conversion', label: '转问题项异常（>8%）', level: '—', levelTone: 'gray', value: '—', targetWorkbench: 'management' },
    { key: 'source-conflict', label: '来源冲突高发', level: '—', levelTone: 'gray', value: '—', targetWorkbench: 'traceability' },
  ],
  flows: null,
  // 转化链/数字桥/对账行：无占位数字（转化链是一串因果，摆假数会读出错误结论），
  // 由 buildOverviewVM 从后端响应装配后才渲染。
  conversionChain: null,
  typeBridges: [],
  typeConfirmations: [],
  statusReconciliation: null,
  coverageReady: false,
  traceReady: false,
  aiReady: false,
  aiCoverageLegend: { touched: '—', untouched: '—', notApplicable: '—', total: '—' },
  aiCalibration: null,
  deliveryFailures: [],
  deferredNote: '待接入（暂无数据源）',
  boundaryItems: [
    {
      key: 'requirement-status',
      title: '需求统计与状态聚合',
      description: '需求资产目录服务（含 AEP-072）',
      tone: 'gray',
    },
    {
      key: 'coverage-gap',
      title: '覆盖度/缺口',
      description: '追溯分析服务',
      tone: 'gray',
    },
    {
      key: 'ai-analysis',
      title: 'AI 统计分析',
      description: '模型推理结果仓储',
      tone: 'gray',
    },
  ],
};
