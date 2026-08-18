import {
  Alert,
  Breadcrumb,
  Button,
  Empty,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Tag,
  Tooltip,
  Tree,
  Typography,
  message,
} from 'antd';
import {
  DeleteOutlined,
  InfoCircleOutlined,
  PlusOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { templatesApi, type TemplateDraftRead, type TemplateValidationRead } from '../api/templates';
import {
  CONTENT_TYPE_GROUPS,
  DEFAULT_EXPORT_BINDING,
  assemblesRequirementItems,
  bindingFromDescriptor,
  buildDesignerPreview,
  buildTemplateJson,
  emptyNode,
  assemblyNoteFor,
  hasAuthoredText,
  numberByNodeId,
  parseDraftState,
  sectionsToTree,
  serializeDraftState,
  type DesignerExportBinding,
  type DesignerNode,
  type DesignerTemplateInfo,
} from '../view-models/template-designer';

const { Text, Paragraph } = Typography;
const { CheckableTag } = Tag;

const ROOT_ID = '__root__';

// ---- 树不可变操作（纯函数，前端投影层）----

function updateNode(tree: DesignerNode[], id: string, patch: Partial<DesignerNode>): DesignerNode[] {
  return tree.map((n) =>
    n.id === id
      ? { ...n, ...patch }
      : n.children.length > 0
        ? { ...n, children: updateNode(n.children, id, patch) }
        : n,
  );
}

function addChild(tree: DesignerNode[], parentId: string, child: DesignerNode): DesignerNode[] {
  return tree.map((n) =>
    n.id === parentId
      ? { ...n, children: [...n.children, child] }
      : n.children.length > 0
        ? { ...n, children: addChild(n.children, parentId, child) }
        : n,
  );
}

function insertSiblingAfter(tree: DesignerNode[], siblingId: string, sib: DesignerNode): DesignerNode[] {
  const idx = tree.findIndex((n) => n.id === siblingId);
  if (idx >= 0) {
    const next = tree.slice();
    next.splice(idx + 1, 0, sib);
    return next;
  }
  return tree.map((n) =>
    n.children.length > 0 ? { ...n, children: insertSiblingAfter(n.children, siblingId, sib) } : n,
  );
}

function removeNode(tree: DesignerNode[], id: string): { tree: DesignerNode[]; removed: DesignerNode | null } {
  let removed: DesignerNode | null = null;
  const filtered: DesignerNode[] = [];
  for (const n of tree) {
    if (n.id === id) {
      removed = n;
      continue;
    }
    if (n.children.length > 0) {
      const r = removeNode(n.children, id);
      if (r.removed) removed = r.removed;
      filtered.push({ ...n, children: r.tree });
    } else {
      filtered.push(n);
    }
  }
  return { tree: filtered, removed };
}

function findNode(tree: DesignerNode[], id: string): DesignerNode | null {
  for (const n of tree) {
    if (n.id === id) return n;
    const found = findNode(n.children, id);
    if (found) return found;
  }
  return null;
}

// 内容装配彩色圆点（呈现槽位类型；纯装饰）
function contentDotColor(node: DesignerNode): string | null {
  if (hasAuthoredText(node.contentTypes)) return '#722ed1';
  if (node.contentTypes.includes('boilerplate')) return '#8c8c8c';
  if (assemblesRequirementItems(node.contentTypes)) return '#1677ff';
  if (node.contentTypes.includes('chart')) return '#13c2c2';
  if (node.contentTypes.includes('material')) return '#fa8c16';
  return null;
}

export function TemplateDesignerEditor({
  operatorRef,
  initialCopyRef,
  initialEditRef,
  initialDraft,
  onBack,
  onRegistered,
}: {
  operatorRef: string;
  initialCopyRef?: string;
  /** 编辑已登记模板：保留 template_id 反填，登记后成为该模板的新版本（注册行本身不可变）。 */
  initialEditRef?: string;
  /** 继续编辑草稿：反填暂存的定制器状态，暂存写回同一草稿行。 */
  initialDraft?: TemplateDraftRead;
  onBack: () => void;
  onRegistered: () => void;
}) {
  const [info, setInfo] = useState<DesignerTemplateInfo>({ templateId: '', title: '', description: '' });
  const [binding, setBinding] = useState<DesignerExportBinding>({ ...DEFAULT_EXPORT_BINDING });
  const [tree, setTree] = useState<DesignerNode[]>([emptyNode({ title: '引言', contentTypes: ['boilerplate'], boilerplate: '' })]);
  const [selectedId, setSelectedId] = useState<string>(ROOT_ID);
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  const [validation, setValidation] = useState<TemplateValidationRead | null>(null);
  const [busy, setBusy] = useState(false);
  const [draftRef, setDraftRef] = useState<string | null>(initialDraft?.draft_ref ?? null);
  const [dirty, setDirty] = useState(false);
  const [exitPromptOpen, setExitPromptOpen] = useState(false);
  const [messageApi, messageHolder] = message.useMessage();

  // 结构/字段变化：清空上次送检态（改结构=需重新送检，沿用现状）；标记未暂存。
  const mutate = useCallback((next: DesignerNode[]) => {
    setTree(next);
    setValidation(null);
    setDirty(true);
  }, []);

  const patchSelected = useCallback(
    (patch: Partial<DesignerNode>) => {
      if (selectedId === ROOT_ID) return;
      mutate(updateNode(tree, selectedId, patch));
    },
    [selectedId, tree, mutate],
  );

  // 登记行反填（复制起草 / 编辑；含 examples，按 number/level 重建树）。
  // 编辑保留 template_id 与名称——登记后按同 key 递增为新版本，注册行本身仍不可变。
  const fillFromRegistry = useCallback(async (registryRef: string, editMode: boolean) => {
    setBusy(true);
    try {
      const detail = await templatesApi.getDetail(registryRef);
      const d = detail.descriptor;
      if (d.error) {
        messageApi.error(`该登记行不可解析：${d.error}`);
        return;
      }
      setInfo(editMode
        ? { templateId: detail.template_key, title: d.title ?? detail.name, description: d.description ?? '' }
        : { templateId: `${detail.template_key}-copy`, title: `${d.title ?? detail.name}（副本）`, description: d.description ?? '' });
      setBinding(bindingFromDescriptor(d));
      const rebuilt = sectionsToTree(d.sections);
      setTree(rebuilt.length > 0 ? rebuilt : [emptyNode({ title: '引言' })]);
      setExpandedKeys(collectIds(rebuilt));
      setValidation(null);
      setSelectedId(ROOT_ID);
      setDirty(false);
      messageApi.success(editMode
        ? `已载入 ${detail.template_key} v${detail.version_no} 内容：登记后将成为该模板的新版本`
        : '已按所选模板反填（含章节样例），可修改后送检登记');
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, [messageApi]);

  // 草稿反填（继续编辑）：信封不兼容/损坏时提示并回退空白起草。
  const restoreDraft = useCallback((draft: TemplateDraftRead) => {
    const state = parseDraftState(draft.payload);
    if (!state) {
      messageApi.error('草稿内容不兼容当前定制器版本，已回退为空白起草（原草稿未删除）');
      return;
    }
    setInfo(state.info);
    setBinding(state.binding);
    setTree(state.tree);
    setExpandedKeys(collectIds(state.tree));
    setValidation(null);
    setSelectedId(ROOT_ID);
    setDirty(false);
    messageApi.success(`已恢复草稿「${draft.name || '未命名'}」，可继续编辑`);
  }, [messageApi]);

  useEffect(() => {
    if (initialDraft) restoreDraft(initialDraft);
    else if (initialEditRef) void fillFromRegistry(initialEditRef, true);
    else if (initialCopyRef) void fillFromRegistry(initialCopyRef, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialCopyRef, initialEditRef, initialDraft]);

  const numberMap = useMemo(() => numberByNodeId(tree), [tree]);
  const preview = useMemo(() => buildDesignerPreview(tree), [tree]);
  const selectedNode = selectedId === ROOT_ID ? null : findNode(tree, selectedId);

  // ---- 树操作 ----
  const onAddChild = (parentId: string) => {
    const child = emptyNode({ title: '新子章节' });
    mutate(addChild(tree, parentId, child));
    setExpandedKeys((k) => (k.includes(parentId) ? k : [...k, parentId]));
    setSelectedId(child.id);
  };
  const onAddSibling = (siblingId: string) => {
    const sib = emptyNode({ title: '新章节' });
    mutate(insertSiblingAfter(tree, siblingId, sib));
    setSelectedId(sib.id);
  };
  const onAddTopLevel = () => {
    const top = emptyNode({ title: '新章节' });
    mutate([...tree, top]);
    setSelectedId(top.id);
  };
  const onDelete = (id: string) => {
    const { tree: next } = removeNode(tree, id);
    if (next.length === 0) {
      messageApi.warning('至少保留一个章节');
      return;
    }
    mutate(next);
    if (selectedId === id) setSelectedId(ROOT_ID);
  };

  // 拖拽改层级/排序（antd Tree onDrop → 模型重排）。
  const onDrop = (dropInfo: {
    dragNode: { key: React.Key };
    node: { key: React.Key };
    dropToGap: boolean;
    dropPosition: number;
  }) => {
    const dragId = String(dropInfo.dragNode.key);
    const dropId = String(dropInfo.node.key);
    if (dragId === dropId) return;
    const { tree: without, removed } = removeNode(tree, dragId);
    if (!removed) return;
    if (!dropInfo.dropToGap) {
      // 落到节点内部 → 作为其首/末子（追加末子，避免层级歧义）
      mutate(addChild(without, dropId, removed));
      setExpandedKeys((k) => (k.includes(dropId) ? k : [...k, dropId]));
    } else {
      // 落到间隙 → 作为目标同级兄弟（插到其后）
      mutate(insertSiblingAfter(without, dropId, removed));
    }
    setSelectedId(dragId);
  };

  const treeData: DataNode[] = useMemo(() => {
    const build = (nodes: DesignerNode[]): DataNode[] =>
      nodes.map((n) => ({
        key: n.id,
        title: renderTreeTitle(n, numberMap.get(n.id) ?? '', {
          onAddChild,
          onAddSibling,
          onDelete,
        }),
        children: n.children.length > 0 ? build(n.children) : undefined,
      }));
    return build(tree);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tree, numberMap]);

  // ---- 暂存草稿 / 退出守卫 ----
  const draftOrigin = initialDraft?.origin ?? (initialEditRef ? 'edit' : initialCopyRef ? 'copy' : 'blank');
  const draftSourceRef = initialDraft?.source_registry_ref ?? initialEditRef ?? initialCopyRef ?? null;

  const saveDraft = useCallback(async (): Promise<boolean> => {
    setBusy(true);
    try {
      const command = {
        name: info.title.trim() || info.templateId.trim(),
        payload: serializeDraftState({ info, binding, tree }),
        origin: draftOrigin,
        source_registry_ref: draftSourceRef,
        operator_ref: operatorRef,
      };
      const row = draftRef
        ? await templatesApi.updateDraft(draftRef, command)
        : await templatesApi.createDraft(command);
      setDraftRef(row.draft_ref);
      setDirty(false);
      messageApi.success('草稿已暂存：退出后可在「文档模板」列表继续编辑');
      return true;
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : String(error));
      return false;
    } finally {
      setBusy(false);
    }
  }, [info, binding, tree, draftOrigin, draftSourceRef, draftRef, operatorRef, messageApi]);

  // 返回守卫：有未暂存修改时先问（暂存并退出 / 直接退出 / 继续编辑）。
  const requestBack = useCallback(() => {
    if (dirty) setExitPromptOpen(true);
    else onBack();
  }, [dirty, onBack]);

  // ---- 送检 / 登记 ----
  const buildJson = useCallback(() => buildTemplateJson(info, binding, tree), [info, binding, tree]);

  const validate = useCallback(async (): Promise<TemplateValidationRead | null> => {
    setBusy(true);
    try {
      const result = await templatesApi.validate(buildJson());
      setValidation(result);
      return result;
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : String(error));
      return null;
    } finally {
      setBusy(false);
    }
  }, [buildJson, messageApi]);

  const register = useCallback(async () => {
    const result = await validate();
    if (!result?.ok) return;
    setBusy(true);
    try {
      const row = await templatesApi.register({
        content: buildJson(),
        name: info.title.trim() || null,
        operator_ref: operatorRef,
        idempotency_key: `tpl-editor-${Date.now()}`,
      });
      if (draftRef) {
        // 登记成功即清理暂存（删除幂等；清理失败不阻断登记结果）
        try {
          await templatesApi.deleteDraft(draftRef, operatorRef);
        } catch {
          messageApi.warning('模板已登记，但草稿清理失败，可在列表手动删除');
        }
      }
      messageApi.success(`模板已登记：${row.template_key} v${row.version_no}（内容快照不可变）`);
      onRegistered();
      onBack();
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, [validate, buildJson, info.title, operatorRef, draftRef, onRegistered, onBack, messageApi]);

  return (
    <section aria-label="大纲树模板编辑器" className="panel settings-domain-panel" data-testid="template-designer-editor">
      {messageHolder}
      <div className="panel__header" style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
        <Breadcrumb
          items={[{ title: '设置' }, { title: '文档模板' }, {
            title: initialDraft
              ? '继续编辑草稿'
              : initialEditRef
                ? '编辑模板（登记为新版本）'
                : initialCopyRef
                  ? '复制起草'
                  : '定制新模板',
          }]}
        />
        {dirty ? <Tag color="orange" data-testid="designer-dirty-tag">未暂存</Tag> : null}
        <span style={{ flex: 1 }} />
        <Space size={8}>
          <Button onClick={requestBack} data-testid="designer-back">返回列表</Button>
          <Button loading={busy} onClick={() => void saveDraft()} data-testid="designer-save-draft">
            暂存草稿
          </Button>
          <Button loading={busy} onClick={() => void validate()} data-testid="designer-validate">
            送检校验（AEP-100）
          </Button>
          <Button type="primary" loading={busy} disabled={!validation?.ok} onClick={() => void register()} data-testid="designer-register">
            登记模板
          </Button>
        </Space>
      </div>

      <div className="panel__body">
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          表单 → 模板 JSON 投影器：树的嵌套/层级/编号/章节 key 均为前端投影，保存写回既有扁平 sections；送检通过才可登记，登记即不可变快照（改内容 = 新版本）。
        </Paragraph>

        <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'stretch', flexWrap: 'wrap' }}>
          {/* 栏1 · 大纲树 */}
          <div style={{ flex: '1 1 20rem', minWidth: '18rem', border: '1px solid var(--color-border)', borderRadius: 8, padding: '0.75rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: '0.5rem' }}>
              <Text strong style={{ flex: 1 }}>章节大纲</Text>
            </div>
            <button
              type="button"
              data-testid="designer-select-root"
              onClick={() => setSelectedId(ROOT_ID)}
              className={selectedId === ROOT_ID ? 'settings-menu-item settings-menu-item--selected' : 'settings-menu-item'}
              style={{ width: '100%', marginBottom: '0.5rem', textAlign: 'left' }}
            >
              <span className="settings-menu-item__icon"><InfoCircleOutlined aria-hidden="true" /></span>
              <span className="settings-menu-item__label">模板信息与版式（根）</span>
            </button>
            <Tree
              blockNode
              draggable
              showLine
              treeData={treeData}
              selectedKeys={selectedId === ROOT_ID ? [] : [selectedId]}
              expandedKeys={expandedKeys}
              onExpand={(keys) => setExpandedKeys(keys.map(String))}
              onSelect={(keys) => { if (keys[0]) setSelectedId(String(keys[0])); }}
              onDrop={onDrop}
              data-testid="designer-tree"
            />
            <Button block icon={<PlusOutlined />} style={{ marginTop: '0.5rem' }} onClick={onAddTopLevel} data-testid="designer-add-top">
              添加顶层章节
            </Button>
          </div>

          {/* 栏2 · 详情（章节 / 根节点） */}
          <div style={{ flex: '2 1 26rem', minWidth: '22rem', border: '1px solid var(--color-border)', borderRadius: 8, padding: '0.75rem' }}>
            {selectedId === ROOT_ID ? (
              <RootForm info={info} binding={binding} onInfo={(patch) => { setInfo((v) => ({ ...v, ...patch })); setValidation(null); setDirty(true); }} onBinding={(patch) => { setBinding((v) => ({ ...v, ...patch })); setValidation(null); setDirty(true); }} />
            ) : selectedNode ? (
              <SectionForm node={selectedNode} number={numberMap.get(selectedId) ?? ''} onPatch={patchSelected} />
            ) : (
              <Empty description="选择左侧章节或根节点编辑" />
            )}
          </div>

          {/* 栏3 · 实时预览（标题/说明/槽位随编辑即时投影） */}
          <div style={{ flex: '1 1 18rem', minWidth: '16rem', border: '1px solid var(--color-border)', borderRadius: 8, padding: '0.75rem' }} data-testid="designer-preview">
            <Text strong>实时结构预览</Text>
            <Paragraph style={{ fontSize: 13, marginTop: 6, marginBottom: 0, fontWeight: 600 }}>
              {info.title.trim() || '（未命名模板）'}
            </Paragraph>
            {info.description.trim() ? (
              <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 2, marginBottom: 0 }}>{info.description.trim()}</Paragraph>
            ) : null}
            <div style={{ marginTop: 8 }}>
              {preview.map((row) => (
                <div
                  key={row.id}
                  className={row.id === selectedId ? 'designer-preview-row designer-preview-row--selected' : 'designer-preview-row'}
                  style={{ marginLeft: `${(row.level - 1) * 0.9}rem` }}
                >
                  <div className="designer-preview-row__title">{row.number} {row.title}</div>
                  {row.purpose ? <div className="designer-preview-row__purpose">{row.purpose}</div> : null}
                  {row.slotText ? <div className="designer-preview-row__slot">{row.slotText}</div> : null}
                </div>
              ))}
            </div>
          </div>
        </div>

        {validation ? (
          <div style={{ marginTop: '0.75rem' }} data-testid="designer-validation">
            {validation.ok ? (
              <Alert
                type="success"
                showIcon
                title="送检通过：模板可被系统消费"
                description={
                  <Space wrap size={4}>
                    {(validation.descriptor?.sections ?? []).map((s) => (
                      <Tag key={s.key}>{s.number} {s.title}</Tag>
                    ))}
                  </Space>
                }
              />
            ) : (
              <Alert type="error" showIcon title="送检未通过（整体拒绝，不落库）" description={validation.error} />
            )}
          </div>
        ) : null}
      </div>

      <Modal
        title="有未暂存的修改"
        open={exitPromptOpen}
        onCancel={() => setExitPromptOpen(false)}
        data-testid="designer-exit-prompt"
        footer={[
          <Button key="stay" onClick={() => setExitPromptOpen(false)} data-testid="exit-stay">继续编辑</Button>,
          <Button key="discard" danger onClick={() => { setExitPromptOpen(false); onBack(); }} data-testid="exit-discard">
            不暂存，直接退出
          </Button>,
          <Button
            key="save"
            type="primary"
            loading={busy}
            data-testid="exit-save"
            onClick={() => {
              void saveDraft().then((ok) => {
                if (ok) {
                  setExitPromptOpen(false);
                  onBack();
                }
              });
            }}
          >
            暂存并退出
          </Button>,
        ]}
      >
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          暂存为草稿后，可随时从「文档模板」列表继续编辑；不暂存则本次修改丢失。
        </Paragraph>
      </Modal>
    </section>
  );
}

function collectIds(tree: DesignerNode[]): string[] {
  const ids: string[] = [];
  const walk = (nodes: DesignerNode[]) => {
    for (const n of nodes) {
      if (n.children.length > 0) {
        ids.push(n.id);
        walk(n.children);
      }
    }
  };
  walk(tree);
  return ids;
}

function renderTreeTitle(
  node: DesignerNode,
  number: string,
  handlers: { onAddChild: (id: string) => void; onAddSibling: (id: string) => void; onDelete: (id: string) => void },
) {
  const dot = contentDotColor(node);
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, width: '100%', minWidth: 0 }} className="designer-tree-title">
      {dot ? <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: '50%', background: dot, flex: '0 0 auto' }} /> : null}
      <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {number} {node.title.trim() || '（未命名章节）'}
      </span>
      {node.repeatable ? <Tag color="blue" style={{ marginInlineEnd: 0 }}>逐条目</Tag> : null}
      {/* 行操作悬停显现：避免窄屏时按钮撑爆行宽溢出到相邻栏 */}
      <span className="designer-tree-actions">
        <Tooltip title="添加子章节">
          <Button size="small" type="text" icon={<PlusOutlined />} aria-label={`为 ${node.title} 添加子章节`} data-testid="tree-add-child" onClick={(e) => { e.stopPropagation(); handlers.onAddChild(node.id); }} />
        </Tooltip>
        <Tooltip title="添加同级章节">
          <Button size="small" type="text" aria-label={`为 ${node.title} 添加同级章节`} data-testid="tree-add-sibling" onClick={(e) => { e.stopPropagation(); handlers.onAddSibling(node.id); }}>＋级</Button>
        </Tooltip>
        <Popconfirm
          title="删除本章节及其子树？"
          okText="删除"
          cancelText="取消"
          onConfirm={() => handlers.onDelete(node.id)}
        >
          <Tooltip title="删除本章节及其子树">
            <Button size="small" type="text" danger icon={<DeleteOutlined />} aria-label={`删除 ${node.title}`} data-testid="tree-delete" onClick={(e) => e.stopPropagation()} />
          </Tooltip>
        </Popconfirm>
      </span>
    </span>
  );
}

