import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../src/App';

const PROJECT_ID = '11111111-1111-1111-1111-111111111111';
const SECOND_PROJECT_ID = '22222222-2222-2222-2222-222222222222';
const CREATED_PROJECT_ID = '99999999-9999-9999-9999-999999999999';

const defaultProjects = [
  {
    id: PROJECT_ID,
    name: '运营效率系统',
    scope: 'release-v0.1',
    background: '提升运营效率与数据一致性',
    status: 'active',
    created_at: '2026-06-01T10:00:00Z',
  },
  {
    id: SECOND_PROJECT_ID,
    name: '供应链协同平台',
    scope: '供应链协同治理',
    background: null,
    status: 'active',
    created_at: '2026-05-20T09:00:00Z',
  },
];

// AEP-052/072 只读投影 stub：确定计数（材料 3 / 条目 2 / 类型 2-1-1-1-0 / 待确认 2），flows 空。
// 转化链与数字桥随同一响应下发（后端恒给），故 stub 一并给全，与真实响应同形。
function overviewStub(projectRef: string) {
  return {
    project_ref: projectRef,
    asset_metrics: [
      { key: 'materials', value: 3 },
      { key: 'elements', value: 5 },
      { key: 'items', value: 2 },
    ],
    requirement_type_metrics: [
      { key: 'functional', value: 2 },
      { key: 'quality', value: 1 },
      { key: 'constraint', value: 1 },
      { key: 'data', value: 1 },
      { key: 'interface', value: 0 },
    ],
    requirement_status_metrics: [
      { key: 'pending', value: 2 },
      { key: 'confirmed', value: 0 },
      { key: 'closed', value: 0 },
    ],
    conversion_chain: {
      elements_total: 5, elements_requirement: 5, elements_other: 0,
      elements_confirmed: 3, elements_pending: 2,
      materials_with_requirement: 3, materials_formed: 1, materials_unformed: 2,
      items_total: 2, items_pending: 2, items_confirmed: 0, items_closed: 0,
      items_sourced: 2, items_direct: 0,
    },
    type_bridge: [
      {
        key: 'functional', elements_total: 2, elements_confirmed: 2, elements_pending: 0,
        entered_formation: 2, not_formed: 0,
        not_formed_material_pending: 0, not_formed_not_adopted: 0,
        items_from_elements_same_type: 2, items_from_elements_other_type: 0,
        items_total: 2, items_sourced: 2, items_direct: 0,
      },
      ...(['quality', 'constraint', 'data'] as const).map((key) => ({
        key, elements_total: 1, elements_confirmed: 0, elements_pending: 1,
        entered_formation: 0, not_formed: 0,
        not_formed_material_pending: 0, not_formed_not_adopted: 0,
        items_from_elements_same_type: 0, items_from_elements_other_type: 0,
        items_total: 0, items_sourced: 0, items_direct: 0,
      })),
      {
        key: 'interface', elements_total: 0, elements_confirmed: 0, elements_pending: 0,
        entered_formation: 0, not_formed: 0,
        not_formed_material_pending: 0, not_formed_not_adopted: 0,
        items_from_elements_same_type: 0, items_from_elements_other_type: 0,
        items_total: 0, items_sourced: 0, items_direct: 0,
      },
    ],
    flows: [],
  };
}

function okJson(data: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => ({ success: true, data, error: null }),
  } as Response;
}

/** V2 应答信封端点用：响应体原样返回（信封由各 api 模块自行拆）。 */
function rawJson(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
  } as Response;
}

function notFound(url: string): Response {
  return {
    ok: false,
    status: 404,
    json: async () => ({ error: `unknown test route: ${url}` }),
  } as Response;
}

// 运行态面板(04A §2.1)stub:全绿正常态;infra 端点返回裸 JSON(非 envelope)。
function runtimeStatusStub() {
  return {
    status: 'normal',
    alert_count: 0,
    generated_at: '2026-07-01T00:00:00Z',
    components: [
      { key: 'api', label: 'API', status: 'ok', detail: '服务响应正常' },
      { key: 'db', label: 'DB', status: 'ok', detail: 'SELECT 1 探活通过' },
      { key: 'redis', label: 'Redis', status: 'ok', detail: 'ping 通过' },
      { key: 'worker', label: 'Worker', status: 'ok', detail: '1 个活跃 worker' },
      { key: 'event_bus', label: 'Event Bus / SSE', status: 'ok', detail: 'Redis Streams 真推送' },
    ],
    alerts: [],
    async_jobs: {
      mode: 'queued',
      queued: 0,
      running: 0,
      failed_recent: 0,
      oldest_waiting_minutes: null,
      queue_depth: 0,
    },
    diagnostics: [],
  };
}

