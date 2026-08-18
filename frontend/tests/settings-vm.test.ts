// 设置工作台 VM 纯测（DTO → VM，无渲染）
import { describe, expect, it } from 'vitest';
import {
  buildConventionCatalog,
  buildDomainForm,
  buildExportReadiness,
  buildSettingsMenu,
  formatUpdatedStamp,
  toSaveValues,
  validateDomainValues,
} from '../src/view-models/settings';
import type {
  ConfigDomainRead,
  ConfigDomainStatusRead,
  ExportReadinessItemRead,
  ExportReadinessRead,
  RequirementConventionCatalogRead,
} from '../src/api/settings';

function status(overrides: Partial<ConfigDomainStatusRead> = {}): ConfigDomainStatusRead {
  return {
    domain: 'model_service',
    label: '模型服务',
    group: '外部能力',
    downstream: '模型服务适配器',
    configured: false,
    source: 'env',
    updated_at: null,
    updated_by: null,
    ...overrides,
  };
}

function domainRead(overrides: Partial<ConfigDomainRead> = {}): ConfigDomainRead {
  return {
    domain: 'model_service',
    label: '模型服务',
    group: '外部能力',
    downstream: '模型服务适配器',
    source: 'saved',
    updated_at: '2026-07-05T10:30:00+00:00',
    updated_by: 'U1',
    fields: [
      { key: 'base_url', value: 'http://llm.local/v1', source: 'saved' },
      { key: 'model', value: 'qwen-plus', source: 'saved' },
      { key: 'timeout_seconds', value: 60, source: 'env' },
    ],
    secrets: [{ key: 'api_key', set: true, placeholder: '••••••••' }],
    ...overrides,
  };
}

describe('buildSettingsMenu', () => {
  it('菜单恒为六组：身份与权限 / 外部能力 / 生成治理 / 文档资源 / 项目 / 个性化', () => {
    const groups = buildSettingsMenu(null);
    expect(groups.map((group) => group.title)).toEqual([
      '身份与权限', '外部能力', '生成治理', '文档资源', '项目', '个性化',
    ]);
    expect(groups[1].items.map((item) => item.key)).toEqual([
      'model_service', 'export', 'document_template', 'chart_rendering',
    ]);
    expect(groups[2].items.map((item) => item.key)).toEqual(['requirement_convention']);
    // 文档资源（T20260721）：撰写文档时取用的素材目录，既不连外部服务也不影响模型生成行为。
    expect(groups[3].items.map((item) => item.key)).toEqual(['reference_standards']);
    // 项目危险区（AEP-113）：非配置存储，状态签恒「危险区」。
    expect(groups[4].items).toEqual([
      { key: 'project', label: '项目管理', statusText: '危险区', statusTone: 'pending' },
    ]);
  });

  it('文档模板域不走 config：状态签由已启用模板数派生（tone=default）', () => {
    const withCount = buildSettingsMenu(null, { documentTemplateCount: 4 });
    const dt = withCount[1].items.find((item) => item.key === 'document_template');
    expect(dt).toMatchObject({ statusText: '4 个可用', statusTone: 'default' });
    // 无计数时回落 '—'（后端不可达/未加载）
    const dtNoCount = buildSettingsMenu(null)[1].items.find((item) => item.key === 'document_template');
    expect(dtNoCount?.statusText).toBe('—');
  });

  it('状态签：已配置=saved；默认值=env；用户与权限恒待接入；外观恒本地偏好', () => {
    const groups = buildSettingsMenu([
      status({ domain: 'model_service', configured: true, source: 'saved' }),
      status({ domain: 'export', label: '导出能力' }),
      status({ domain: 'chart_rendering', label: '图表渲染' }),
    ]);
    const capability = groups[1].items;
    expect(capability[0]).toMatchObject({ statusText: '已配置', statusTone: 'configured' });
    expect(capability[1]).toMatchObject({ statusText: '默认值', statusTone: 'default' });
    expect(groups[0].items[0]).toMatchObject({ statusText: '待接入', statusTone: 'pending' });
    expect(groups[5].items[0]).toMatchObject({ key: 'appearance', statusText: '本地偏好', statusTone: 'local' });
  });

  it('生成治理组的需求规约域走后端状态签（已配置/默认值）', () => {
    const groups = buildSettingsMenu([
      status({ domain: 'requirement_convention', label: '需求规约', group: '生成治理', configured: true, source: 'saved' }),
    ]);
    expect(groups[2].items[0]).toMatchObject({
      key: 'requirement_convention',
      statusText: '已配置',
      statusTone: 'configured',
    });
  });

  it('后端不可达时不显示假状态（statusText 为 —）', () => {
    const groups = buildSettingsMenu(null);
    expect(groups[1].items.every((item) => item.statusText === '—')).toBe(true);
  });
});