// ---- 栏2：章节详情表单（03 §3）----

function SectionForm({
  node,
  number,
  onPatch,
}: {
  node: DesignerNode;
  number: string;
  onPatch: (patch: Partial<DesignerNode>) => void;
}) {
  const canRepeat = assemblesRequirementItems(node.contentTypes);
  // 「模板默认文本」与「人工撰稿」可同选，三种组合各自出什么在此讲清（文案单一来源在 VM）。
  const assemblyNote = assemblyNoteFor(node.contentTypes);

  const toggleType = (value: string, checked: boolean) => {
    const set = new Set(node.contentTypes);
    if (checked) set.add(value);
    else set.delete(value);
    const nextTypes = Array.from(set);
    const patch: Partial<DesignerNode> = { contentTypes: nextTypes };
    // 取消需求条目装配后「逐条目成节」失效 → 归零，避免残留无意义 true。
    if (node.repeatable && !assemblesRequirementItems(nextTypes)) patch.repeatable = false;
    onPatch(patch);
  };

  return (
    <div className="designer-form" data-testid="designer-section-form">
      <div className="designer-form__head">
        <Text strong>章节详情</Text>
        {number ? <Tag>{number}</Tag> : null}
      </div>

      <div className="designer-field">
        <span className="designer-field__label">章节标题</span>
        <Input value={node.title} placeholder="如 编写目的" onChange={(e) => onPatch({ title: e.target.value })} data-testid="section-title" />
      </div>

      <div className="designer-field">
        <span className="designer-field__label">章节说明</span>
        <span className="designer-field__hint">本章承载什么内容；也是 AI 起草初稿的主要依据，可留空。右侧结构预览实时同步。</span>
        <Input.TextArea
          rows={3}
          value={node.purpose}
          placeholder="如 说明文档的目的、范围与阅读约定"
          onChange={(e) => onPatch({ purpose: e.target.value })}
          data-testid="section-purpose"
        />
      </div>

      <hr className="designer-form__divider" />

      <div className="designer-field">
        <span className="designer-field__label">内容装配</span>
        <div className="designer-assembly">
          {CONTENT_TYPE_GROUPS.map((group) => (
            <div key={group.title} className="designer-assembly__row">
              <span className="designer-assembly__group">{group.title}</span>
              <span className="designer-assembly__chips">
                {group.options.map((opt) => (
                  <CheckableTag
                    key={opt.value}
                    checked={node.contentTypes.includes(opt.value)}
                    onChange={(checked) => toggleType(opt.value, checked)}
                  >
                    {opt.label}
                  </CheckableTag>
                ))}
              </span>
            </div>
          ))}
        </div>
        {assemblyNote ? (
          <Alert
            type="info"
            showIcon
            icon={<RobotOutlined />}
            data-testid="section-authored-note"
            data-note-kind={assemblyNote.kind}
            title={assemblyNote.title}
            description={assemblyNote.description}
          />
        ) : null}
      </div>

      <hr className="designer-form__divider" />

      <div className="designer-field">
        <span className="designer-field__label">章节规则</span>
        <div className="designer-rules">
          <span className="designer-rule">
            <Switch checked={node.required} onChange={(v) => onPatch({ required: v })} data-testid="section-required" />
            <Text>必填</Text>
          </span>
          <Tooltip title={canRepeat ? '每个需求条目各成一小节（3.1.1/3.1.2），关则汇总为一节列表' : '仅对装配需求条目的章节生效'}>
            <span className="designer-rule" data-testid="section-repeatable-wrap">
              <Switch checked={node.repeatable} disabled={!canRepeat} onChange={(v) => onPatch({ repeatable: v })} data-testid="section-repeatable" />
              <Text type={canRepeat ? undefined : 'secondary'}>逐条目成节</Text>
            </span>
          </Tooltip>
          <span className="designer-rule">
            <Text type="secondary">缺失处理</Text>
            <Switch
              checkedChildren="阻断"
              unCheckedChildren="跳过"
              checked={node.missingPolicy === 'block'}
              onChange={(v) => onPatch({ missingPolicy: v ? 'block' : 'skip' })}
              data-testid="section-missing-policy"
            />
          </span>
        </div>
        {!canRepeat ? (
          <span className="designer-field__hint">「逐条目成节」仅对装配需求条目的章节生效，当前已灰置。</span>
        ) : null}
      </div>

      {node.contentTypes.includes('boilerplate') ? (
        <div className="designer-field">
          <span className="designer-field__label">模板默认文本</span>
          <span className="designer-field__hint">生成时作为该章节预填稿；支持 {'{project_name}'}/{'{coverage_scope}'} 占位符。</span>
          <Input.TextArea
            rows={3}
            value={node.boilerplate}
            placeholder="如 本文档描述 {project_name} 的需求规格。"
            onChange={(e) => onPatch({ boilerplate: e.target.value })}
            data-testid="section-boilerplate"
          />
        </div>
      ) : null}

      <hr className="designer-form__divider" />

      <div className="designer-field">
        <span className="designer-field__label">章节样例（供 AI 学习参考）</span>
        <span className="designer-field__hint">1–3 个优质范例，AI 起草/补全据此模仿风格与结构；样例本身不进入正式文档。</span>
        {node.examples.map((ex, i) => (
          <div key={i} style={{ display: 'flex', gap: 6 }} data-testid="section-example-row">
            <Input.TextArea
              rows={2}
              value={ex}
              placeholder={`范例 ${i + 1}`}
              onChange={(e) => {
                const next = node.examples.slice();
                next[i] = e.target.value;
                onPatch({ examples: next });
              }}
            />
            <Button
              type="text"
              danger
              icon={<DeleteOutlined />}
              aria-label={`移除范例 ${i + 1}`}
              data-testid="section-example-remove"
              onClick={() => onPatch({ examples: node.examples.filter((_, j) => j !== i) })}
            />
          </div>
        ))}
        <Button icon={<PlusOutlined />} style={{ alignSelf: 'flex-start' }} onClick={() => onPatch({ examples: [...node.examples, ''] })} data-testid="section-example-add">
          添加样例
        </Button>
      </div>

      <hr className="designer-form__divider" />

      <div className="designer-field">
        <span className="designer-field__hint">章节标识 key（留空自动派生）</span>
        <Input
          size="small"
          style={{ maxWidth: '20rem' }}
          value={node.keyOverride}
          placeholder="如 intro.purpose（留空按编号派生）"
          onChange={(e) => onPatch({ keyOverride: e.target.value })}
          data-testid="section-key"
        />
      </div>
    </div>
  );
}