function stubApi(projects = defaultProjects) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);

    if (url === '/api/runtime-status') {
      return {
        ok: true,
        status: 200,
        json: async () => runtimeStatusStub(),
      } as Response;
    }

    // 通知徽标(04A §2.1)stub:无未读事项;同为裸 JSON infra 端点。
    if (url === '/api/notifications') {
      return {
        ok: true,
        status: 200,
        json: async () => ({ notifications: [], unread_count: 0 }),
      } as Response;
    }

    // 项目四操作＝V2 应答信封（2026-08-07 项目管理组重构）：响应体本身就是信封，
    // 不套 okJson 的 V1 旧封套；列表只回摘要，详情走单读。
    if (url === '/api/projects') {
      if (init?.method === 'POST') {
        const body = JSON.parse(String(init.body));
        return rawJson({
          result: '成功',
          data: {
            project_id: CREATED_PROJECT_ID,
            name: body.name,
            scope: body.scope ?? null,
            background: body.background ?? null,
            domain_profile_key: null,
            domain_profile_label: '通用',
            created_at: '2026-08-07T10:00:00Z',
          },
        });
      }

      return rawJson({
        result: '成功',
        data: projects.map((project) => ({
          project_id: project.id,
          name: project.name,
          created_at: project.created_at,
        })),
      });
    }

    const projectDetailMatch = url.match(/^\/api\/projects\/([^/]+)$/);
    if (projectDetailMatch) {
      const found = projects.find((project) => project.id === projectDetailMatch[1]);
      if (found) {
        return rawJson({
          result: '成功',
          data: {
            project_id: found.id,
            name: found.name,
            scope: found.scope ?? null,
            background: found.background ?? null,
            domain_profile_key: null,
            domain_profile_label: '通用',
            created_at: found.created_at,
          },
        });
      }
      if (projectDetailMatch[1] === CREATED_PROJECT_ID) {
        return rawJson({
          result: '成功',
          data: {
            project_id: CREATED_PROJECT_ID,
            name: '智能客服升级',
            scope: 'release-v0.2',
            background: null,
            domain_profile_key: null,
            domain_profile_label: '通用',
            created_at: '2026-08-07T10:00:00Z',
          },
        });
      }
      return notFound(url);
    }

    // 总览台只读投影（任意项目返回同一确定 stub；新建项目未覆盖 → 404 → 前端保持 `—` 占位）
    const overviewMatch = url.match(/^\/api\/projects\/([^/]+)\/overview$/);
    if (overviewMatch && projects.some((project) => project.id === overviewMatch[1])) {
      return okJson(overviewStub(overviewMatch[1]));
    }

    // 资产读侧（维护列表/需求卡片/资产目录）：确定 stub，覆盖默认视图与资产树
    const itemListMatch = url.match(/^\/api\/projects\/([^/]+)\/requirement-items(\?.*)?$/);
    if (itemListMatch && projects.some((project) => project.id === itemListMatch[1])) {
      return okJson({
        project_ref: itemListMatch[1],
        items: [
          {
            ref: 'item-1',
            req_no: 'REQ-18',
            expression: '系统应在链路异常时给出可解释的诊断提示',
            req_type: 'quality',
            status: 'pending_confirmation',
            updated_at: '2026-07-01T09:00:00',
            source_count: 1,
            revision_count: 1,
          },
        ],
        total: 1,
      });
    }

    const itemCardMatch = url.match(/^\/api\/projects\/([^/]+)\/requirement-items\/item-1$/);
    if (itemCardMatch) {
      return okJson({
        ref: 'item-1',
        req_no: 'REQ-18',
        expression: '系统应在链路异常时给出可解释的诊断提示',
        req_type: 'quality',
        status: 'pending_confirmation',
        updated_at: '2026-07-01T09:00:00',
        source_evidence: [
          {
            element_ref: 'el-1',
            element_type: 'quality_attribute',
            content: '当链路检测到异常时，系统应提供清晰的原因提示与处理建议。',
            material_label: '运维访谈纪要',
          },
        ],
        revisions: [
          {
            field_key: 'expression',
            before_value: '旧表达',
            after_value: '系统应在链路异常时给出可解释的诊断提示',
            revision_mode: 'manual',
            reason: null,
            operator_ref: 'U1',
            created_at: '2026-07-01T09:00:00',
          },
        ],
        related: { charts: 1, documents: 1, trace_effective: 1, trace_suspect: 0 },
      });
    }

    const catalogMatch = url.match(/^\/api\/projects\/([^/]+)\/assets\/catalog$/);
    if (catalogMatch && projects.some((project) => project.id === catalogMatch[1])) {
      return okJson({
        project_ref: catalogMatch[1],
        groups: [
          {
            asset_type: 'requirement_item',
            count: 1,
            nodes: [
              {
                ref: 'item-1',
                label: 'REQ-18 系统应在链路异常时给出可解释的诊断提示',
                sub_label: 'quality',
                status: 'pending_confirmation',
                updated_at: '2026-07-01T09:00:00',
              },
            ],
          },
          { asset_type: 'material', count: 0, nodes: [] },
          { asset_type: 'element', count: 0, nodes: [] },
          { asset_type: 'chart', count: 0, nodes: [] },
          { asset_type: 'trace_link', count: 0, nodes: [] },
          { asset_type: 'document', count: 0, nodes: [] },
          { asset_type: 'issue', count: 0, nodes: [] },
        ],
        trace_summary: { effective: 1, pre_established: 0, suspect: 0, invalid: 0 },
      });
    }

    // 接入两操作＝V2 应答信封（2026-08-08 三拍制定案）：响应体本身就是信封。
    if (url === `/api/projects/${PROJECT_ID}/intake` && init?.method === 'POST') {
      return rawJson({
        result: '成功',
        data: { context_ref: 'ctx-1', agent_run_ref: 'run-1' },
      });
    }

    if (url === '/api/agent-runs/run-1') {
      return okJson({
        id: 'run-1',
        kind: 'source_intake',
        status: 'succeeded',
        error: null,
        events: [{ event: 'succeeded', at: '2026-07-01T00:00:00Z' }],
      });
    }

    if (url === `/api/projects/${PROJECT_ID}/intake/ctx-1`) {
      return rawJson({
        result: '成功',
        data: {
          context_ref: 'ctx-1',
          intake_conclusion: 'accepted',
          material_ref: 'MAT-001',
          basis: '材料具备明确业务诉求和来源，可进入知识抽取。',
          next_action: '进入知识抽取',
          available_actions: [{ key: 'start_recognition', enabled: true, disabled_reason: null }],
        },
      });
    }

    // 配置管理入口（04A §9）stub：模型服务已保存，其余 env 默认；外观域无后端路由。
    if (url === '/api/config/domains') {
      return okJson([
        {
          domain: 'model_service',
          label: '模型服务',
          group: '外部能力',
          downstream: '模型服务适配器',
          configured: true,
          source: 'saved',
          updated_at: '2026-07-05T00:00:00Z',
          updated_by: 'U1',
        },
        {
          domain: 'export',
          label: '导出能力',
          group: '外部能力',
          downstream: '文档转换适配器',
          configured: false,
          source: 'env',
          updated_at: null,
          updated_by: null,
        },
        {
          domain: 'chart_rendering',
          label: '图表渲染',
          group: '外部能力',
          downstream: '图表渲染适配器',
          configured: false,
          source: 'env',
          updated_at: null,
          updated_by: null,
        },
      ]);
    }

    // 模型服务域已改为多 provider 列表（T20260720）：列表 + 启用指针 + 类型封闭集目录。
    if (url === '/api/config/model-service/providers') {
      return okJson({
        active_provider_id: 'default',
        providers: [
          {
            id: 'default',
            name: '通义千问服务',
            provider_type: 'llama_cpp',
            base_url: 'http://llm.local/v1',
            model: 'qwen-plus',
            timeout_seconds: 60,
            max_retries: 3,
            concurrency_limit: 5,
            api_key_set: true,
            active: true,
          },
        ],
        provider_types: [
          { key: 'llama_cpp', label: 'llama.cpp', description: 'llama.cpp 自带的兼容服务' },
          { key: 'ollama', label: 'Ollama', description: 'Ollama 的兼容层' },
          { key: 'vllm', label: 'vLLM', description: 'vLLM 的兼容服务' },
          { key: 'openai_compatible', label: '通用 OpenAI 兼容', description: '其他兼容服务' },
        ],
        source: 'saved',
        updated_at: '2026-07-05T00:00:00Z',
        updated_by: 'U1',
      });
    }

    if (url === '/api/config/model_service') {
      return okJson({
        domain: 'model_service',
        label: '模型服务',
        group: '外部能力',
        downstream: '模型服务适配器',
        source: 'saved',
        updated_at: '2026-07-05T00:00:00Z',
        updated_by: 'U1',
        fields: [
          { key: 'service_name', value: '通义千问服务', source: 'saved' },
          { key: 'base_url', value: 'http://llm.local/v1', source: 'saved' },
          { key: 'model', value: 'qwen-plus', source: 'saved' },
          { key: 'timeout_seconds', value: 60, source: 'env' },
          { key: 'max_retries', value: 3, source: 'env' },
          { key: 'concurrency_limit', value: 5, source: 'env' },
        ],
        secrets: [{ key: 'api_key', set: true, placeholder: '••••••••' }],
      });
    }

    return notFound(url);
  });

  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function metricButtonNames(testId: string): string[] {
  return within(screen.getByTestId(testId))
    .getAllByRole('button')
    .map((button) => button.getAttribute('aria-label') ?? '');
}