describe('buildConventionCatalog', () => {
  const catalog: RequirementConventionCatalogRead = {
    active_convention: 'boilerplate-cn',
    conventions: [
      {
        convention_key: 'ears-cn',
        display_name: '中文 EARS',
        blueprint: 'EARS 五句型中文化（§3.1）',
        positioning: '基础句型规范：简单、易读。适合快速梳理。',
        pattern_overview: [{ label: '功能需求', pattern: '「当…系统应…」' }],
        examples: [{ req_type: 'functional', statement: '当用户提交时，系统应生成列表' }],
      },
      {
        convention_key: 'boilerplate-cn',
        display_name: '中文 Boilerplates',
        blueprint: '槽位化样板（§3.3）',
        positioning: '结构化程度高。',
        pattern_overview: [{ label: '功能需求', pattern: '「系统应为…提供…能力」' }],
        examples: [{ req_type: 'quality', statement: '在不超过 500 条时，30 秒内生成草稿' }],
      },
    ],
  };

  it('active 方案打生效标；tagline 取定位首句；示例类型码映射中文名', () => {
    const vm = buildConventionCatalog(catalog);
    expect(vm.activeKey).toBe('boilerplate-cn');
    expect(vm.cards.map((c) => [c.key, c.active])).toEqual([
      ['ears-cn', false],
      ['boilerplate-cn', true],
    ]);
    expect(vm.cards[0].tagline).toBe('基础句型规范：简单、易读。');
    expect(vm.detailByKey['ears-cn'].examples[0].typeLabel).toBe('功能需求');
    expect(vm.detailByKey['boilerplate-cn'].examples[0].typeLabel).toBe('质量属性');
  });

  it('null 目录降级为空 VM（后端不可达不崩）', () => {
    const vm = buildConventionCatalog(null);
    expect(vm).toEqual({ activeKey: '', cards: [], detailByKey: {} });
  });
});

describe('formatUpdatedStamp（settings.ts 与 SettingsWorkbench 共用，格式实现唯一）', () => {
  it('落款＝本地分钟 · 操作人（跨日样本：17:30Z ⇒ 次日 01:30）', () => {
    expect(formatUpdatedStamp('2026-07-04T17:30:00+00:00', 'U1', '尚未保存过')).toBe(
      '2026-07-05 01:30 · U1',
    );
  });

  it('缺时刻或缺操作人 → 调用方回退文案（两页措辞不同，不得写死在实现里）', () => {
    expect(formatUpdatedStamp(null, null, '尚未保存过')).toBe('尚未保存过');
    expect(formatUpdatedStamp('2026-07-04T17:30:00+00:00', null, '尚未保存过')).toBe('尚未保存过');
    expect(formatUpdatedStamp(null, 'U1', '尚未保存过（默认生效：中文 EARS）')).toBe(
      '尚未保存过（默认生效：中文 EARS）',
    );
  });
});

