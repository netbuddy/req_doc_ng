// 模型服务多 provider 与两级连通测试的 VM 纯测（T20260720-model-provider-registry）
// 结果文案在这里定：后端只回封闭集结果码，走查阶段改措辞不必动后端，改完这里跑本文件即可。
import { describe, expect, it } from 'vitest';
import {
  baseUrlHint,
  connectionResultText,
  emptyProviderDraft,
  providerDraftFrom,
  providerDraftToWrite,
  providerListStamp,
  resolveTestKeyUsage,
  validateProviderDrafts,
  type ProviderDraftVM,
} from '../src/view-models/settings';
import type {
  ConnectionOutcome,
  LlmProviderListRead,
  LlmProviderRead,
  ModelConnectionTestResult,
} from '../src/api/settings';

function providerRead(overrides: Partial<LlmProviderRead> = {}): LlmProviderRead {
  return {
    id: 'default',
    name: '本地 llama.cpp',
    provider_type: 'llama_cpp',
    base_url: 'http://127.0.0.1:8084/v1',
    model: 'qwen2.5',
    timeout_seconds: 180,
    max_retries: 3,
    concurrency_limit: 5,
    api_key_set: false,
    active: true,
    thinking_enabled: false,
    capability_profile: {},
    ...overrides,
  };
}

function draft(overrides: Partial<ProviderDraftVM> = {}): ProviderDraftVM {
  return { ...providerDraftFrom(providerRead()), ...overrides };
}

function testResult(overrides: Partial<ModelConnectionTestResult> = {}): ModelConnectionTestResult {
  return {
    ok: true,
    latency_ms: 42,
    model_count: null,
    error_code: null,
    level: 'reachability',
    outcome: 'ok',
    model_listed: null,
    reply_length: null,
    models: [],
    ...overrides,
  };
}

describe('provider 草稿与写入命令', () => {
  it('读投影转草稿：密钥只带「是否已设置」，明文永不出现', () => {
    const d = providerDraftFrom(providerRead({ api_key_set: true }));
    expect(d.apiKeySet).toBe(true);
    expect(d.apiKeyInput).toBe('');
    expect(d.clearApiKey).toBe(false);
  });

  it('新增草稿就地派号，因此刚加的一条也能立刻设为使用中', () => {
    const fresh = emptyProviderDraft();
    expect(fresh.id).not.toBe('');
    // 字符集与后端 provider 标识校验一致（字母数字连字符下划线，至多 40 位）
    expect(fresh.id).toMatch(/^[A-Za-z0-9_-]{1,40}$/);
    expect(providerDraftToWrite(fresh).id).toBe(fresh.id);
  });

  it('两次新增派出的号不撞车', () => {
    expect(emptyProviderDraft().id).not.toBe(emptyProviderDraft().id);
  });

  it('数字字段非法时回落默认值，不把 NaN 发给后端', () => {
    const write = providerDraftToWrite(draft({ timeoutSeconds: '', maxRetries: '-1', concurrencyLimit: 'abc' }));
    expect(write.timeout_seconds).toBe(180);
    expect(write.max_retries).toBe(3);
    expect(write.concurrency_limit).toBe(5);
  });

  it('密钥留空=不改（发 null），显式清除单独走 clear 标志', () => {
    expect(providerDraftToWrite(draft({ apiKeyInput: '  ' })).api_key).toBeNull();
    expect(providerDraftToWrite(draft({ apiKeyInput: 'sk-x' })).api_key).toBe('sk-x');
    expect(providerDraftToWrite(draft({ clearApiKey: true })).clear_api_key).toBe(true);
  });

  it('没应用过新档案就不发能力档案字段，让后端「缺席=保留原值」的保护生效', () => {
    // 界面若每次保存都把读回来的档案原样发回去，那条保护永远走不到：拿着一个开了很久的页面点
    // 一次普通保存，就会把别处刚探明的档案覆盖成页面上那份旧的。
    const stale = draft({ capabilityProfile: { thinking: { off_state: 'unknown' } } });
    expect(providerDraftToWrite(stale).capability_profile).toBeNull();

    const applied = draft({
      capabilityProfile: { thinking: { off_state: 'supported', off_mode: 'reasoning_effort' } },
      capabilityProfileChanged: true,
    });
    expect(providerDraftToWrite(applied).capability_profile).toEqual({
      thinking: { off_state: 'supported', off_mode: 'reasoning_effort' },
    });
  });

  it('名称/地址/模型两端空白被裁掉', () => {
    const write = providerDraftToWrite(draft({ name: ' 甲 ', baseUrl: ' http://a/v1 ', model: ' m ' }));
    expect([write.name, write.base_url, write.model]).toEqual(['甲', 'http://a/v1', 'm']);
  });
});