// 可驱动的 EventSource 桩：让测试手动推 SSE 事件帧，覆盖 Redis 推送路径。
class DrivableEventSource {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(public url: string) {
    DrivableEventSource.instances.push(this);
  }

  static instances: DrivableEventSource[] = [];

  close() {
    this.closed = true;
  }

  emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }
}

function installDrivableEventSource(): DrivableEventSource[] {
  DrivableEventSource.instances = [];
  vi.stubGlobal('EventSource', DrivableEventSource);
  return DrivableEventSource.instances;
}

beforeEach(() => {
  stubApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('App shell navigation', () => {
  it('渲染顶栏、项目选择和全局搜索', async () => {
    render(<App />);

    expect(screen.getByText('睿析')).toBeInTheDocument();
    expect((await screen.findAllByText(/运营效率系统/)).length).toBeGreaterThan(0);
    expect(screen.getByLabelText('全局搜索')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: '运行态 正常' })).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '主导航' })).toBeInTheDocument();
  });

  it('项目选择器加载真实项目并支持切换', async () => {
    render(<App />);

    expect((await screen.findAllByText(/运营效率系统/)).length).toBeGreaterThan(0);

    fireEvent.mouseDown(screen.getByLabelText('当前项目'));
    const projectOptions = await screen.findAllByText(/供应链协同平台/);
    await userEvent.click(projectOptions[projectOptions.length - 1]);

    expect(screen.getAllByText(/供应链协同平台/).length).toBeGreaterThan(0);
  });

  it('默认显示项目治理总览台', async () => {
    render(<App />);

    expect(screen.getByRole('region', { name: '项目治理总览台' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '左区：项目管理' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '主区 · 需求统计' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '主区 · AI 效能分析' })).toBeInTheDocument();
    expect(screen.getByText('边界：总览台只读聚合 + 导航（项目动作除外）')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: '选中项目：运营效率系统' })).toBeInTheDocument();
    // 范围/背景等详情字段经「读单个项目」异步到位（2026-08-07 列表瘦身），须等待。
    expect((await screen.findAllByText('release-v0.1')).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /新建/ })).not.toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('button', { name: /归档/ })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getAllByRole('button', { name: /设置/ }).some((button) => button.getAttribute('aria-disabled') === 'true')).toBe(true);
    expect(screen.getByText('项目列表')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('搜索项目名称')).toBeInTheDocument();

    // 项目列表 = 真实项目、行可点（当前项目行 + 可切换行）
    expect(await screen.findByRole('button', { name: '运营效率系统（当前项目）' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '切换到项目 供应链协同平台' })).toBeInTheDocument();

    // 选中项目卡 = 真实事实（背景/创建时间）+ 无来源字段标注待接入。
    // 「状态」行已随 status 死列删除（2026-08-07 项目管理组重构）。
    expect(screen.getByText('提升运营效率与数据一致性')).toBeInTheDocument();
    expect(screen.getByText('2026-06-01')).toBeInTheDocument();
    expect(screen.getByText(/成员\/团队\/创建人：待接入/)).toBeInTheDocument();

    // 有数据源指标 = 后端投影真值；无数据源（图表/文档/问题项/覆盖/追溯）一律 `—`
    await screen.findByRole('button', { name: /待确认 2/ });
    expect(metricButtonNames('overview-project-assets')).toEqual([
      '材料 3，跳转到管理',
      '条目 2，跳转到管理',
      '图表 —，跳转到图表',
      '文档 —，跳转到发布',
      '问题项 —，跳转到管理',
    ]);
    // 类型瓦片两个动作分开成两个按钮：瓦片主体切换数字桥，右侧箭头跳转工作台
    expect(metricButtonNames('overview-type-metrics')).toEqual([
      '功能知识项 2，查看该类型的数字桥',
      '跳转到管理查看功能知识项',
      '质量知识项 1，查看该类型的数字桥',
      '跳转到管理查看质量知识项',
      '约束知识项 1，查看该类型的数字桥',
      '跳转到管理查看约束知识项',
      '数据知识项 1，查看该类型的数字桥',
      '跳转到管理查看数据知识项',
      '接口知识项 0，查看该类型的数字桥',
      '跳转到设置查看接口知识项',
    ]);
    // 按状态补终态瓦片（顺序按原型：待确认 → 已确认 → 已了结）
    expect(metricButtonNames('overview-status-metrics')).toEqual([
      '待确认 2，跳转到管理',
      '已确认 0，跳转到管理',
      '已了结（终止/被替代） 0，跳转到管理',
    ]);
    // 对账行：三块之和与资产盘点「需求条目」一致
    expect(screen.getByTestId('overview-status-reconciliation')).toHaveTextContent(
      '2＋0＋0＝2＝资产盘点「需求条目」✓',
    );
    // 转化链四节点 + 默认展示功能类数字桥
    expect(screen.getByTestId('overview-chain-node-recognition')).toHaveTextContent('已有知识项');
    expect(screen.getByTestId('overview-chain-node-output')).toHaveTextContent('需求条目');
    expect(screen.getByTestId('overview-type-bridge')).toHaveTextContent(
      '2 个已有功能知识项',
    );
    expect(metricButtonNames('overview-coverage-metrics')).toEqual([
      '来源覆盖 —，跳转到追溯',
      '图表覆盖 —，跳转到追溯',
      '文档覆盖 —，跳转到发布',
    ]);
    expect(metricButtonNames('overview-risk-metrics')).toEqual([
      '缺口 —，跳转到追溯',
      '可疑 —，跳转到追溯',
      '问题项 —，跳转到管理',
    ]);
    expect(within(screen.getByTestId('overview-ai-stage-table')).getAllByRole('row')).toHaveLength(5);
    // 全页不残留任何旧示例假数值
    expect(screen.queryByText(/92%/)).not.toBeInTheDocument();
    expect(screen.queryByText(/0\.08/)).not.toBeInTheDocument();
    expect(screen.queryByText('86%')).not.toBeInTheDocument();
    expect(screen.getByText('边界：总览台只读聚合 + 导航（项目动作除外）')).toBeInTheDocument();
  });

  it('项目列表行点击切换项目并过滤搜索', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole('heading', { name: '选中项目：运营效率系统' });

    // 行点击切换（与顶栏项目选择器同一入口）
    await user.click(screen.getByRole('button', { name: '切换到项目 供应链协同平台' }));
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: '选中项目：供应链协同平台' })).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: '供应链协同平台（当前项目）' })).toBeInTheDocument();

    // 搜索过滤真实列表
    await user.type(screen.getByPlaceholderText('搜索项目名称'), '运营');
    expect(screen.getByRole('button', { name: '切换到项目 运营效率系统' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '供应链协同平台（当前项目）' })).not.toBeInTheDocument();

    await user.clear(screen.getByPlaceholderText('搜索项目名称'));
    await user.type(screen.getByPlaceholderText('搜索项目名称'), '不存在的项目');
    expect(screen.getByText('无匹配项目')).toBeInTheDocument();
  });

  it('总览台随顶栏项目切换更新当前项目上下文', async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByRole('heading', { name: '选中项目：运营效率系统' })).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByLabelText('当前项目'));
    const projectOptions = await screen.findAllByText(/供应链协同平台/);
    await user.click(projectOptions[projectOptions.length - 1]);

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: '选中项目：供应链协同平台' })).toBeInTheDocument();
    });
    expect(screen.getAllByText('供应链协同治理').length).toBeGreaterThan(0);
  });

  it('总览台新建项目：填写表单提交后写入并选中新项目', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    render(<App />);

    await screen.findAllByText(/运营效率系统/);

    await user.click(screen.getByRole('button', { name: /新建/ }));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(await screen.findByText('新建项目')).toBeInTheDocument();

    await user.type(await screen.findByLabelText('项目名称'), '智能客服升级');
    await user.type(await screen.findByLabelText('项目范围'), 'release-v0.2');
    await user.click(await screen.findByRole('button', { name: /创\s*建/ }));

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url) === '/api/projects' && init?.method === 'POST',
      );

      expect(createCall).toBeTruthy();
      expect(JSON.parse(String(createCall?.[1]?.body))).toMatchObject({
        name: '智能客服升级',
        scope: 'release-v0.2',
      });
    });

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: '选中项目：智能客服升级' })).toBeInTheDocument();
    });
  });

  it('总览台新建项目：空名称时阻止提交并提示', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    render(<App />);

    await screen.findAllByText(/运营效率系统/);

    await user.click(screen.getByRole('button', { name: /新建/ }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: /创\s*建/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('请填写项目名称');
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url) === '/api/projects' && init?.method === 'POST',
      ),
    ).toBe(false);
  });

  it('总览台可点数字导航到对应工作面', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findAllByText(/运营效率系统/);
    await user.click(await screen.findByRole('button', { name: /待确认 2/ }));

    expect(screen.getByRole('region', { name: '需求管理工作台' })).toBeInTheDocument();
    expect(screen.getByText('资产导航')).toBeInTheDocument();
  });

  it('导航不再存在需求入口（资产树已并入管理）', async () => {
    render(<App />);
    await screen.findAllByText(/运营效率系统/);
    expect(screen.queryByRole('button', { name: '需求' })).not.toBeInTheDocument();
  });

  it('管理入口显示需求管理工作台（资产导航树 + 详情卡）', async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.click(screen.getByText('管理'));

    expect(screen.getByRole('region', { name: '需求管理工作台' })).toBeInTheDocument();
    expect(screen.getByText('资产导航')).toBeInTheDocument();
    expect(screen.getByText('需求卡片（选中条目详情）')).toBeInTheDocument();
    // 资产导航树：五类分组 + 条目按语义类型子分组（追溯关系/问题项不入树）
    // 注：v2 KPI 仪表带也含「需求条目」标签，故用 findAllByText（树分组 + KPI 各一）
    expect((await screen.findAllByText('需求条目')).length).toBeGreaterThan(0);
    expect(screen.getByText('材料')).toBeInTheDocument();
    expect(screen.getByText('质量属性')).toBeInTheDocument();
    expect(screen.queryByText('追溯关系')).not.toBeInTheDocument();
    expect(screen.queryByText('问题项')).not.toBeInTheDocument();
    expect(screen.getByText('追溯摘要（项目级）')).toBeInTheDocument();
    // 条目叶子与需求卡片来自资产读侧 AEP（真实数据投影，含首条自动选中）
    expect((await screen.findAllByText(/REQ-18/)).length).toBeGreaterThan(0);
    expect(await screen.findByText('状态门禁')).toBeInTheDocument();
    expect(screen.getByText('下一步')).toBeInTheDocument();
    expect(screen.getAllByText('评审确认').length).toBeGreaterThan(0);
    // v2 高保真详情卡：dt-h 页签（质量与陈述/追溯与影响/版本历史/验收与验证）
    expect(screen.getByRole('button', { name: '质量与陈述' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /版本历史/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '验收与验证' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /追溯/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /图表/ }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('button', { name: /发布/ }).length).toBeGreaterThan(0);
    expect(screen.queryByText('新增需求流程')).not.toBeInTheDocument();
    expect(screen.queryByText('区3 来源画布')).not.toBeInTheDocument();
  });

  it('需求管理工作台通过 viewMode 在默认视图与新增需求视图之间互斥切换', async () => {
    const user = userEvent.setup();
    render(<App />);

    await screen.findAllByText(/运营效率系统/);
    await user.click(screen.getByText('管理'));
    await user.click(screen.getByRole('button', { name: /新增/ }));

    expect(screen.getByRole('region', { name: '材料接入' })).toBeInTheDocument();
    expect(screen.getByText('本次材料来源')).toBeInTheDocument();
    expect(screen.getByText('材料正文（来源画布）')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /提交接入判断/ })).toBeDisabled();
    // 新建模式（非预填）不显示「放弃本次接入」（位置修正 2026-07-10：该动作仅预填模式）
    expect(screen.queryByRole('button', { name: /放弃本次接入/ })).not.toBeInTheDocument();
    expect(screen.queryByText('当前状态')).not.toBeInTheDocument();
    expect(screen.queryByText('其他操作')).not.toBeInTheDocument();
    expect(screen.queryByText('资产导航')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /放弃本次输入/ }));

    expect(screen.getByText('资产导航')).toBeInTheDocument();
    expect(screen.queryByText('材料正文（来源画布）')).not.toBeInTheDocument();
  });

  it('无项目时禁用材料接入提交', async () => {
    vi.unstubAllGlobals();
    stubApi([]);

    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByText('请先创建项目')).toBeInTheDocument();
    await user.click(screen.getByText('管理'));
    await user.click(screen.getByRole('button', { name: /新增/ }));

    expect(screen.getByRole('button', { name: /提交接入判断/ })).toBeDisabled();
    expect(screen.getByText('无项目时不可提交接入判断')).toBeInTheDocument();
  });

  it('提交成功后监听 AgentRun 并在 accepted 时开放进入知识抽取', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    render(<App />);

    await screen.findAllByText(/运营效率系统/);
    await user.click(screen.getByText('管理'));
    await user.click(screen.getByRole('button', { name: /新增/ }));
    await user.type(
      screen.getByLabelText('材料正文'),
      '客户要求在异常链路出现时提供诊断提示，并保留来源依据。',
    );
    await user.click(screen.getByRole('button', { name: /提交接入判断/ }));

    expect((await screen.findAllByText('可进入知识抽取')).length).toBeGreaterThan(0);
    expect(screen.getByText('MAT-001')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /进入知识抽取/ })).toBeEnabled();

    await waitFor(() => {
      const intakeCall = fetchMock.mock.calls.find(
        ([url, init]) => String(url) === `/api/projects/${PROJECT_ID}/intake` && init?.method === 'POST',
      );

      expect(intakeCall).toBeTruthy();
      expect(JSON.parse(String(intakeCall?.[1]?.body))).toMatchObject({
        text: '客户要求在异常链路出现时提供诊断提示，并保留来源依据。',
        operator_ref: '李想',
      });
    });
  });

  it('SSE 终态帧内联结论时直接渲染，不再第三次 GET 接入结果', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    const esInstances = installDrivableEventSource();
    render(<App />);

    await screen.findAllByText(/运营效率系统/);
    await user.click(screen.getByText('管理'));
    await user.click(screen.getByRole('button', { name: /新增/ }));
    await user.type(
      screen.getByLabelText('材料正文'),
      '客户要求在异常链路出现时提供诊断提示，并保留来源依据。',
    );
    await user.click(screen.getByRole('button', { name: /提交接入判断/ }));

    // 提交返回 agent_run_ref 后订阅 SSE。
    await waitFor(() => expect(esInstances.length).toBe(1));

    act(() => {
      esInstances[0].emit({ event: 'agent_run.started' });
    });
    act(() => {
      esInstances[0].emit({
        event: 'agent_run.completed',
        result: {
          context_ref: 'ctx-1',
          intake_conclusion: 'accepted',
          material_ref: 'MAT-777',
          basis: '材料具备明确业务诉求和来源。',
          next_action: '进入知识抽取',
          available_actions: [{ key: 'start_recognition', enabled: true, disabled_reason: null }],
        },
      });
    });

    // 内联结论（MAT-777，区别于结果读端点的 MAT-001）被直接渲染。
    expect((await screen.findAllByText('可进入知识抽取')).length).toBeGreaterThan(0);
    expect(screen.getByText('MAT-777')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /进入知识抽取/ })).toBeEnabled();

    // 消费了内联结论 → 不应再对结果读端点发起第三次 GET。
    const intakeResultGet = fetchMock.mock.calls.find(
      ([url]) => String(url) === `/api/projects/${PROJECT_ID}/intake/ctx-1`,
    );
    expect(intakeResultGet).toBeUndefined();
  });

  it('SSE 终态帧为 agent_run.failed 时呈现处理失败', async () => {
    const user = userEvent.setup();
    const esInstances = installDrivableEventSource();
    render(<App />);

    await screen.findAllByText(/运营效率系统/);
    await user.click(screen.getByText('管理'));
    await user.click(screen.getByRole('button', { name: /新增/ }));
    await user.type(screen.getByLabelText('材料正文'), '一段无法判定归属的零散信息。');
    await user.click(screen.getByRole('button', { name: /提交接入判断/ }));

    await waitFor(() => expect(esInstances.length).toBe(1));

    act(() => {
      esInstances[0].emit({ event: 'agent_run.failed', error: '模型判断失败', result: null });
    });

    expect((await screen.findAllByText('处理失败')).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /进入知识抽取/ })).toBeDisabled();
  });
});

