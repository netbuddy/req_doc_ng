import { describe, expect, it } from 'vitest';
import type { TemplateDraftRead, TemplateRegistryRead } from '../src/api/templates';
import type { TemplateDescriptorRead } from '../src/api/publication';
import {
  buildDraftRows,
  buildTemplateOptions,
  buildTemplatePreview,
  buildTemplateRows,
} from '../src/view-models/templates';

function row(overrides: Partial<TemplateRegistryRead>): TemplateRegistryRead {
  return {
    registry_ref: 'r1',
    template_key: 'srs-iso29148-v1',
    version_no: 1,
    name: '需求规格说明',
    schema_version: '1.0',
    doc_type: 'srs',
    content_hash: 'a'.repeat(64),
    source: 'builtin',
    status: 'active',
    registered_by: 'system',
    registered_at: '2026-07-03T10:00:00+00:00',
    ...overrides,
  };
}

function draft(overrides: Partial<TemplateDraftRead>): TemplateDraftRead {
  return {
    draft_ref: 'd1',
    name: '需求规格说明草稿',
    payload: '{}',
    origin: 'blank',
    created_by: 'U1',
    created_at: '2026-07-04T10:00:00+00:00',
    updated_at: '2026-07-04T10:00:00+00:00',
    ...overrides,
  };
}

describe('模板时刻落本地时区（issue #21：原 slice(0,19) 直示 UTC 原串）', () => {
  it('登记时刻：wire +00:00 → 本地时刻（TZ 由 vite.config 钉 Asia/Shanghai，字面量断言）', () => {
    // issue #21 实证样本(其现场为 UTC-7 主机,故 issue 记本地 07-09 23:48;
    // 测试恒按 vite.config 所钉 +8 断言 ⇒ 14:48。旧 slice 手法会给出 UTC 原串 06:48，在此必挂。
    const [vm] = buildTemplateRows([row({ registered_at: '2026-07-10T06:48:19.687564+00:00' })]);
    expect(vm.registeredAtText).toBe('2026-07-10 14:48:19');
  });

  it('草稿更新时刻：跨日样本（17:30Z → 次日 01:30）', () => {
    const [vm] = buildDraftRows([draft({ updated_at: '2026-07-04T17:30:00+00:00' })]);
    expect(vm.updatedAtText).toBe('2026-07-05 01:30:00');
  });
});

describe('templates view-model', () => {
  it('注册行视图：内置模板不可停用，停用行可启用（UINV-20）', () => {
    const rows = buildTemplateRows([
      row({}),
      row({ registry_ref: 'r2', source: 'registered', version_no: 2, registered_by: 'U1' }),
      row({ registry_ref: 'r3', source: 'registered', version_no: 3, status: 'disabled' }),
    ]);
    expect(rows[0].sourceText).toBe('内置');
    expect(rows[0].canDisable).toBe(false);
    expect(rows[1].canDisable).toBe(true);
    expect(rows[2].canEnable).toBe(true);
    expect(rows[1].hashShort).toHaveLength(12);
  });

  it('选择器选项：每个 template_key 取最新 active 版本，停用不入选', () => {
    const options = buildTemplateOptions([
      row({}),
      row({ registry_ref: 'r2', version_no: 3, status: 'disabled' }),
      row({ registry_ref: 'r3', version_no: 2, name: '企业定制版' }),
      row({ registry_ref: 'r4', template_key: 'srs-other', name: '另一模板' }),
    ]);
    expect(options).toHaveLength(2);
    const srs = options.find((o) => o.value === 'srs-iso29148-v1')!;
    expect(srs.label).toContain('企业定制版');
    expect(srs.label).toContain('v2'); // v3 已停用，取 v2
  });

  it('结构预览：槽位类型/必填/缺失策略/模板文本齐备', () => {
    const descriptor: TemplateDescriptorRead = {
      template_ref: 'srs-iso29148-v1',
      schema_version: '1.0',
      sections: [
        {
          key: 'intro.purpose', number: '1.1', title: '编写目的', level: 2, purpose: '说明目的',
          content_types: ['boilerplate'], required: true, repeatable: false,
          missing_policy: 'block', boilerplate: '本文档定义{project_name}…',
        },
        {
          key: 'requirements.functional', number: '3.1', title: '功能需求', level: 2, purpose: '',
          content_types: ['requirement_item:functional'], required: true, repeatable: true,
          missing_policy: 'block',
        },
        {
          key: 'overview', number: '2', title: '总体描述', level: 1, purpose: '',
          content_types: [], required: false, repeatable: false, missing_policy: 'skip',
        },
      ],
      error: null,
    };
    const preview = buildTemplatePreview(descriptor);
    expect(preview[0].slotText).toBe('模板文本');
    expect(preview[0].boilerplate).toContain('{project_name}');
    expect(preview[1].slotText).toBe('功能需求条目槽位');
    expect(preview[1].requiredText).toBe('必填');
    expect(preview[1].missingPolicyText).toBe('缺失阻塞');
    expect(preview[2].slotText).toBeNull();
    expect(preview[2].indent).toBe(0);
  });
});
