import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { runtimeStatusApi, type RuntimeStatusRead } from '../src/api/runtime-status';
import { RuntimeStatusBadge } from '../src/shell/RuntimeStatusBadge';

// 运行态面板的即时性与最近作业明细表（T20260724-agent-run-observability ③⑤）。

function payload(overrides: Partial<RuntimeStatusRead> = {}): RuntimeStatusRead {
  return {
    status: 'normal',
    alert_count: 0,
    generated_at: '2026-07-25T03:14:30+08:00',
    components: [{ key: 'api', label: 'API', status: 'ok', detail: '服务响应正常' }],
    alerts: [],
    async_jobs: {
      mode: 'queued',
      queued: 0,
      running: 1,
      failed_recent: 1,
      oldest_waiting_minutes: null,
      queue_depth: 0,
    },
    recent_jobs: [
      {
        run_id: 'r-1',
        kind: 'element_recognition',
        kind_label: '知识项识别',
        status: 'started',
        created_at: '2026-07-25T03:14:08+08:00',
        duration_seconds: null,
        reason_code: null,
      },
      {
        run_id: 'r-2',
        kind: 'docx_export',
        kind_label: 'docx 导出转换',
        status: 'failed',
        created_at: '2026-07-03T09:00:00+08:00',
        duration_seconds: 1_900_000,
        reason_code: 'queue.orphaned',
      },
    ],
    diagnostics: [],
    ...overrides,
  };
}

function openPanel(): void {
  fireEvent.click(screen.getByRole('button', { name: /运行态/ }));
}

function closePanel(): void {
  fireEvent.click(screen.getByRole('button', { name: /close/i }));
}

let getRuntimeStatus: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  getRuntimeStatus = vi
    .spyOn(runtimeStatusApi, 'getRuntimeStatus')
    .mockResolvedValue(payload());
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('运行态面板即时性', () => {
  it('打开面板立刻拉取一次，不等下一个轮询刻度', async () => {
    render(<RuntimeStatusBadge />);
    await waitFor(() => expect(getRuntimeStatus).toHaveBeenCalledTimes(1));

    openPanel();

    await waitFor(() => expect(getRuntimeStatus).toHaveBeenCalledTimes(2));
  });

  it('展开期轮询加密到 5 秒，收起后恢复 30 秒', async () => {
    render(<RuntimeStatusBadge />);
    await waitFor(() => expect(getRuntimeStatus).toHaveBeenCalledTimes(1));

    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });
    expect(getRuntimeStatus).toHaveBeenCalledTimes(1); // 收起态：5 秒不动

    await act(async () => {
      vi.advanceTimersByTime(25_000);
    });
    expect(getRuntimeStatus).toHaveBeenCalledTimes(2); // 收起态：满 30 秒拉一次

    openPanel();
    await waitFor(() => expect(getRuntimeStatus).toHaveBeenCalledTimes(3)); // 打开即拉

    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });
    expect(getRuntimeStatus).toHaveBeenCalledTimes(4); // 展开态：5 秒即拉

    // 从展开态收起：节奏必须退回 30 秒。抽屉若不复位 open，收起后仍会每 5 秒打一次后端。
    closePanel();
    await waitFor(() => expect(getRuntimeStatus).toHaveBeenCalledTimes(5)); // 收起也立即拉一次

    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });
    expect(getRuntimeStatus).toHaveBeenCalledTimes(5); // 已收起：5 秒不动

    await act(async () => {
      vi.advanceTimersByTime(25_000);
    });
    expect(getRuntimeStatus).toHaveBeenCalledTimes(6); // 已收起：满 30 秒拉一次
  });

  it('头部标出本屏数据的采集时刻，取值即后端下发的 generated_at', async () => {
    // 测试时区钉死为 Asia/Shanghai（见 vite.config.ts）；投喂的 generated_at 带 +08:00，
    // 故期望值确定。只匹配 \d{2}:\d{2}:\d{2} 的形状时，渲染浏览器当前时钟也照过。
    render(<RuntimeStatusBadge />);
    openPanel();

    await waitFor(() =>
      expect(screen.getByText(/数据截至/).textContent).toBe('数据截至 03:14:30'),
    );
    // 悬停原值走绝对时刻格式（机器 ISO 串比可见文案还难读；合并裁定 F6）
    expect(screen.getByText(/数据截至/)).toHaveAttribute('title', '2026-07-25 03:14:30');
  });

  it('加载中没有采集时刻时不渲染空 title', () => {
    getRuntimeStatus.mockReturnValue(new Promise(() => {})); // 永不落定：停在加载态
    render(<RuntimeStatusBadge />);
    openPanel();

    expect(screen.getByText(/数据截至/)).not.toHaveAttribute('title');
  });

  it('轮询刻度发出的请求迟到时不覆盖已写入的新数据', async () => {
    // 定时器每一拍发出的请求，其作废句柄被直接丢弃（effect 清理只作废自己那次首拉），
    // 所以只有序号能拦住它。这里让展开期的一个刻度请求悬着不落定，用户随即收起面板、
    // 收起当拍的首拉成功返回，然后那个刻度请求才失败——无序号保护时它会清空面板并把
    // 徽标打成「后端不可达」，且要挂满一个 30 秒周期才自愈（冷审查裁定 C2）。
    render(<RuntimeStatusBadge />);
    await waitFor(() => expect(getRuntimeStatus).toHaveBeenCalledTimes(1));
    openPanel();
    await waitFor(() => expect(getRuntimeStatus).toHaveBeenCalledTimes(2));

    let rejectStaleTick: (reason?: unknown) => void = () => {};
    getRuntimeStatus.mockReturnValueOnce(
      new Promise((_resolve, reject) => {
        rejectStaleTick = reject;
      }),
    );
    await act(async () => {
      vi.advanceTimersByTime(5_000); // 展开期刻度：这一拍的请求悬着
    });
    expect(getRuntimeStatus).toHaveBeenCalledTimes(3);

    closePanel(); // 收起当拍的首拉成功返回，写入新数据
    await waitFor(() => expect(getRuntimeStatus).toHaveBeenCalledTimes(4));
    await waitFor(() => expect(screen.getByText(/数据截至 03:14:30/)).toBeInTheDocument());

    await act(async () => {
      rejectStaleTick(new Error('stale tick failed'));
    });

    expect(screen.getByText(/数据截至 03:14:30/)).toBeInTheDocument();
    expect(screen.queryByText('后端不可达,无法获取组件状态')).toBeNull();
    expect(screen.getByRole('button', { name: /运行态/ })).toHaveAccessibleName('运行态 正常');
  });
});

