// 能力清单与思考模式开关的文案纯测（T20260724-capability-probe-panel）。
// 后端只回封闭集代码与实测数值，措辞全在 view-models/settings.ts 里定：走查阶段改措辞不必动
// 后端，改完跑本文件即可。这里钉的是「每种结论都说得出人话，且说得对」。
import { describe, expect, it } from 'vitest';
import {
  buildCapabilityRows,
  buildThinkingMode,
  capabilityProbeStamp,
  thinkingFactsFromItems,
  thinkingFactsFromProfile,
} from '../src/view-models/settings';
import type { CapabilityItemRead, CapabilityKey } from '../src/api/settings';

function item(overrides: Partial<CapabilityItemRead> & { key: CapabilityKey }): CapabilityItemRead {
  return {
    state: 'unknown',
    mode: null,
    available: null,
    tier: null,
    tokens: null,
    source: null,
    note_code: null,
    outcome: null,
    latency_ms: null,
    detail: {},
    ...overrides,
  };
}

function row(
  items: CapabilityItemRead[],
  key: CapabilityKey,
  model = 'qwen3.6-27b',
  providerType = 'llama_cpp',
) {
  const found = buildCapabilityRows(items, model, providerType).find((r) => r.key === key);
  if (!found) {
    throw new Error(`清单里没有 ${key}`);
  }
  return found;
}