// ---- 栏2：模板信息与版式（根节点态，03 §4）----

function RootForm({
  info,
  binding,
  onInfo,
  onBinding,
}: {
  info: DesignerTemplateInfo;
  binding: DesignerExportBinding;
  onInfo: (patch: Partial<DesignerTemplateInfo>) => void;
  onBinding: (patch: Partial<DesignerExportBinding>) => void;
}) {
  return (
    <div className="designer-form" data-testid="designer-root-form">
      <div className="designer-form__head">
        <Text strong>模板信息</Text>
        <Tag>schema 1.0</Tag>
        <Tag>doc_type srs</Tag>
      </div>
      <div className="designer-field">
        <span className="designer-field__label">模板标识（template_id）</span>
        <Input value={info.templateId} placeholder="如 srs-internal-v1（小写-连字符）" onChange={(e) => onInfo({ templateId: e.target.value })} data-testid="root-template-id" />
      </div>
      <div className="designer-field">
        <span className="designer-field__label">模板名称</span>
        <Input value={info.title} placeholder="如 内部 SRS 模板" onChange={(e) => onInfo({ title: e.target.value })} data-testid="root-title" />
      </div>
      <div className="designer-field">
        <span className="designer-field__label">说明</span>
        <Input value={info.description} placeholder="模板用途说明（可选）" onChange={(e) => onInfo({ description: e.target.value })} data-testid="root-description" />
      </div>

      <hr className="designer-form__divider" />

      <div className="designer-field">
        <span className="designer-field__label">导出版式绑定（docx）</span>
        <div className="designer-rules">
          <span className="designer-rule">
            <Text type="secondary">正文中文字体</Text>
            <Input variant="borderless" style={{ width: '6rem', padding: 0 }} value={binding.bodyFontEastAsia} onChange={(e) => onBinding({ bodyFontEastAsia: e.target.value })} data-testid="root-body-font" />
          </span>
          <span className="designer-rule">
            <Text type="secondary">正文字号(pt)</Text>
            <InputNumber size="small" min={8} max={24} value={binding.bodySizePt} onChange={(v) => onBinding({ bodySizePt: v ?? 12 })} data-testid="root-body-size" />
          </span>
          <span className="designer-rule">
            <Text type="secondary">首行缩进(字符)</Text>
            <InputNumber size="small" min={0} max={4} value={binding.firstLineIndentChars} onChange={(v) => onBinding({ firstLineIndentChars: v ?? 2 })} data-testid="root-indent" />
          </span>
          <span className="designer-rule">
            <Text type="secondary">标题字号(pt)</Text>
            <Input variant="borderless" style={{ width: '6rem', padding: 0 }} placeholder="16, 14, 13" value={binding.headingSizesPt} onChange={(e) => onBinding({ headingSizesPt: e.target.value })} data-testid="root-heading-sizes" />
          </span>
        </div>
        <span className="designer-field__hint">
          导出版式的必填四键（中文字体/正文字号/首行缩进/标题字号）已绑定；其余扩展键（西文字体/行距/段后距）待后续登记为可选键后再落投影。
        </span>
      </div>
    </div>
  );
}
