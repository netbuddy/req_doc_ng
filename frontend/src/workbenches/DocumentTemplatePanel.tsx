import { Alert, Button, Collapse, Empty, Input, Modal, Popconfirm, Space, Spin, Table, Tag, Typography, message } from 'antd';
import { EditOutlined, InfoCircleFilled, SafetyOutlined } from '@ant-design/icons';
import { useCallback, useEffect, useState } from 'react';
import { templatesApi, type TemplateDraftRead, type TemplateRegistryRead } from '../api/templates';
import {
  buildDraftRows,
  buildTemplateChoices,
  buildTemplateRows,
  type TemplateChoiceVM,
  type TemplateDraftRowVM,
  type TemplateRowVM,
} from '../view-models/templates';
import { TemplateDesignerEditor } from './TemplateDesignerEditor';
import { TemplatePreviewDrawer } from './TemplatePreviewDrawer';

const { Text, Paragraph } = Typography;

const TONE_COLOR: Record<string, string> = {
  processing: 'blue',
  neutral: 'default',
  success: 'green',
  warning: 'orange',
};

/**
 * 文档模板配置域（设置工作台）：模板注册表的定制 / 登记 / 预览 / 复制起草 / 停用启用。
 * 复用既有模板端点（GET/POST /templates、validate、status、preview-docx）；
 * 本域**不写 config_registry**（管的是模板注册表，不是 key-value 配置，UINV-19/20 不变）。
 * 定制器（大纲树编辑器）与「登记新模板」入口从发布工作台迁入此处，发布侧只保留选用/预览。
 */