describe('能力清单文案', () => {
  it('探到思考段并找到关它的参数时，给出参数名与前后耗时对比', () => {
    const result = row([item({
      key: 'thinking', state: 'supported', mode: 'reasoning_effort', available: true,
      detail: {
        baseline_latency_ms: 24000,
        tried: [{ mode: 'reasoning_effort', has_thinking: false, latency_ms: 1400 }],
      },
    })], 'thinking');
    expect(result.tone).toBe('ok');
    expect(result.summary).toContain('具备思考能力');
    expect(result.summary).toContain('reasoning_effort=none');
    // 前后耗时是这条结论最有说服力的证据，必须出现在说明里，且按秒读
    expect(result.detail).toContain('24.0 秒');
    expect(result.detail).toContain('1.4 秒');
  });

  it('端点声明不支持思考时才说「不具备思考能力」', () => {
    const result = row([item({
      key: 'thinking', state: 'supported', mode: 'none', available: false,
    })], 'thinking');
    expect(result.tone).toBe('ok');
    expect(result.summary).toContain('不具备思考能力');
    // 说明里要交代「仍会下发」，否则用户以为产品从此不管这件事了
    expect(result.detail).toContain('仍按');
  });

  it('具备能力但服务端关掉时，不能说成「不具备思考能力」', () => {
    // 116 生产端点的真实形态：模板支持思考、服务端 -rea off 关掉，探测因而看不到思考段。
    const result = row([item({
      key: 'thinking', state: 'supported', mode: 'none', available: true,
      note_code: 'thinking_disabled_on_server',
    })], 'thinking');
    expect(result.summary).toContain('具备思考能力');
    expect(result.summary).toContain('服务端关着');
    expect(result.detail).toContain('-rea off');
    // 关键：给的下一步是改服务端，不是换模型
    expect(result.detail).toContain('不是换模型');
  });

  it('声明支持思考但端点没报「服务端已关闭」时，不套用 llama.cpp 的启动参数建议', () => {
    // 只凭「声明支持却没看到思考段」就说成服务端关的，会让 Ollama 用户照着 -rea off 去改一个
    // 根本不存在的配置。真实原因也可能只是这道探测题太简单、模型没展开思考。
    const result = row([item({
      key: 'thinking', state: 'supported', mode: 'none', available: true,
      note_code: 'thinking_declared_not_observed',
    })], 'thinking', 'qwen3.6-27b', 'ollama');
    expect(result.summary).toContain('具备思考能力');
    expect(result.detail).not.toContain('-rea off');
    expect(result.detail).toContain('从这一轮问不出是哪种');
  });

  it('vLLM 与通用兼容端点不说「仍按默认方式下发关思考参数」——先验就是一个字段都不发', () => {
    for (const providerType of ['vllm', 'openai_compatible']) {
      const result = row([item({
        key: 'thinking', state: 'supported', mode: 'none', available: null,
      })], 'thinking', 'qwen3.6-27b', providerType);
      expect(result.detail).toContain('不向这类服务下发关思考参数');
      expect(result.detail).not.toContain('仍按这类服务的默认方式下发');
    }
    // llama.cpp / ollama 的先验确实会下发，那句话对它们成立
    const llama = row([item({
      key: 'thinking', state: 'supported', mode: 'none', available: null,
    })], 'thinking', 'qwen3.6-27b', 'llama_cpp');
    expect(llama.detail).toContain('仍按这类服务的默认方式下发');
  });

  it('端点不声明能力又没探到思考段时，如实说判断不了', () => {
    const result = row([item({
      key: 'thinking', state: 'supported', mode: 'none', available: null,
    })], 'thinking');
    expect(result.summary).toContain('判断不了');
  });

  it('会思考却关不掉时（vLLM 缺服务端参数）给出可照做的下一步', () => {
    const result = row([item({
      key: 'thinking', state: 'degraded', available: true,
      note_code: 'vllm_needs_reasoning_parser',
    })], 'thinking');
    expect(result.tone).toBe('warn');
    expect(result.detail).toContain('--reasoning-parser');
    // 说清为什么不下发那个参数——否则用户会以为产品偷懒
    expect(result.detail).toContain('不会下发');
  });

  it('结构化输出的假成功要说破', () => {
    const result = row([item({
      key: 'structured', state: 'unsupported', tier: 'prompt_only',
      detail: { tried: [
        { tier: 'json_schema', ok: true, conforms: false },
        { tier: 'json_object', ok: true, conforms: false },
      ] },
    })], 'structured');
    expect(result.tone).toBe('bad');
    expect(result.detail).toContain('假成功');
  });

  it('上下文探明时说明已据此卡长度上限', () => {
    const result = row([item({
      key: 'context', state: 'supported', tokens: 32768, source: 'models.max_model_len',
    })], 'context');
    expect(result.summary).toContain('32,768');
    expect(result.detail).toContain('max_model_len');
    expect(result.detail).toContain('卡在窗口内');
  });

  it('Ollama 的模型上限只作参考，明说不据此截断', () => {
    const result = row([item({
      key: 'context', state: 'degraded', tokens: 262144, source: 'api_show.context_length',
      note_code: 'ollama_model_limit_only',
    })], 'context');
    expect(result.tone).toBe('warn');
    expect(result.summary).toContain('不是实际生效值');
    expect(result.detail).toContain('不据此卡长度上限');
  });

  it('探不到上下文时不给数字，只说没探到', () => {
    const result = row([item({ key: 'context', state: 'unknown' })], 'context');
    expect(result.summary).toBe('没探到');
    expect(result.detail).toContain('不会用猜的数字');
  });

  it('静默接受未知参数时解释它为何影响其他结论', () => {
    const result = row([item({ key: 'unknown_fields', state: 'degraded' })], 'unknown_fields');
    expect(result.tone).toBe('warn');
    expect(result.detail).toContain('不等于「参数生效」');
  });

  it('基线两项复用既有连通测试文案，失败时给出下一步', () => {
    const rows = buildCapabilityRows([
      item({ key: 'reachable', state: 'unsupported', outcome: 'unreachable' }),
      item({ key: 'generate', state: 'unknown' }),
    ], 'qwen3.6-27b', 'llama_cpp');
    expect(rows[0].tone).toBe('bad');
    expect(rows[0].detail).toContain('服务地址');
    expect(rows[1].summary).toContain('前一项没过');
  });

  it('清单顺序照后端给的来，前端不重排', () => {
    const keys: CapabilityKey[] = ['reachable', 'generate', 'thinking', 'structured', 'context', 'unknown_fields'];
    const rows = buildCapabilityRows(keys.map((key) => item({ key })), 'm', 'llama_cpp');
    expect(rows.map((r) => r.key)).toEqual(keys);
  });
});

