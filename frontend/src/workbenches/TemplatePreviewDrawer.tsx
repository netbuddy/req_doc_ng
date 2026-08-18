import { Alert, Button, Drawer, Empty, Space, Tag, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { templatesApi } from '../api/templates';
import type { TemplateRegistryDetailRead } from '../api/templates';
import { buildTemplatePreview } from '../view-models/templates';

const { Text, Paragraph } = Typography;

interface TemplatePreviewDrawerProps {
  registryRef: string | null; // null = 关闭
  onClose: () => void;
}

/** 模板结构预览抽屉（章节树/槽位/必填规则 + 样例 docx 下载）；发布页与设置页共用。 */
export function TemplatePreviewDrawer({ registryRef, onClose }: TemplatePreviewDrawerProps) {
  const [detail, setDetail] = useState<TemplateRegistryDetailRead | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    if (!registryRef) return;
    let disposed = false;
    templatesApi
      .getDetail(registryRef)
      .then((d) => {
        if (!disposed) setDetail(d);
      })
      .catch((e) => {
        if (!disposed) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      disposed = true;
    };
  }, [registryRef]);

  const sections = detail && !detail.descriptor.error ? buildTemplatePreview(detail.descriptor) : [];

  return (
    <Drawer
      title={detail ? `模板预览：${detail.name}（${detail.template_key} v${detail.version_no}）` : '模板预览'}
      open={registryRef !== null}
      onClose={onClose}
      width={560}
      extra={
        registryRef ? (
          <Button type="primary" href={templatesApi.previewDocxUrl(registryRef)} target="_blank">
            下载样例 docx（版式预览）
          </Button>
        ) : null
      }
    >
      {error ? <Alert type="error" showIcon title="模板详情加载失败" description={error} /> : null}
      {detail?.descriptor.error ? (
        <Alert type="error" showIcon title="模板内容不可解析" description={detail.descriptor.error} />
      ) : null}
      {!detail && !error ? <Empty description="正在加载…" image={Empty.PRESENTED_IMAGE_SIMPLE} /> : null}
      {detail && !detail.descriptor.error ? (
        <>
          <Paragraph type="secondary">
            {detail.descriptor.description || '——'}
          </Paragraph>
          <Paragraph>
            <Text type="secondary">schema {detail.schema_version} · 内容哈希 {detail.content_hash.slice(0, 12)}…
              · 登记快照不可变，改内容 = 登记新版本</Text>
          </Paragraph>
          {sections.map((section) => (
            <div
              key={section.key}
              style={{ padding: '6px 0', marginLeft: section.indent * 16, borderBottom: '1px solid var(--color-border-soft)' }}
            >
              <Space size={6} wrap>
                <Text strong={section.indent === 0}>{section.headingText}</Text>
                {section.slotText ? <Tag>{section.slotText}</Tag> : null}
                {section.requiredText ? (
                  <Tag color={section.requiredText === '必填' ? 'volcano' : 'default'}>{section.requiredText}</Tag>
                ) : null}
                {section.missingPolicyText ? (
                  <Tag color={section.missingPolicyText === '缺失阻塞' ? 'red' : 'default'}>
                    {section.missingPolicyText}
                  </Tag>
                ) : null}
              </Space>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>{section.purpose}</Text>
              </div>
              {section.boilerplate ? (
                <div style={{ background: 'var(--color-panel)', padding: 6, borderRadius: 4, marginTop: 4 }}>
                  <Text style={{ fontSize: 12 }}>{section.boilerplate}</Text>
                </div>
              ) : null}
            </div>
          ))}
        </>
      ) : null}
    </Drawer>
  );
}
