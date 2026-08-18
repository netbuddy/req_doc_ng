import { describe, expect, it } from 'vitest';
import { derivePublishSteps } from '../src/workbenches/PublicationWorkbench';

/**
 * 发布主工作台右栏竖向进度条的形态推导（任务 T20260720-pub-rail-vstepper 验收项 A1）。
 * 六个状态逐一对照 docs/proposals/publication-stepper-redesign/
 * 发布页定稿导出发布竖向进度条原型-v1.html 的状态画廊。
 */

const base = {
  finalized: false,
  converting: false,
  hasActionableCandidate: false,
  hasFailure: false,
  published: false,
};

describe('derivePublishSteps · 原型六状态', () => {
  it('① 编辑中 · 可定稿：第 1 步为当前，其后两步未达', () => {
    const [s1, s2, s3] = derivePublishSteps(base);

    expect(s1).toEqual({ node: 'active', line: 'flow', card: 'active' });
    expect(s2).toEqual({ node: 'pending', line: 'plain', card: 'dim' });
    expect(s3).toEqual({ node: 'pending', line: 'plain', card: 'dim' });
  });

  it('② 已定稿 · 转换中：第 1 步完成，第 2 步转换中（旋转环）', () => {
    const [s1, s2, s3] = derivePublishSteps({ ...base, finalized: true, converting: true });

    expect(s1).toEqual({ node: 'done', line: 'done', card: 'dim' });
    expect(s2).toEqual({ node: 'busy', line: 'flow', card: 'active' });
    expect(s3.node).toBe('pending');
  });

  it('③ 转换失败 · 就地处理：第 2 步为失败态，其下轨道不为推进段', () => {
    const [s1, s2, s3] = derivePublishSteps({ ...base, finalized: true, hasFailure: true });

    expect(s1.node).toBe('done');
    expect(s2).toEqual({ node: 'failed', line: 'plain', card: 'failed' });
    expect(s3.node).toBe('pending');
  });

  it('④ 候选件待检查：第 2 步为当前，轨道呈绿→蓝→灰', () => {
    const [s1, s2, s3] = derivePublishSteps({ ...base, finalized: true, hasActionableCandidate: true });

    expect(s1.line).toBe('done');
    expect(s2).toEqual({ node: 'active', line: 'flow', card: 'active' });
    expect(s3).toEqual({ node: 'pending', line: 'plain', card: 'dim' });
  });

  /* ⑤ 人工降级交付与④候选件待检查在本函数看来是同一个输入（都是「有可操作候选件」），
     形态因而必然相同——两者的差别只在卡内徽标与说明文字，由组件渲染而非本函数决定，
     故此处不设重复断言，人工降级的外观差异走浏览器走查核对。 */

  it('⑥ 已发布基线：三步全绿，第 3 步为承载详情的普通卡而非素卡', () => {
    const [s1, s2, s3] = derivePublishSteps({
      ...base,
      finalized: true,
      hasActionableCandidate: false,
      published: true,
    });

    expect(s1).toEqual({ node: 'done', line: 'done', card: 'dim' });
    expect(s2).toEqual({ node: 'done', line: 'done', card: 'dim' });
    expect(s3).toEqual({ node: 'done', line: 'plain', card: 'plain' });
  });
});

describe('derivePublishSteps · 分支优先级与回退', () => {
  it('未定稿时第 2 步恒为未达，纵有失败记录也不亮红', () => {
    const [, s2] = derivePublishSteps({ ...base, hasFailure: true, hasActionableCandidate: true });

    expect(s2.node).toBe('pending');
  });

  it('定稿被索引调整或条目修订回流作废后，当前步退回第 1 步', () => {
    const [s1, s2] = derivePublishSteps({ ...base, finalized: false, hasFailure: true });

    expect(s1.node).toBe('active');
    expect(s2.node).toBe('pending');
  });

  it('失败后重试成功：候选件优先于历史失败记录，第 2 步回到当前态', () => {
    const [, s2] = derivePublishSteps({
      ...base,
      finalized: true,
      hasActionableCandidate: true,
      hasFailure: true,
    });

    expect(s2.node).toBe('active');
  });

  it('转换中优先于其余一切：并存的候选件与失败记录都不改变旋转环', () => {
    const [, s2] = derivePublishSteps({
      ...base,
      finalized: true,
      converting: true,
      hasActionableCandidate: true,
      hasFailure: true,
    });

    expect(s2.node).toBe('busy');
  });

  it('已成基线时第 2 步收为完成态，不因残留失败记录变红', () => {
    const [, s2, s3] = derivePublishSteps({
      ...base,
      finalized: true,
      published: true,
      hasFailure: true,
    });

    expect(s2.node).toBe('done');
    expect(s3.node).toBe('done');
  });

  // 已发布只读复核态下，第 2 步必须恒为收束态（done），不因残留的在途转换或候选件被重新点亮。
  // 这两例钉住 published 在优先级链里排最前——正是 C1 死路（已发布态仍渲染写入按钮）的所在：
  // 若把 published 与 converting 优先级互换，第一例即转红。
  it('已发布态优先于转换中：残留在途转换不把已收束的第 2 步重新点亮为旋转环', () => {
    const [, s2] = derivePublishSteps({
      ...base,
      finalized: true,
      published: true,
      converting: true,
    });

    expect(s2.node).toBe('done');
  });

  it('已发布态优先于候选件待检查：残留候选件不把已收束的第 2 步拉回当前态', () => {
    const [, s2] = derivePublishSteps({
      ...base,
      finalized: true,
      published: true,
      hasActionableCandidate: true,
    });

    expect(s2.node).toBe('done');
  });

  it('开启新一轮后上一轮的基线不算数：定稿完成而未生成候选件时，第 2、3 步不显示为已完成', () => {
    // v0.1 单文档单基线，基线记录在新一轮里依然留存；完成与否只认文档状态 baseline_published。
    const [s1, s2, s3] = derivePublishSteps({ ...base, finalized: true, published: false });

    expect(s1.node).toBe('done');
    expect(s2.node).toBe('pending');
    expect(s3.node).toBe('pending');
  });
});
