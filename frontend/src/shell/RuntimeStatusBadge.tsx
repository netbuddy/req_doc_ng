import { Button, Drawer, Tag, Tooltip } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { runtimeStatusApi, type RuntimeStatusRead } from '../api/runtime-status';
import {
  buildRuntimeStatusVM,
  RECENT_JOBS_EMPTY_TEXT,
  type RuntimeFetchPhase,
} from '../view-models/runtime-status';
import { ReloadOutlined } from '../ui/icons';
import { RelativeTime } from '../ui/RelativeTime';
import '../styles-runtime-panel.css';

// 状态栏运行态徽标 + 右侧只读侧滑面板"运行态面板 / 诊断中心"(04A §2.1)。
// UINV-24:只展开侧滑面板,不新增导航项、不切换正文区、不处理业务对象。

/** 面板收起时的轮询节奏:徽标只需要"大致新鲜",不值得频繁打后端。 */
const POLL_INTERVAL_MS = 30_000;
/** 面板展开时的轮询节奏。收起档的 30 秒会整窗错过短作业——实测一次 34 秒的运行只落在
 * 一个刻度上,用户刷新时任务已结束、面板里什么也没看见,故展开期加密到 5 秒。 */
const OPEN_POLL_INTERVAL_MS = 5_000;

const ALERT_TAG_COLOR = { warning: 'warning', error: 'error' } as const;
const BADGE_TAG_COLOR: Record<string, string> = {
  normal: 'success',
  degraded: 'warning',
  down: 'error',
  unknown: 'default',
};