describe('思考模式开关的说明与提醒', () => {
  const canTurnOff = { state: 'supported' as const, mode: 'reasoning_effort', available: true, noteCode: null };

  it('关着时只说明，不吓唬人', () => {
    const vm = buildThinkingMode(canTurnOff, false, 'ollama');
    expect(vm.warning).toBe('');
    expect(vm.warningTitle).toBe('');
    expect(vm.statusText).toContain('跳过思考过程');
  });

  it('用户执意打开时，把两条实测后果原样告诉他', () => {
    const vm = buildThinkingMode(canTurnOff, true, 'ollama');
    expect(vm.warningTone).toBe('warn');
    expect(vm.warningTitle).toContain('两个后果');
    expect(vm.warning).toContain('20–50 倍');
    expect(vm.warning).toContain('240 秒超时');
    expect(vm.warning).toContain('正文为空');
  });

  it('关不掉思考的端点：即使开关关着也要提醒它仍在思考', () => {
    const vm = buildThinkingMode(
      { state: 'degraded', mode: '', available: true, noteCode: 'vllm_needs_reasoning_parser' },
      false,
      'vllm',
    );
    expect(vm.warningTone).toBe('warn');
    expect(vm.warning).toContain('仍会带思考跑');
    expect(vm.warning).toContain('--reasoning-parser');
  });

  it('模型确实不具备思考能力时，打开只给「没作用」的说明而不是警告', () => {
    const vm = buildThinkingMode({ state: 'supported', mode: 'none', available: false, noteCode: null }, true, 'llama_cpp');
    expect(vm.warningTone).toBe('info');
    expect(vm.warning).toContain('不会有思考');
    expect(vm.warning).toContain('换一个');
  });

  it('服务端关掉思考时打开开关：说清闸门在服务端，别让人去换模型', () => {
    const vm = buildThinkingMode(
      { state: 'supported', mode: 'none', available: true, noteCode: 'thinking_disabled_on_server' },
      true,
      'llama_cpp',
    );
    expect(vm.statusText).toContain('服务端把思考关掉了');
    expect(vm.warningTitle).toContain('闸门在服务端');
    expect(vm.warning).toContain('不是换模型');
    expect(vm.warning).toContain('-rea off');
  });

  it('vLLM 没探明关思考方式时，不说「调用时会要求模型跳过思考」', () => {
    // 这类端点在探明之前一个关思考字段都不发，说「会要求模型跳过思考」是假话——用户日后排查
    // 「怎么还在思考」时，会去请求体里找一个根本不存在的字段。
    const vm = buildThinkingMode(
      { state: 'unknown', mode: '', available: null, noteCode: null }, false, 'vllm',
    );
    expect(vm.statusText).not.toContain('会要求模型跳过思考');
    expect(vm.statusText).toContain('取决于服务端设置');
  });

  it('vLLM 一旦探明了有效方式，就照实说会下发', () => {
    const vm = buildThinkingMode(
      { state: 'supported', mode: 'reasoning_effort', available: true, noteCode: null }, false, 'vllm',
    );
    expect(vm.statusText).toContain('跳过思考过程');
  });

  it('没探测过时如实说没探明', () => {
    const vm = buildThinkingMode({ state: 'unknown', mode: '', available: null, noteCode: null }, false, 'llama_cpp');
    expect(vm.statusText).toContain('还没探明');
  });
});

describe('思考结论的两个来源同形状', () => {
  it('从这一轮探测结果里读', () => {
    const facts = thinkingFactsFromItems([
      item({ key: 'thinking', state: 'supported', mode: 'enable_thinking', available: true }),
    ]);
    expect(facts).toEqual({ state: 'supported', mode: 'enable_thinking', available: true, noteCode: null });
  });

  it('从已保存的档案里读', () => {
    const facts = thinkingFactsFromProfile({
      thinking: { off_state: 'supported', off_mode: 'reasoning_effort', available: true },
    });
    expect(facts.mode).toBe('reasoning_effort');
    expect(facts.available).toBe(true);
  });

  it('没档案、坏档案都回落「没探明」，不报错', () => {
    expect(thinkingFactsFromProfile(null).state).toBe('unknown');
    expect(thinkingFactsFromProfile({}).state).toBe('unknown');
    expect(thinkingFactsFromProfile({ thinking: '坏数据' }).state).toBe('unknown');
  });

  it('落款没时间戳时给空串（不显示一个空壳）', () => {
    expect(capabilityProbeStamp(null)).toBe('');
    expect(capabilityProbeStamp('2026-07-24T18:30:00+08:00')).toContain('探测于');
  });
});