export function DocumentTemplatePanel({
  operatorRef,
  onTemplatesChanged,
}: {
  operatorRef: string;
  onTemplatesChanged?: () => void;
}) {
  const [rows, setRows] = useState<TemplateRegistryRead[] | null>(null);
  const [drafts, setDrafts] = useState<TemplateDraftRead[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  // 整页三栏编辑器与列表页互斥（03 §1）：mode='editor' 时占满面板区。
  const [mode, setMode] = useState<'list' | 'editor'>('list');
  // 编辑器入口三选一：copyRef=复制起草；editRef=编辑（登记为同 key 新版本）；draft=继续编辑草稿。
  const [editorEntry, setEditorEntry] = useState<{ copyRef?: string; editRef?: string; draft?: TemplateDraftRead }>({});
  const [registerOpen, setRegisterOpen] = useState(false);
  const [registerContent, setRegisterContent] = useState('');
  const [registerName, setRegisterName] = useState('');
  const [registerError, setRegisterError] = useState<string | null>(null);
  const [registering, setRegistering] = useState(false);
  const [statusBusy, setStatusBusy] = useState(false);
  const [previewRef, setPreviewRef] = useState<string | null>(null);
  const [messageApi, messageHolder] = message.useMessage();

  const load = useCallback(async () => {
    try {
      const [templateRows, draftRows] = await Promise.all([templatesApi.list(), templatesApi.listDrafts()]);
      setRows(templateRows);
      setDrafts(draftRows);
      setLoadError(null);
    } catch (error) {
      setRows(null);
      setLoadError(error instanceof Error ? error.message : String(error));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const afterChange = useCallback(async () => {
    await load();
    onTemplatesChanged?.();
  }, [load, onTemplatesChanged]);

  const openEditor = (entry: { copyRef?: string; editRef?: string; draft?: TemplateDraftRead } = {}) => {
    setEditorEntry(entry);
    setMode('editor');
  };

  const deleteDraft = async (draftRef: string) => {
    try {
      await templatesApi.deleteDraft(draftRef, operatorRef);
      setDrafts(await templatesApi.listDrafts());
      messageApi.success('草稿已删除');
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : String(error));
    }
  };

  const register = async () => {
    setRegistering(true);
    setRegisterError(null);
    try {
      const row = await templatesApi.register({
        content: registerContent,
        name: registerName || null,
        operator_ref: operatorRef,
        idempotency_key: `tpl-settings-${Date.now()}`,
      });
      setRegisterOpen(false);
      setRegisterContent('');
      setRegisterName('');
      await afterChange();
      messageApi.success(`模板已登记：${row.template_key} v${row.version_no}（内容快照不可变）`);
    } catch (error) {
      // 校验失败整体拒绝：问题清单就地展示，不落库
      setRegisterError(error instanceof Error ? error.message : String(error));
    } finally {
      setRegistering(false);
    }
  };

  const setStatus = async (row: TemplateRowVM, status: 'active' | 'disabled') => {
    setStatusBusy(true);
    try {
      await templatesApi.setStatus(row.registryRef, status, operatorRef);
      await afterChange();
      messageApi.success(status === 'disabled' ? '模板已停用（历史基线引用不受影响）' : '模板已启用');
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : String(error));
    } finally {
      setStatusBusy(false);
    }
  };

  const choices: TemplateChoiceVM[] = rows ? buildTemplateChoices(rows) : [];
  const versionRows: TemplateRowVM[] = rows ? buildTemplateRows(rows) : [];
  const draftRows: TemplateDraftRowVM[] = buildDraftRows(drafts);
  const activeCount = choices.length;

  const versionColumns = [
    {
      title: '模板',
      render: (_: unknown, r: TemplateRowVM) => (
        <Space orientation="vertical" size={0}>
          <Text>{r.name}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {r.templateKey} · {r.versionText} · schema {r.schemaVersion}
          </Text>
        </Space>
      ),
    },
    { title: '内容哈希', dataIndex: 'hashShort', render: (v: string) => <Text code style={{ fontSize: 12 }}>{v}…</Text> },
    { title: '来源', render: (_: unknown, r: TemplateRowVM) => <Tag color={TONE_COLOR[r.sourceTone]}>{r.sourceText}</Tag> },
    { title: '状态', render: (_: unknown, r: TemplateRowVM) => <Tag color={TONE_COLOR[r.statusTone]}>{r.statusText}</Tag> },
    {
      title: '登记',
      render: (_: unknown, r: TemplateRowVM) => (
        <Text type="secondary" style={{ fontSize: 12 }}>{r.registeredBy} · {r.registeredAtText}</Text>
      ),
    },
    {
      title: '操作',
      render: (_: unknown, r: TemplateRowVM) => (
        <Space size={4}>
          <Button size="small" onClick={() => setPreviewRef(r.registryRef)}>预览</Button>
          <Button size="small" onClick={() => openEditor({ editRef: r.registryRef })}>编辑</Button>
          {r.canDisable ? (
            <Button size="small" danger loading={statusBusy} onClick={() => void setStatus(r, 'disabled')}>停用</Button>
          ) : null}
          {r.canEnable ? (
            <Button size="small" loading={statusBusy} onClick={() => void setStatus(r, 'active')}>启用</Button>
          ) : null}
        </Space>
      ),
    },
  ];

  if (mode === 'editor') {
    return (
      <>
        {messageHolder}
        <TemplateDesignerEditor
          operatorRef={operatorRef}
          initialCopyRef={editorEntry.copyRef}
          initialEditRef={editorEntry.editRef}
          initialDraft={editorEntry.draft}
          onBack={() => { setMode('list'); void afterChange(); }}
          onRegistered={() => void afterChange()}
        />
      </>
    );
  }

  return (
    <section aria-label="文档模板配置" className="panel settings-domain-panel" data-testid="settings-document-template-panel">
      {messageHolder}
      <div className="panel__header" style={{ display: 'flex', alignItems: 'center' }}>
        <h2 className="panel__title" style={{ flex: 1 }}>文档模板</h2>
        <Space size={8}>
          <Button type="primary" data-testid="dt-open-designer" onClick={() => openEditor()}>
            定制新模板
          </Button>
          <Button data-testid="dt-open-register" onClick={() => setRegisterOpen(true)}>
            登记新模板（粘贴 JSON）
          </Button>
        </Space>
      </div>
      <div className="panel__body">
        <div className="settings-hint-bar">
          <InfoCircleFilled aria-hidden="true" />
          <span>
            模板登记即<b>内容快照</b>（sha256 固定）：「编辑」不改写已登记行，登记后成为同模板的<b>新版本</b>；停用不影响历史发布基线的引用。
            定制过程可<b>暂存草稿</b>随时退出，从下方草稿列表继续编辑。发布工作台只<b>选用与预览</b>已登记模板。
          </span>
        </div>

        {loadError ? (
          <Alert showIcon title="模板列表加载失败" description={loadError} type="warning" style={{ marginTop: 12 }} />
        ) : null}

        {draftRows.length > 0 ? (
          <div data-testid="dt-draft-list" style={{ marginTop: 12, border: '1px dashed var(--color-border)', borderRadius: 8, padding: 12 }}>
            <Text strong>未完成草稿（{draftRows.length}）</Text>
            <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {draftRows.map((draft) => (
                <div key={draft.draftRef} data-testid="dt-draft-row" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <Text>{draft.name}</Text>
                  <Tag>{draft.originText}</Tag>
                  <Text type="secondary" style={{ fontSize: 12, flex: 1 }}>
                    {draft.createdBy} · 暂存于 {draft.updatedAtText}
                  </Text>
                  <Space size={4}>
                    <Button
                      size="small"
                      type="primary"
                      ghost
                      data-testid="dt-draft-resume"
                      onClick={() => {
                        const row = drafts.find((d) => d.draft_ref === draft.draftRef);
                        if (row) openEditor({ draft: row });
                      }}
                    >
                      继续编辑
                    </Button>
                    <Popconfirm title="删除该草稿？（不可恢复）" okText="删除" cancelText="取消" onConfirm={() => void deleteDraft(draft.draftRef)}>
                      <Button size="small" danger data-testid="dt-draft-delete">删除</Button>
                    </Popconfirm>
                  </Space>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {rows === null && !loadError ? (
          <div className="settings-domain-loading"><Spin /></div>
        ) : choices.length === 0 && !loadError ? (
          <Empty description="暂无可用模板：点击右上角「定制新模板」或「登记新模板」创建" style={{ marginTop: 16 }} />
        ) : (
          <div
            data-testid="dt-card-grid"
            style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(20rem, 1fr))', gap: 12, marginTop: 12 }}
          >
            {choices.map((choice) => (
              <div
                key={choice.templateId}
                data-testid="dt-template-card"
                style={{ border: '1px solid var(--color-border)', borderRadius: 8, padding: 12 }}
              >
                <Space orientation="vertical" size={4} style={{ width: '100%' }}>
                  <Space size={6} wrap>
                    <Text strong>{choice.name}</Text>
                    <Tag color={TONE_COLOR[choice.sourceTone]}>{choice.sourceText}</Tag>
                  </Space>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {choice.templateId} · {choice.versionText} · schema {choice.schemaVersion} · {choice.hashShort}…
                  </Text>
                  <Space size={4} wrap>
                    <Button size="small" onClick={() => setPreviewRef(choice.registryRef)}>预览结构与版式</Button>
                    <Button size="small" icon={<EditOutlined />} data-testid="dt-edit-template" onClick={() => openEditor({ editRef: choice.registryRef })}>
                      编辑
                    </Button>
                    <Button size="small" onClick={() => openEditor({ copyRef: choice.registryRef })}>复制起草</Button>
                  </Space>
                </Space>
              </div>
            ))}
          </div>
        )}

        <Collapse
          ghost
          style={{ marginTop: 12 }}
          items={[
            {
              key: 'versions',
              label: `全部登记版本（${versionRows.length}）· 停用/启用`,
              children: (
                <Table<TemplateRowVM>
                  rowKey="registryRef"
                  size="small"
                  columns={versionColumns}
                  dataSource={versionRows}
                  pagination={false}
                />
              ),
            },
          ]}
        />

        <div className="settings-note">
          <SafetyOutlined aria-hidden="true" />
          <span>
            <b>边界：</b>文档模板域管理的是<b>模板注册表</b>，不写 <code>config_registry</code> 键、不形成确认结论/追溯/基线；
            登记只表示模板可被系统消费（活跃 {activeCount} 个）。
          </span>
        </div>
      </div>

      <Modal
        title="登记模板（内容按内置 schema 送检）"
        open={registerOpen}
        okText="送检并登记"
        cancelText="取消"
        onOk={() => void register()}
        onCancel={() => setRegisterOpen(false)}
        confirmLoading={registering}
        width={720}
      >
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          粘贴模板文件 JSON。校验失败将整体拒绝并列出全部问题，不落库；校验通过只表示模板可被系统消费，不代表企业标准内容已由系统审定。
        </Paragraph>
        <Input
          placeholder="显示名（可选，缺省取模板 title）"
          value={registerName}
          onChange={(e) => setRegisterName(e.target.value)}
          style={{ marginBottom: 8 }}
        />
        <Input.TextArea
          rows={14}
          placeholder='{"template_id": "...", "schema_version": "1.0", ...}'
          value={registerContent}
          onChange={(e) => setRegisterContent(e.target.value)}
          style={{ fontFamily: 'monospace' }}
          data-testid="dt-register-input"
        />
        {registerError ? (
          <Alert type="error" showIcon title="模板送检未通过" description={registerError} style={{ marginTop: 8 }} />
        ) : null}
      </Modal>

      <TemplatePreviewDrawer registryRef={previewRef} onClose={() => setPreviewRef(null)} />
    </section>
  );
}