export function RuntimeStatusBadge() {
  const [data, setData] = useState<RuntimeStatusRead | null>(null);
  const [phase, setPhase] = useState<RuntimeFetchPhase>('loading');
  const [open, setOpen] = useState(false);

  // 请求序号:定时器每一拍都新发一次请求,先发后到的响应会覆盖后发先到的。失败分支尤其重
  // ——它清空面板并让徽标显示「后端不可达」,一次迟到的失败能在数据已经正常的前提下把面板
  // 挂成异常态,最长挂满一个轮询周期。故成功与失败两个分支同守:只有当前最新一次请求的
  // 响应允许写 state(冷审查裁定 C2)。
  const requestSeq = useRef(0);

  const refresh = useCallback(() => {
    let disposed = false;
    const seq = (requestSeq.current += 1);
    const isCurrent = (): boolean => !disposed && seq === requestSeq.current;

    runtimeStatusApi
      .getRuntimeStatus()
      .then((next) => {
        if (isCurrent()) {
          setData(next);
          setPhase('ready');
        }
      })
      .catch(() => {
        if (isCurrent()) {
          setData(null);
          setPhase('error');
        }
      });

    return () => {
      disposed = true;
    };
  }, []);

  // open 进依赖表,于是展开/收起当拍就重跑本 effect:先立即拉一次(打开面板即见当下真实
  // 情况,不必等下一个轮询刻度),再按对应节奏起表。
  useEffect(() => {
    const dispose = refresh();
    const timer = window.setInterval(refresh, open ? OPEN_POLL_INTERVAL_MS : POLL_INTERVAL_MS);

    return () => {
      dispose();
      window.clearInterval(timer);
    };
  }, [refresh, open]);

  const vm = useMemo(() => buildRuntimeStatusVM(data, phase), [data, phase]);

  return (
    <>
      <Tooltip title="运行态面板 / 诊断中心">
        <button
          aria-label={`运行态 ${vm.badge.statusText}`}
          className={`runtime-badge runtime-badge--${vm.badge.tone}`}
          type="button"
          onClick={() => setOpen(true)}
        >
          <span aria-hidden="true" className="runtime-badge__dot" />
          <span>运行态</span>
          <span className="runtime-badge__state">{vm.badge.statusText}</span>
          {vm.badge.alertCount > 0 && (
            <span className="runtime-badge__count">{vm.badge.alertCount}</span>
          )}
        </button>
      </Tooltip>

      <Drawer
        className="runtime-panel"
        open={open}
        title="运行态面板 / 诊断中心"
        width={480}
        onClose={() => setOpen(false)}
      >
        <div className="runtime-panel__header">
          <div>
            <span className="runtime-panel__section-title">总体状态</span>
            <Tag color={BADGE_TAG_COLOR[vm.badge.tone]}>{vm.overallStatusText}</Tag>
          </div>
          <div className="runtime-panel__refresh">
            <span className="runtime-panel__muted" title={vm.generatedAtTitle || undefined}>
              数据截至 {vm.generatedAtClock}
            </span>
            <Button
              aria-label="刷新运行态"
              icon={<ReloadOutlined />}
              size="small"
              type="text"
              onClick={refresh}
            />
          </div>
        </div>

        <section aria-label="组件状态" className="runtime-panel__section">
          <h4 className="runtime-panel__section-title">组件状态</h4>
          <div className="runtime-panel__components">
            {vm.components.map((component) => (
              <Tooltip key={component.key} title={component.detail}>
                <div className="runtime-component">
                  <span className="runtime-component__label">{component.label}</span>
                  <span className={`runtime-component__state runtime-tone--${component.tone}`}>
                    <span aria-hidden="true" className="runtime-component__dot" />
                    {component.statusText}
                  </span>
                </div>
              </Tooltip>
            ))}
            {vm.components.length === 0 && (
              <span className="runtime-panel__muted">
                {phase === 'error' ? '后端不可达,无法获取组件状态' : '正在检测…'}
              </span>
            )}
          </div>
        </section>

        <section aria-label="当前告警" className="runtime-panel__section">
          <h4 className="runtime-panel__section-title">
            当前告警
            {vm.alerts.length > 0 && (
              <span className="runtime-panel__count-chip">{vm.alerts.length}</span>
            )}
          </h4>
          {vm.alerts.length === 0 ? (
            <span className="runtime-panel__muted">无活跃告警</span>
          ) : (
            <ul className="runtime-panel__alerts">
              {vm.alerts.map((alert) => (
                <li className="runtime-alert" key={alert.code}>
                  <div className="runtime-alert__row">
                    <span className="runtime-alert__summary">{alert.summary}</span>
                    <Tag color={ALERT_TAG_COLOR[alert.tone]}>{alert.levelText}</Tag>
                  </div>
                  {alert.hint && <div className="runtime-panel__muted">{alert.hint}</div>}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-label="异步作业" className="runtime-panel__section">
          <h4 className="runtime-panel__section-title">
            异步作业
            <span className="runtime-panel__muted"> {vm.asyncModeText}</span>
          </h4>
          <div className="runtime-panel__tiles">
            {vm.asyncJobTiles.map((tile) => (
              <div className="runtime-tile" key={tile.key}>
                <span className="runtime-tile__label">{tile.label}</span>
                <span className="runtime-tile__value">{tile.value}</span>
              </div>
            ))}
          </div>
        </section>

        <section aria-label="最近作业" className="runtime-panel__section">
          <h4 className="runtime-panel__section-title">最近作业</h4>
          {vm.recentJobs.length === 0 ? (
            <span className="runtime-panel__muted">{RECENT_JOBS_EMPTY_TEXT}</span>
          ) : (
            <table className="runtime-panel__table runtime-jobs">
              <thead>
                <tr>
                  <th>类型</th>
                  <th>状态</th>
                  <th>发起时间</th>
                  <th>耗时</th>
                </tr>
              </thead>
              <tbody>
                {vm.recentJobs.map((job) => (
                  <tr key={job.runId}>
                    <td className="runtime-jobs__type">{job.typeText}</td>
                    <td>
                      <span className={`runtime-jobs__state runtime-tone--${job.statusTone}`}>
                        <span aria-hidden="true" className="runtime-jobs__dot" />
                        {job.statusText}
                      </span>
                      {job.reasonCode && (
                        <div className="runtime-jobs__reason">{job.reasonCode}</div>
                      )}
                    </td>
                    <td><RelativeTime iso={job.createdAt} /></td>
                    <td className="runtime-jobs__duration">{job.durationText}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section aria-label="诊断事件" className="runtime-panel__section">
          <h4 className="runtime-panel__section-title">诊断事件</h4>
          {vm.diagnostics.length === 0 ? (
            <span className="runtime-panel__muted">暂无 WARN/ERROR 诊断事件</span>
          ) : (
            <table className="runtime-panel__table">
              <thead>
                <tr>
                  <th>事件码</th>
                  <th>级别</th>
                  <th>首次</th>
                  <th>最近</th>
                  <th>次数</th>
                </tr>
              </thead>
              <tbody>
                {vm.diagnostics.map((entry) => (
                  <tr key={entry.event}>
                    <td className="runtime-panel__event-code">{entry.event}</td>
                    <td>
                      <Tag color={ALERT_TAG_COLOR[entry.tone]}>{entry.levelText}</Tag>
                    </td>
                    <td><RelativeTime iso={entry.firstSeen} /></td>
                    <td><RelativeTime iso={entry.lastSeen} /></td>
                    <td>{entry.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section aria-label="通知计数规则" className="runtime-panel__section">
          <h4 className="runtime-panel__section-title">计数规则</h4>
          <ul className="runtime-panel__rules">
            {vm.countingRules.map((rule) => (
              <li className="runtime-panel__muted" key={rule}>
                {rule}
              </li>
            ))}
          </ul>
        </section>
      </Drawer>
    </>
  );
}