describe('最近作业明细表', () => {
  it('逐行给类型白话名、状态、耗时与失败原因码', async () => {
    render(<RuntimeStatusBadge />);
    openPanel();

    const section = await screen.findByLabelText('最近作业');
    await waitFor(() => expect(within(section).getAllByRole('row')).toHaveLength(3)); // 表头 + 2 行
    const [running, orphaned] = within(section)
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getAllByRole('cell'));

    // 列序＝类型 / 状态 / 发起时间 / 耗时
    expect(running[0]).toHaveTextContent('知识项识别');
    expect(running[1]).toHaveTextContent('进行中');
    expect(running[3]).toHaveTextContent('进行中'); // 非终态不编造耗时数字
    expect(orphaned[0]).toHaveTextContent('docx 导出转换');
    expect(orphaned[1]).toHaveTextContent('失败');
    expect(orphaned[1]).toHaveTextContent('queue.orphaned'); // 失败原因给稳定码
    expect(orphaned[3]).toHaveTextContent('21 天 23 小时');
  });

  it('没有近期作业时给一句白话，不留空表格', async () => {
    getRuntimeStatus.mockResolvedValue(payload({ recent_jobs: [] }));
    render(<RuntimeStatusBadge />);
    openPanel();

    const section = await screen.findByLabelText('最近作业');

    expect(within(section).getByText('最近没有异步作业')).toBeInTheDocument();
    expect(within(section).queryByRole('table')).toBeNull();
  });

  it('旧后端不返回 recent_jobs 时按空态渲染，不崩', async () => {
    const { recent_jobs: _dropped, ...withoutRecent } = payload();
    getRuntimeStatus.mockResolvedValue(withoutRecent as RuntimeStatusRead);
    render(<RuntimeStatusBadge />);
    openPanel();

    const section = await screen.findByLabelText('最近作业');

    expect(within(section).getByText('最近没有异步作业')).toBeInTheDocument();
  });
});
