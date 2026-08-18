/**
 * 交付失败块 VM（口径设计 §5.5）：lane × 失败关卡投影。
 * 覆盖：失败率/分数派生、失败关卡按规范列对齐（含未分关补零）、
 * 失败率降序排序、严重度色阈值（暂定）、空数组。
 * 交付失败＝AI 未能交出合法结论（judgement=*_failed），与拒绝率不同维度。
 */
import { describe, expect, it } from 'vitest';
import type { AiDeliveryFailureRead, AiEffectivenessRead } from '../src/api/ai-effectiveness';
import {
  DELIVERY_FAILURE_STAGE_ORDER,
  buildDeliveryFailures,
} from '../src/view-models/overview';

function ai(delivery_failures: AiDeliveryFailureRead[]): AiEffectivenessRead {
  return { delivery_failures } as unknown as AiEffectivenessRead;
}

describe('buildDeliveryFailures', () => {
  it('派生失败率/分数并按规范列对齐失败关卡（未分关补零）', () => {
    const [row] = buildDeliveryFailures(
      ai([
        {
          stage: 'item_diagnosis',
          total: 10,
          failed: 4,
          by_failure_stage: [
            { failure_stage: 'synthesis', count: 2 },
            { failure_stage: 'parse', count: 1 },
            { failure_stage: 'unclassified', count: 1 },
          ],
        },
      ]),
    );
    expect(row.laneLabel).toBe('条目诊断');
    expect(row.rateText).toBe('40%');
    expect(row.ratePercent).toBe(40);
    expect(row.scoreText).toBe('4 / 10');
    expect(row.tone).toBe('red'); // >20% 高
    // 单格按规范列顺序对齐，缺失关卡补 0
    expect(row.cells.map((c) => c.failureStage)).toEqual([...DELIVERY_FAILURE_STAGE_ORDER]);
    const byStage = Object.fromEntries(row.cells.map((c) => [c.failureStage, c.count]));
    expect(byStage).toEqual({
      parse: 1,
      llm_error: 0,
      structure: 0,
      aggregation: 0,
      synthesis: 2,
      unclassified: 1,
    });
  });

  it('按失败率降序排序，高失败 lane 前置', () => {
    const rows = buildDeliveryFailures(
      ai([
        { stage: 'source_intake', total: 20, failed: 1, by_failure_stage: [{ failure_stage: 'unclassified', count: 1 }] },
        { stage: 'item_diagnosis', total: 10, failed: 6, by_failure_stage: [{ failure_stage: 'synthesis', count: 6 }] },
        { stage: 'item_formation', total: 20, failed: 3, by_failure_stage: [{ failure_stage: 'unclassified', count: 3 }] },
      ]),
    );
    expect(rows.map((r) => r.key)).toEqual(['item_diagnosis', 'item_formation', 'source_intake']);
    expect(rows.map((r) => r.tone)).toEqual(['red', 'orange', 'green']); // 60% / 15% / 5%
  });

  it('零失败 lane 失败率 0%、色 green、单格全零', () => {
    const [row] = buildDeliveryFailures(
      ai([{ stage: 'source_intake', total: 5, failed: 0, by_failure_stage: [] }]),
    );
    expect(row.rateText).toBe('0%');
    expect(row.tone).toBe('green');
    expect(row.cells.every((c) => c.count === 0)).toBe(true);
  });

  it('未知 lane 稳定码回落为原码，不崩', () => {
    const [row] = buildDeliveryFailures(
      ai([{ stage: 'mystery_lane', total: 2, failed: 1, by_failure_stage: [{ failure_stage: 'unclassified', count: 1 }] }]),
    );
    expect(row.laneLabel).toBe('mystery_lane');
    expect(row.rateText).toBe('50%');
  });

  it('无交付失败数据 → 空数组', () => {
    expect(buildDeliveryFailures(ai([]))).toEqual([]);
  });
});