describe('保存前的就地校验', () => {
  it('全部合规返回 null', () => {
    expect(validateProviderDrafts([draft()])).toBeNull();
  });

  it('一个都不剩时拦下', () => {
    expect(validateProviderDrafts([])).toContain('至少');
  });

  it.each([
    [{ name: '  ' }, '名称'],
    [{ baseUrl: '' }, '服务地址'],
    [{ model: '' }, '模型标识'],
    [{ baseUrl: '192.168.0.9:11434/v1' }, 'http://'],
  ])('缺项被指名道姓地报出来：%o', (patch, hint) => {
    expect(validateProviderDrafts([draft(patch as Partial<ProviderDraftVM>)])).toContain(hint);
  });

  it('重名被拦下（列表靠名称辨识，重名没法用）', () => {
    expect(validateProviderDrafts([draft({ name: '甲' }), draft({ name: '甲' })])).toContain('重复');
  });
});

describe('服务地址写法提醒', () => {
  it('以 /v1 结尾不提醒', () => {
    expect(baseUrlHint('http://127.0.0.1:8084/v1')).toBeNull();
    expect(baseUrlHint('http://127.0.0.1:8084/v1/')).toBeNull();
  });

  it('漏了 /v1 时提醒（这是最常见的填错）', () => {
    expect(baseUrlHint('http://127.0.0.1:8084')).toContain('/v1');
  });

  it('还没填完不打扰', () => {
    expect(baseUrlHint('')).toBeNull();
    expect(baseUrlHint('127.0.0.1')).toBeNull();
  });
});

describe('两级测试结果的白话文案', () => {
  it('第一级通过：报延迟与模型数，并指出下一步可以测第二级', () => {
    const vm = connectionResultText(
      testResult({ model_count: 3, model_listed: true, models: ['qwen2.5'] }),
      'qwen2.5',
    );
    expect(vm.tone).toBe('success');
    expect(vm.title).toContain('42 毫秒');
    expect(vm.detail).toContain('3 个模型');
    expect(vm.detail).toContain('qwen2.5');
    expect(vm.detail).toContain('能正常回答');
  });

  it('第二级通过：报回复字数，不带回正文', () => {
    const vm = connectionResultText(testResult({ level: 'generation', reply_length: 2 }), 'qwen2.5');
    expect(vm.tone).toBe('success');
    expect(vm.title).toContain('正常回复');
    expect(vm.detail).toContain('2 个字');
  });

  it.each<[ConnectionOutcome, string, string]>([
    ['unreachable', '连不上', '是否已启动'],
    ['timeout', '等不到响应', '超时时间'],  // 标题按秒说，明细指向可调的超时设置
    ['auth_failed', 'API Key', '过期'],
    ['bad_response', '不是预期格式', '/v1'],
  ])('失败形态 %s 给出结论与下一步该看哪儿', (outcome, titlePart, detailPart) => {
    const vm = connectionResultText(testResult({ ok: false, outcome }), 'qwen2.5');
    expect(vm.tone).toBe('error');
    expect(vm.title).toContain(titlePart);
    expect(vm.detail).toContain(detailPart);
  });

  it('超时按秒说，不让读者自己换算毫秒', () => {
    const vm = connectionResultText(testResult({ ok: false, outcome: 'timeout', latency_ms: 8040 }), 'm');
    expect(vm.title).toContain('8 秒');
    expect(vm.title).not.toContain('毫秒');
  });

  it('模型不存在：报出服务上实际有哪些，用户能照着改', () => {
    const vm = connectionResultText(
      testResult({ ok: false, outcome: 'model_missing', models: ['qwen2.5:7b', 'llama3:8b'] }),
      'qwen2.5',
    );
    expect(vm.title).toContain('qwen2.5');
    expect(vm.detail).toContain('qwen2.5:7b');
  });

  it('模型不存在且列表拿不到时，提示 Ollama 的标签写法', () => {
    const vm = connectionResultText(testResult({ ok: false, outcome: 'model_missing' }), 'qwen2.5');
    expect(vm.detail).toContain('qwen2.5:7b');
  });

  it('文案里不出现内部词', () => {
    const all = (['unreachable', 'timeout', 'auth_failed', 'model_missing', 'bad_response'] as ConnectionOutcome[])
      .map((outcome) => connectionResultText(testResult({ ok: false, outcome }), 'm'))
      .flatMap((vm) => [vm.title, vm.detail])
      .join('');
    for (const jargon of ['兜底', '探针', '契约', 'provider', 'outcome', 'lane']) {
      expect(all).not.toContain(jargon);
    }
  });
});