// 总览行动作统一为「恢复」＋放弃下沉接入页（T20260710-overview-resume-unify，位置修正 2026-07-10）。
describe('总览行统一「恢复」与接入页放弃本次接入', () => {
  const TERMINAL_FLOW_ID = 'ctx-t1';
  const TERMINAL_TITLE = '六月客户访谈';

  function flowStages(intakeStatus: string) {
    return [
      { stage: 'intake', status: intakeStatus, detail: null },
      { stage: 'analysis', status: 'not_started', detail: null },
      { stage: 'itemFormation', status: 'not_started', detail: null },
      { stage: 'itemReview', status: 'not_started', detail: null },
    ];
  }

  // 行3 终结态（需补充，dismissable=true）+ 行1 可恢复（resumable=true）各一条。
  function stubFlowScenario() {
    let flows: unknown[] = [
      {
        flow_id: 'ctx-r1',
        title: '进行中的运维材料',
        summary: '材料接入 · 进行中',
        current_stage: 'intake',
        resume_stage: 'intake',
        resumable: true,
        dismissable: false,
        stages: flowStages('in_progress'),
        intake_context_ref: 'ctx-r1',
        material_ref: null,
        parse_context_ref: null,
        formation_context_ref: null,
        updated_at: '2026-07-10T08:00:00+00:00',
      },
      {
        flow_id: TERMINAL_FLOW_ID,
        title: TERMINAL_TITLE,
        summary: '材料接入 · 停靠',
        current_stage: 'intake',
        resume_stage: 'intake',
        resumable: false,
        dismissable: true,
        stages: flowStages('stopped'),
        intake_context_ref: TERMINAL_FLOW_ID,
        material_ref: null,
        parse_context_ref: null,
        formation_context_ref: null,
        updated_at: '2026-07-10T09:00:00+00:00',
      },
    ];
    const base = vi.mocked(fetch);
    const wrapped = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === `/api/projects/${PROJECT_ID}/overview`) {
        return okJson({ ...overviewStub(PROJECT_ID), flows });
      }
      if (url === `/api/projects/${PROJECT_ID}/requirement-flows/${TERMINAL_FLOW_ID}/intake-prefill`) {
        return okJson({
          context_ref: TERMINAL_FLOW_ID,
          raw_text: '六月访谈中客户提出的原始诉求正文。',
          source_note:
            '来源类型:客户访谈；来源对象:六月客户访谈；来源时间:2026-06-30；提交人:李想；来源说明:无',
        });
      }
      if (
        url === `/api/projects/${PROJECT_ID}/requirement-flows/${TERMINAL_FLOW_ID}/dismiss` &&
        init?.method === 'POST'
      ) {
        flows = flows.filter((flow) => (flow as { flow_id: string }).flow_id !== TERMINAL_FLOW_ID);
        return okJson({ context_ref: TERMINAL_FLOW_ID, dismissed_at: '2026-07-10T10:00:00+00:00' });
      }
      return base(input, init);
    });
    vi.stubGlobal('fetch', wrapped);
    return wrapped;
  }

  it('总览全部流程行只有统一「恢复」动作；无「继续编辑/放弃本次接入」残留', async () => {
    stubFlowScenario();
    render(<App />);

    const panel = await screen.findByTestId('overview-flows-panel');
    // 可恢复行与终结态行各渲染一个「恢复」按钮（终结态 hover 注明预填重提）
    expect(
      await within(panel).findByRole('button', { name: '恢复 进行中的运维材料，跳转到需求管理工作台' }),
    ).toBeInTheDocument();
    const terminalResume = within(panel).getByRole('button', {
      name: `恢复 ${TERMINAL_TITLE}，预填后重新提交为新流程`,
    });
    expect(terminalResume).toHaveAttribute('title', '预填后重新提交为新流程');
    expect(terminalResume).toHaveClass('overview-flow-resume');
    // 双按钮实现无残留
    expect(within(panel).queryByRole('button', { name: /继续编辑/ })).not.toBeInTheDocument();
    expect(within(panel).queryByRole('button', { name: /放弃本次接入/ })).not.toBeInTheDocument();
  });

  it('终结态行点「恢复」→ 接入页预填并显示「放弃本次接入」→ 确认后软删并返回总览', async () => {
    const user = userEvent.setup();
    const fetchMock = stubFlowScenario();
    render(<App />);

    const panel = await screen.findByTestId('overview-flows-panel');
    await user.click(
      await within(panel).findByRole('button', {
        name: `恢复 ${TERMINAL_TITLE}，预填后重新提交为新流程`,
      }),
    );

    // 预填承接（AEP-112）：正文与折叠来源字段还原
    expect(await screen.findByRole('region', { name: '材料接入' })).toBeInTheDocument();
    expect(screen.getByLabelText('材料正文')).toHaveValue('六月访谈中客户提出的原始诉求正文。');
    expect(screen.getByDisplayValue('六月客户访谈')).toBeInTheDocument();

    // 预填模式显示「放弃本次接入」→ 二次确认弹层
    await user.click(screen.getByRole('button', { name: `放弃本次接入 ${TERMINAL_TITLE}` }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/过程记录保留，可审计追溯/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: '放弃本次接入' }));

    // AEP-111 调用 + 返回总览且该行消失（可恢复行仍在）
    await waitFor(() => {
      const dismissCall = fetchMock.mock.calls.find(
        ([url, init]) =>
          String(url) === `/api/projects/${PROJECT_ID}/requirement-flows/${TERMINAL_FLOW_ID}/dismiss` &&
          init?.method === 'POST',
      );
      expect(dismissCall).toBeTruthy();
      expect(JSON.parse(String(dismissCall?.[1]?.body))).toMatchObject({ operator_ref: '李想' });
    });
    expect(await screen.findByRole('region', { name: '项目治理总览台' })).toBeInTheDocument();
    const panelAfter = await screen.findByTestId('overview-flows-panel');
    await within(panelAfter).findByRole('button', { name: '恢复 进行中的运维材料，跳转到需求管理工作台' });
    expect(within(panelAfter).queryByText(TERMINAL_TITLE)).not.toBeInTheDocument();
  });
});