describe('buildDomainForm', () => {
  it('字段按基础连接/调用参数分组，标注生效值来源', () => {
    const form = buildDomainForm(domainRead());
    expect(form.connectionFields.map((field) => field.key)).toEqual(['base_url', 'model']);
    expect(form.paramFields.map((field) => field.key)).toEqual(['timeout_seconds']);
    expect(form.connectionFields[0].sourceText).toBe('已保存');
    expect(form.paramFields[0].sourceText).toBe('env 默认');
    // 字面量断言（原 toContain('U1') 对时区错误不敏感，issue #21 得以久居）：
    // wire 10:30Z ⇒ 所钉 +8 本地 18:30；旧 slice 手法会给出 UTC 原串 10:30。
    expect(form.updatedText).toBe('2026-07-05 18:30 · U1');
  });

  it('密钥只读投影：已设置时仅有脱敏占位，VM 不含明文', () => {
    const form = buildDomainForm(domainRead());
    expect(form.secrets).toEqual([
      { key: 'api_key', label: 'API Key', set: true, placeholder: '••••••••' },
    ]);
    expect(JSON.stringify(form)).not.toContain('sk-');
  });
});

describe('toSaveValues', () => {
  it('只提交被编辑过的字段；数字字段回转数字；非法数字跳过', () => {
    const form = buildDomainForm(domainRead());
    const values = toSaveValues(form, {
      base_url: 'http://new.local/v1',
      model: 'qwen-plus', // 与原值相同 → 不提交
      timeout_seconds: '90',
    });
    expect(values).toEqual({ base_url: 'http://new.local/v1', timeout_seconds: 90 });
    expect(toSaveValues(form, { timeout_seconds: 'abc' })).toEqual({});
  });
});

describe('导出域：僵尸字段删除后（T20260724 A1）', () => {
  it('导出域表单只剩导出目录一个字段，「转换超时」不再出现', () => {
    const form = buildDomainForm({
      domain: 'export',
      label: '导出能力',
      group: '外部能力',
      downstream: '文档转换适配器',
      source: 'env',
      updated_at: null,
      updated_by: null,
      fields: [{ key: 'export_dir', value: '/var/exports', source: 'env' }],
      secrets: [],
    });
    expect(form.connectionFields.map((f) => f.label)).toEqual(['导出目录']);
    expect(form.paramFields).toEqual([]);
    expect(JSON.stringify(form)).not.toContain('转换超时');
  });
});