describe('测试连接时是否用已存密钥（外泄面守卫的前端配合，C1）', () => {
  it('现输了密钥就用现输的，与已存密钥无关', () => {
    const r = resolveTestKeyUsage({
      typedKey: 'sk-x',
      apiKeySet: true,
      draftBaseUrl: 'http://a/v1',
      savedBaseUrl: 'http://b/v1',
    });
    expect(r.useSavedKey).toBe(false);
    expect(r.savedKeyBlockedHint).toBeNull();
  });

  it('没存过密钥：既不带已存密钥，也不提示', () => {
    const r = resolveTestKeyUsage({
      typedKey: '',
      apiKeySet: false,
      draftBaseUrl: 'http://a/v1',
      savedBaseUrl: 'http://a/v1',
    });
    expect(r.useSavedKey).toBe(false);
    expect(r.savedKeyBlockedHint).toBeNull();
  });

  it('存过密钥且地址与保存时一致：用已存密钥（结尾斜杠差异忽略）', () => {
    const r = resolveTestKeyUsage({
      typedKey: '  ',
      apiKeySet: true,
      draftBaseUrl: 'http://a/v1/',
      savedBaseUrl: 'http://a/v1',
    });
    expect(r.useSavedKey).toBe(true);
    expect(r.savedKeyBlockedHint).toBeNull();
  });

  it('存过密钥但地址被改过：不带已存密钥并给出提示（避免撞后端 400）', () => {
    const r = resolveTestKeyUsage({
      typedKey: '',
      apiKeySet: true,
      draftBaseUrl: 'https://somewhere-else/v1',
      savedBaseUrl: 'http://a/v1',
    });
    expect(r.useSavedKey).toBe(false);
    expect(r.savedKeyBlockedHint).toContain('重新输入密钥');
  });

  it('存过密钥但服务端没有对应已存地址（新草稿）：不带已存密钥', () => {
    const r = resolveTestKeyUsage({
      typedKey: '',
      apiKeySet: true,
      draftBaseUrl: 'http://a/v1',
      savedBaseUrl: null,
    });
    expect(r.useSavedKey).toBe(false);
    expect(r.savedKeyBlockedHint).toContain('重新输入密钥');
  });
});

describe('列表落款', () => {
  function listRead(overrides: Partial<LlmProviderListRead> = {}): LlmProviderListRead {
    return {
      active_provider_id: 'default',
      providers: [providerRead()],
      provider_types: [],
      source: 'env',
      updated_at: null,
      updated_by: null,
      ...overrides,
    };
  }

  it('尚未保存过时说明沿用原有配置，不显示假的保存时间', () => {
    const text = providerListStamp(listRead());
    expect(text).toContain('尚未保存过');
    expect(text).toContain('无保存记录');
  });

  it('已保存时带上时刻与操作人', () => {
    const text = providerListStamp(
      listRead({ source: 'saved', updated_at: '2026-07-20T10:30:00', updated_by: 'U1' }),
    );
    expect(text).toContain('已保存配置');
    expect(text).toContain('U1');
  });

  it('还没加载出来时不编造内容', () => {
    expect(providerListStamp(null)).toBe('—');
  });
});