describe('设置工作台（04A §9 配置管理入口）', () => {
  afterEach(() => {
    window.localStorage.clear();
    delete document.documentElement.dataset.theme;
  });

  async function openSettings(user: ReturnType<typeof userEvent.setup>) {
    render(<App />);
    const nav = screen.getByRole('navigation', { name: '主导航' });
    await user.click(within(nav).getByRole('button', { name: '设置' }));
  }

  it('设置入口显示真实设置工作台：两区布局 + 配置域菜单三组 + 状态签', async () => {
    const user = userEvent.setup();
    await openSettings(user);

    expect(screen.getByRole('region', { name: '设置工作台' })).toBeInTheDocument();
    expect(screen.getByText('只写配置 + 本地偏好，不写治理事实')).toBeInTheDocument();
    // 左区：配置域菜单三组（身份与权限 / 外部能力 / 个性化）
    expect(screen.getByText('配置域菜单')).toBeInTheDocument();
    expect(screen.getByText('身份与权限')).toBeInTheDocument();
    expect(screen.getByText('外部能力')).toBeInTheDocument();
    expect(screen.getByText('个性化')).toBeInTheDocument();
    // 状态签：模型服务已配置（stub），用户与权限待接入，外观本地偏好
    expect(await screen.findByText('已配置')).toBeInTheDocument();
    expect(screen.getByText('待接入')).toBeInTheDocument();
    expect(screen.getByText('本地偏好')).toBeInTheDocument();
    // 右区默认选中模型服务域：provider 列表 + 详情表单 + 两级连通测试（T20260720）
    expect(await screen.findByRole('heading', { name: '模型服务' })).toBeInTheDocument();
    expect(await screen.findByTestId('settings-provider-panel')).toBeInTheDocument();
    // 列表：已保存的那一个显示为使用中
    expect(await screen.findByText('通义千问服务')).toBeInTheDocument();
    expect(screen.getByTestId('provider-active-tag')).toHaveTextContent('使用中');
    // 详情：基础连接/调用参数/思考模式/连通测试与能力探测/生效范围五段
    // （能力探测面板 T20260724 起把「连通测试」一段扩为「连通测试与能力探测」并新增「思考模式」）
    expect(screen.getByText('基础连接')).toBeInTheDocument();
    expect(screen.getByText('调用参数')).toBeInTheDocument();
    expect(screen.getByText('连通测试与能力探测')).toBeInTheDocument();
    expect(screen.getByText('思考模式')).toBeInTheDocument();
    expect(screen.getByText('生效范围')).toBeInTheDocument();
    // 两级测试各一个按钮
    expect(screen.getByTestId('provider-test-reachability')).toBeInTheDocument();
    expect(screen.getByTestId('provider-test-generation')).toBeInTheDocument();
    expect(screen.getByTestId('provider-save')).toBeInTheDocument();
    // 密钥只写不回显：已设置只给「留空则保留原值」占位，页面上无明文
    expect(await screen.findByPlaceholderText('留空则保留原值')).toBeInTheDocument();
    expect(screen.getByText('已设置（只写不回显）')).toBeInTheDocument();
  });

  it('外观域：五张方案卡 + 预览 + 应用后才切换全局主题（本地偏好）', async () => {
    const user = userEvent.setup();
    await openSettings(user);

    await user.click(screen.getByTestId('settings-domain-appearance'));
    expect(screen.getByRole('heading', { name: '外观设置' })).toBeInTheDocument();
    // 五张方案卡（迷你预览 + 名称 + 定位 + 单选高亮）
    for (const key of ['a-qingkong', 'b-xuanye', 'c-dianqing', 'd-qingbi', 'e-baolan']) {
      expect(screen.getByTestId(`theme-card-${key}`)).toBeInTheDocument();
    }
    expect(screen.getByText('方案 B · 玄夜')).toBeInTheDocument();
    // 新流程：预览后点击「应用」才生效
    expect(screen.getByText(/点击「应用」后生效/)).toBeInTheDocument();
    // 效果预览示例页存在；默认草稿=方案 A，应用/还原初始禁用（无改动）
    expect(screen.getByTestId('theme-preview-sample')).toBeInTheDocument();
    expect(screen.getByTestId('theme-card-a-qingkong')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('theme-apply')).toBeDisabled();
    expect(screen.getByTestId('theme-reset')).toBeDisabled();

    // 点选玄夜：仅进入草稿预览（示例页切主题、卡片高亮），不改全站、不写本地偏好
    await user.click(screen.getByTestId('theme-card-b-xuanye'));
    expect(screen.getByTestId('theme-card-b-xuanye')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('theme-preview-sample')).toHaveAttribute('data-theme', 'b-xuanye');
    expect(document.documentElement.dataset.theme).toBe('a-qingkong');
    expect(window.localStorage.getItem('rx-theme')).toBeNull();
    expect(screen.getByTestId('theme-apply')).toBeEnabled();

    // 还原：草稿回退到当前已应用方案（A），示例页复位、按钮重新禁用
    await user.click(screen.getByTestId('theme-reset'));
    expect(screen.getByTestId('theme-preview-sample')).toHaveAttribute('data-theme', 'a-qingkong');
    expect(screen.getByTestId('theme-apply')).toBeDisabled();

    // 点选玄夜后点击应用：即时生效（data-theme）+ 本地偏好持久化（rx-theme），无后端调用
    await user.click(screen.getByTestId('theme-card-b-xuanye'));
    await user.click(screen.getByTestId('theme-apply'));
    expect(document.documentElement.dataset.theme).toBe('b-xuanye');
    expect(window.localStorage.getItem('rx-theme')).toBe('b-xuanye');
    // 应用后草稿=已应用，按钮回到禁用
    expect(screen.getByTestId('theme-apply')).toBeDisabled();

    // 跟随系统开关：即时生效（不入预览流程）；jsdom mock matchMedia matches=false（浅色）→ 回到晴空蓝
    await user.click(screen.getByRole('switch', { name: '跟随系统深浅色' }));
    expect(window.localStorage.getItem('rx-theme-follow')).toBe('1');
    expect(document.documentElement.dataset.theme).toBe('a-qingkong');
    // 手动选择优先级更高：点选青碧 → 应用 → 跟随开关自动关闭
    await user.click(screen.getByTestId('theme-card-d-qingbi'));
    await user.click(screen.getByTestId('theme-apply'));
    expect(document.documentElement.dataset.theme).toBe('d-qingbi');
    expect(window.localStorage.getItem('rx-theme-follow')).toBe('0');
  });
});