describe('buildExportReadiness（T20260724 A2）', () => {
  function readiness(items: ExportReadinessItemRead[], allReady: boolean): ExportReadinessRead {
    return { checked_at: '2026-07-24T10:30:00+00:00', all_ready: allReady, items };
  }

  it('就绪项：能力名用用户视角，说明给依赖名＋版本号＋定位到的路径', () => {
    const vm = buildExportReadiness(
      readiness(
        [
          {
            key: 'pdf_preview',
            ready: true,
            outcome: 'ready',
            path: '/usr/bin/soffice',
            // 各工具自报的版本串格式不一，VM 只取其中的版本号
            version: 'LibreOffice 24.2.7.2 420(Build:2)',
          },
          { key: 'mermaid_diagram', ready: true, outcome: 'ready', path: '/n/bin/mmdc', version: '11.16.0' },
          {
            key: 'plantuml_diagram',
            ready: true,
            outcome: 'ready',
            path: '/var/tools/plantuml.jar',
            version: 'PlantUML version 1.2024.7 (Sat Sep 07 04:18:17 PDT 2024)',
          },
        ],
        true,
      ),
    );
    expect(vm.rows.map((r) => r.capability)).toEqual(['文档转 PDF 预览', '流程图渲染', '结构图渲染']);
    expect(vm.rows.map((r) => r.statusText)).toEqual(['就绪', '就绪', '就绪']);
    expect(vm.rows[0].detail).toBe('LibreOffice 24.2.7.2 · /usr/bin/soffice');
    expect(vm.rows[1].detail).toBe('mermaid-cli 11.16.0 · /n/bin/mmdc');
    expect(vm.rows[2].detail).toBe('PlantUML 1.2024.7 · /var/tools/plantuml.jar');
    expect(vm.allReady).toBe(true);
    expect(vm.summary).toBe('导出所需的本地工具已全部就绪。');
    expect(vm.checkedText).toBe('检测于 2026-07-24 18:30');
  });

  it('缺失项：逐个结果码给出对应的白话后果，且能区分缺 Java 还是缺 plantuml.jar', () => {
    const vm = buildExportReadiness(
      readiness(
        [
          { key: 'pdf_preview', ready: false, outcome: 'soffice_missing', path: null, version: null },
          { key: 'mermaid_diagram', ready: false, outcome: 'mmdc_missing', path: null, version: null },
          { key: 'plantuml_diagram', ready: false, outcome: 'java_missing', path: null, version: null },
        ],
        false,
      ),
    );
    expect(vm.rows.map((r) => r.statusText)).toEqual(['缺失', '缺失', '缺失']);
    expect(vm.rows[0].detail).toContain('精确预览');
    expect(vm.rows[0].detail).toContain('导出的 Word 文件本身不受影响');
    expect(vm.rows[1].detail).toContain('流程图会以源码文本呈现');
    expect(vm.rows[2].detail).toContain('没找到 Java 运行环境');
    expect(vm.summary).toBe('有 3 项能力缺少本地工具，导出仍可进行，但下面这些效果会打折扣。');

    const jarMissing = buildExportReadiness(
      readiness(
        [{ key: 'plantuml_diagram', ready: false, outcome: 'plantuml_jar_missing', path: '/x/java', version: null }],
        false,
      ),
    );
    expect(jarMissing.rows[0].detail).toContain('没找到 plantuml.jar');
  });

  it('版本取不到不影响就绪结论：说明退回只写依赖名与路径', () => {
    const vm = buildExportReadiness(
      readiness([{ key: 'mermaid_diagram', ready: true, outcome: 'ready', path: '/n/bin/mmdc', version: null }], true),
    );
    expect(vm.rows[0].ready).toBe(true);
    expect(vm.rows[0].detail).toBe('mermaid-cli · /n/bin/mmdc');
  });

  it('结构图两条要连屏幕预览的后果一起讲（PlantUML 由后端渲染，缺工具时预览也坏）', () => {
    const vm = buildExportReadiness(
      readiness(
        [
          { key: 'plantuml_diagram', ready: false, outcome: 'java_missing', path: null, version: null },
          { key: 'mermaid_diagram', ready: false, outcome: 'mmdc_missing', path: null, version: null },
        ],
        false,
      ),
    );
    expect(vm.rows[0].detail).toContain('屏幕预览会显示一条渲染失败提示');
    // mermaid 在浏览器里渲染、不经后端，缺 mmdc 确实只影响导出文件——这条不该跟着改口径
    expect(vm.rows[1].detail).not.toContain('屏幕预览');
  });

  it('后端多出一种没见过的能力：那一行降级显示，其余各行照常', () => {
    const vm = buildExportReadiness(
      readiness(
        [
          { key: 'pdf_preview', ready: true, outcome: 'ready', path: '/usr/bin/soffice', version: '24.2' },
          // 旧前端包遇上新后端时的形态：key 与 outcome 都是本地映射里没有的
          { key: 'pdf_signature' as never, ready: false, outcome: 'signer_missing' as never, path: null, version: null },
        ],
        false,
      ),
    );
    expect(vm.rows).toHaveLength(2);
    expect(vm.rows[0].capability).toBe('文档转 PDF 预览'); // 已探到的结果没被未知行拖累
    expect(vm.rows[1].capability).toBe('pdf_signature');
    expect(vm.rows[1].detail).toBe('本机缺少这项能力依赖的工具。');
  });
});

describe('validateDomainValues：导出目录的形态校验（与后端 save_domain 同一口径）', () => {
  it('相对路径与 ~ 前缀在提交前就拦下，并说清该怎么填', () => {
    for (const bad of ['exports', '~/exports', './exports', '../exports']) {
      expect(validateDomainValues({ export_dir: bad })).toBe(
        '「导出目录」需填绝对路径（以 / 开头），不支持 ~ 与相对路径',
      );
    }
  });

  it('绝对路径放行；空串＝清掉保存值回落 env，不算坏值；其它字段不受这道校验管', () => {
    expect(validateDomainValues({ export_dir: '/var/exports' })).toBeNull();
    expect(validateDomainValues({ export_dir: '   ' })).toBeNull();
    expect(validateDomainValues({ base_url: 'http://x.local/v1', timeout_seconds: 90 })).toBeNull();
  });
});
