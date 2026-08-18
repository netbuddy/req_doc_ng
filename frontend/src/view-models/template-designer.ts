// 大纲树编辑器 VM（03 篇）：树 ↔ 扁平 sections 投影的**唯一来源**。
// 树/层级(level)/编号(number)/章节 key 均为前端投影：保存写回既有扁平 sections，
// 后端 parse_template 契约不变（不变式 4）。纯函数，无 React / 无 fetch（MVVM 边界）。
import type { TemplateDescriptorRead, TemplateSectionRead } from '../api/publication';

export const MAX_LEVEL = 4; // 层级封顶（与 parse_template/publication 渲染一致）

export type MissingPolicy = 'block' | 'skip';

/** 编辑期树节点：children 承载嵌套；keyOverride 空则由编号路径派生。 */
export interface DesignerNode {
  id: string; // 客户端稳定 id（React key / 拖拽锚点；不进投影）
  keyOverride: string; // 手填 key（''=自动派生）
  title: string;
  purpose: string;
  contentTypes: string[];
  required: boolean;
  repeatable: boolean;
  missingPolicy: MissingPolicy;
  boilerplate: string;
  examples: string[];
  children: DesignerNode[];
}

/** 投影产物：既有扁平 section（送检/登记 JSON 的 sections 元素）。 */
export interface FlatSection {
  key: string;
  number: string;
  title: string;
  level: number;
  purpose: string;
  content_types: string[];
  required: boolean;
  repeatable: boolean;
  missing_policy: MissingPolicy;
  boilerplate?: string;
  examples?: string[];
}

// ---- 内容装配（content_types）分组 chip：呈现层标签映射（内容类型集权威仍在后端 _KNOWN_CONTENT_TYPES）----

export interface ContentTypeGroupVM {
  title: string;
  options: { value: string; label: string }[];
}

export const CONTENT_TYPE_GROUPS: ContentTypeGroupVM[] = [
  {
    title: '结构与撰稿',
    options: [
      { value: 'boilerplate', label: '模板默认文本（固定预填）' },
      { value: 'authored_text', label: '人工撰稿（AI 起草初稿）' },
    ],
  },
  {
    title: '需求条目',
    options: [
      { value: 'requirement_item:functional', label: '功能需求' },
      { value: 'requirement_item:quality', label: '质量属性' },
      { value: 'requirement_item:constraint', label: '约束' },
      { value: 'requirement_item:data', label: '数据需求' },
      { value: 'requirement_item:interface', label: '接口需求' },
    ],
  },
  {
    title: '素材',
    options: [
      { value: 'chart', label: '需求图表' },
      { value: 'material', label: '支撑材料' },
    ],
  },
];

const REQUIREMENT_ITEM_PREFIX = 'requirement_item:';

/** 是否装配了需求条目类型（决定「逐条目成节」是否可用，03 §3.3）。 */
export function assemblesRequirementItems(contentTypes: string[]): boolean {
  return contentTypes.some((c) => c.startsWith(REQUIREMENT_ITEM_PREFIX));
}

/** 内容装配是否含「人工撰稿」（决定是否显示 AI 起草说明条，03 §3.2 / 反馈③）。 */
export function hasAuthoredText(contentTypes: string[]): boolean {
  return contentTypes.includes('authored_text');
}

/**
 * 「模板默认文本」与「人工撰稿」两个勾选的三态说明（T20260720，用户拍板：两者可同选）。
 *
 * 两者不互斥，同选是有意义的组合，但组合起来到底出什么，光看勾选框看不出来——这里把三种
 * 组合各自的结果讲清。只改呈现：勾选的数据含义与发布行为都没变，说明文案与后端既有语义一致
 * （撰稿覆盖默认文本：publication.py 渲染时撰稿存在即用撰稿；撰稿编辑器以默认文本作预填底稿）。
 */
export interface AssemblyNoteVM {
  /** 三态标识：仅默认文本 / 仅撰稿 / 同选。 */
  kind: 'boilerplate_only' | 'authored_only' | 'both';
  title: string;
  description: string;
}

export function assemblyNoteFor(contentTypes: string[]): AssemblyNoteVM | null {
  const boilerplate = contentTypes.includes('boilerplate');
  const authored = hasAuthoredText(contentTypes);
  if (boilerplate && authored) {
    return {
      kind: 'both',
      title: '本章有默认文本，也可以改写',
      description:
        '下面填的默认文本会作为撰稿的底稿，进入撰稿环节时预先填好；有人改写或让 AI 起草后，'
        + '文档里出的是改写后的内容，默认文本不再出现。没人动过就按默认文本原样出。',
    };
  }
  if (boilerplate) {
    return {
      kind: 'boilerplate_only',
      title: '本章出固定内容',
      description: '下面填的默认文本会原样进入文档，每次发布都一样，不经人工改写、也不经 AI 加工。',
    };
  }
  if (authored) {
    return {
      kind: 'authored_only',
      title: '本章由人来写',
      description:
        '本章没有底稿，正文在撰稿环节由人写；也可以让 AI 依据【章节说明】＋【关联需求资产】＋'
        + '【章节样例】起草一份初稿，供人工完善（初稿不是最终稿，不会自动确认）。',
    };
  }
  return null;
}

let idSeq = 0;
export function newNodeId(): string {
  idSeq += 1;
  return `n${Date.now().toString(36)}-${idSeq}`;
}

/** key 自动派生：由编号路径生成稳定唯一 key（编号前序唯一 ⇒ key 唯一）。 */
export function autoKey(numberPath: string): string {
  return `sec-${numberPath.replaceAll('.', '-')}`;
}

export function emptyNode(overrides: Partial<DesignerNode> = {}): DesignerNode {
  return {
    id: newNodeId(),
    keyOverride: '',
    title: '',
    purpose: '',
    contentTypes: [],
    required: false,
    repeatable: false,
    missingPolicy: 'skip',
    boilerplate: '',
    examples: [],
    children: [],
    ...overrides,
  };
}

/**
 * 树 → 扁平（**唯一**转 JSON 处）：前序遍历；level=树深(封顶 MAX_LEVEL)；
 * number 按同级位置派生（"1"/"1.1"/"1.1.1"）；key=keyOverride 或按编号派生；
 * boilerplate 仅装配 boilerplate 时带；examples 去空后仅非空时带。
 */
export function treeToSections(tree: DesignerNode[]): FlatSection[] {
  const out: FlatSection[] = [];
  const walk = (nodes: DesignerNode[], parentNumber: string, depth: number) => {
    nodes.forEach((node, i) => {
      const number = parentNumber ? `${parentNumber}.${i + 1}` : `${i + 1}`;
      const section: FlatSection = {
        key: node.keyOverride.trim() || autoKey(number),
        number,
        title: node.title.trim(),
        level: Math.min(depth, MAX_LEVEL),
        purpose: node.purpose.trim(),
        content_types: [...node.contentTypes],
        required: node.required,
        repeatable: node.repeatable,
        missing_policy: node.missingPolicy,
      };
      if (node.contentTypes.includes('boilerplate')) {
        section.boilerplate = node.boilerplate;
      }
      const examples = node.examples.map((e) => e.trim()).filter(Boolean);
      if (examples.length > 0) {
        section.examples = examples;
      }
      out.push(section);
      if (node.children.length > 0) {
        walk(node.children, number, depth + 1);
      }
    });
  };
  walk(tree, '', 1);
  return out;
}

/** id → number 映射（预览/树节点编号展示；与 treeToSections 同序派生）。 */
export function numberByNodeId(tree: DesignerNode[]): Map<string, string> {
  const map = new Map<string, string>();
  const walk = (nodes: DesignerNode[], parentNumber: string) => {
    nodes.forEach((node, i) => {
      const number = parentNumber ? `${parentNumber}.${i + 1}` : `${i + 1}`;
      map.set(node.id, number);
      if (node.children.length > 0) walk(node.children, number);
    });
  };
  walk(tree, '');
  return map;
}

/**
 * 扁平 → 树（复制起草反填）：按 level 前序重建父子（number 作展示，level 定深度）。
 * 保留原 key 为 keyOverride、examples 回显；栈式重建保证与 treeToSections 往返一致。
 */
export function sectionsToTree(sections: TemplateSectionRead[]): DesignerNode[] {
  const roots: DesignerNode[] = [];
  const stack: { level: number; node: DesignerNode }[] = [];
  for (const s of sections) {
    const node = emptyNode({
      keyOverride: s.key,
      title: s.title,
      purpose: s.purpose,
      contentTypes: [...s.content_types],
      required: s.required,
      repeatable: s.repeatable,
      missingPolicy: (s.missing_policy as MissingPolicy) ?? 'skip',
      boilerplate: s.boilerplate ?? '',
      examples: [...(s.examples ?? [])],
    });
    while (stack.length > 0 && stack[stack.length - 1].level >= s.level) {
      stack.pop();
    }
    if (stack.length === 0) {
      roots.push(node);
    } else {
      stack[stack.length - 1].node.children.push(node);
    }
    stack.push({ level: s.level, node });
  }
  return roots;
}

// ---- 模板信息与版式（根节点态，03 §4）----

export interface DesignerTemplateInfo {
  templateId: string;
  title: string;
  description: string;
}

export interface DesignerExportBinding {
  bodyFontEastAsia: string;
  bodySizePt: number;
  firstLineIndentChars: number;
  headingSizesPt: string; // 逗号分隔，如 "16, 14, 13"
}

export const DEFAULT_EXPORT_BINDING: DesignerExportBinding = {
  bodyFontEastAsia: '仿宋',
  bodySizePt: 12,
  firstLineIndentChars: 2,
  headingSizesPt: '16, 14, 13',
};

/** 组装送检/登记 JSON（表单 → 模板 JSON 投影器；schema_version 恒 1.0）。 */
export function buildTemplateJson(
  info: DesignerTemplateInfo,
  binding: DesignerExportBinding,
  tree: DesignerNode[],
): string {
  const headingSizes = binding.headingSizesPt
    .split(/[,，\s]+/)
    .filter(Boolean)
    .map((v) => Number(v));
  return JSON.stringify(
    {
      template_id: info.templateId.trim(),
      schema_version: '1.0',
      doc_type: 'srs',
      title: info.title.trim(),
      description: info.description.trim(),
      export_binding: {
        body_font_east_asia: binding.bodyFontEastAsia,
        body_size_pt: binding.bodySizePt,
        first_line_indent_chars: binding.firstLineIndentChars,
        heading_sizes_pt: headingSizes,
      },
      sections: treeToSections(tree),
    },
    null,
    2,
  );
}

/** 复制起草：descriptor → 表单信息 + 版式（配合 sectionsToTree 反填树）。 */
export function bindingFromDescriptor(descriptor: TemplateDescriptorRead): DesignerExportBinding {
  const eb = (descriptor.export_binding ?? {}) as Record<string, unknown>;
  return {
    bodyFontEastAsia: String(eb.body_font_east_asia ?? DEFAULT_EXPORT_BINDING.bodyFontEastAsia),
    bodySizePt: Number(eb.body_size_pt ?? DEFAULT_EXPORT_BINDING.bodySizePt),
    firstLineIndentChars: Number(eb.first_line_indent_chars ?? DEFAULT_EXPORT_BINDING.firstLineIndentChars),
    headingSizesPt: Array.isArray(eb.heading_sizes_pt)
      ? (eb.heading_sizes_pt as number[]).join(', ')
      : DEFAULT_EXPORT_BINDING.headingSizesPt,
  };
}

// ---- 草稿信封（暂存/继续编辑）：定制器状态 ↔ 后端不透明 payload ----

export const DESIGNER_STATE_VERSION = 1;

export interface DesignerDraftState {
  info: DesignerTemplateInfo;
  binding: DesignerExportBinding;
  tree: DesignerNode[];
}

/** 状态 → 草稿 payload（后端只存取不解析；信封带版本号供前向兼容）。 */
export function serializeDraftState(state: DesignerDraftState): string {
  return JSON.stringify({
    designer_state_version: DESIGNER_STATE_VERSION,
    info: state.info,
    binding: state.binding,
    tree: state.tree,
  });
}

/** 树节点净化：字段缺省补齐（旧信封/手改 payload 容错），id 重新生成保证唯一。 */
function sanitizeNodes(raw: unknown): DesignerNode[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((n) => {
    const r = (n ?? {}) as Record<string, unknown>;
    return emptyNode({
      keyOverride: typeof r.keyOverride === 'string' ? r.keyOverride : '',
      title: typeof r.title === 'string' ? r.title : '',
      purpose: typeof r.purpose === 'string' ? r.purpose : '',
      contentTypes: Array.isArray(r.contentTypes) ? r.contentTypes.filter((c) => typeof c === 'string') : [],
      required: r.required === true,
      repeatable: r.repeatable === true,
      missingPolicy: r.missingPolicy === 'block' ? 'block' : 'skip',
      boilerplate: typeof r.boilerplate === 'string' ? r.boilerplate : '',
      examples: Array.isArray(r.examples) ? r.examples.filter((e) => typeof e === 'string') : [],
      children: sanitizeNodes(r.children),
    });
  });
}

/** 草稿 payload → 状态（不兼容/损坏返回 null，由调用方提示后回退空白起草）。 */
export function parseDraftState(payload: string): DesignerDraftState | null {
  try {
    const raw = JSON.parse(payload) as Record<string, unknown>;
    if (raw.designer_state_version !== DESIGNER_STATE_VERSION) return null;
    const info = (raw.info ?? {}) as Record<string, unknown>;
    const binding = (raw.binding ?? {}) as Record<string, unknown>;
    const tree = sanitizeNodes(raw.tree);
    if (tree.length === 0) return null;
    return {
      info: {
        templateId: typeof info.templateId === 'string' ? info.templateId : '',
        title: typeof info.title === 'string' ? info.title : '',
        description: typeof info.description === 'string' ? info.description : '',
      },
      binding: {
        bodyFontEastAsia: typeof binding.bodyFontEastAsia === 'string'
          ? binding.bodyFontEastAsia : DEFAULT_EXPORT_BINDING.bodyFontEastAsia,
        bodySizePt: typeof binding.bodySizePt === 'number'
          ? binding.bodySizePt : DEFAULT_EXPORT_BINDING.bodySizePt,
        firstLineIndentChars: typeof binding.firstLineIndentChars === 'number'
          ? binding.firstLineIndentChars : DEFAULT_EXPORT_BINDING.firstLineIndentChars,
        headingSizesPt: typeof binding.headingSizesPt === 'string'
          ? binding.headingSizesPt : DEFAULT_EXPORT_BINDING.headingSizesPt,
      },
      tree,
    };
  } catch {
    return null;
  }
}

// ---- 实时预览（03 §5）：按投影派生编号大纲 + 槽位标识 ----

export interface DesignerPreviewRowVM {
  id: string;
  number: string;
  level: number;
  title: string;
  purpose: string; // 章节说明（编辑中实时投影；空则不展示）
  slotText: string | null; // 槽位/撰稿标识；纯结构章节为 null
}

const ITEM_TYPE_LABEL: Record<string, string> = {
  functional: '功能需求条目',
  quality: '质量属性条目',
  constraint: '约束条目',
  data: '数据需求条目',
  interface: '接口需求条目',
};

function previewSlotText(node: DesignerNode): string | null {
  if (hasAuthoredText(node.contentTypes)) return '〔人工撰稿 · AI 起草初稿〕';
  if (node.contentTypes.includes('boilerplate')) return '〔模板默认文本 · 固定预填〕';
  const items = node.contentTypes
    .filter((c) => c.startsWith(REQUIREMENT_ITEM_PREFIX))
    .map((c) => ITEM_TYPE_LABEL[c.slice(REQUIREMENT_ITEM_PREFIX.length)] ?? c);
  if (items.length > 0) {
    const each = node.repeatable ? ' · 逐条目成节' : ' · 确认态自动装配';
    return `〔${items.join('/')}${each}〕`;
  }
  if (node.contentTypes.includes('chart')) return '〔需求图表 · 确认态装配〕';
  if (node.contentTypes.includes('material')) return '〔支撑材料 · 装配〕';
  return null;
}

export function buildDesignerPreview(tree: DesignerNode[]): DesignerPreviewRowVM[] {
  const rows: DesignerPreviewRowVM[] = [];
  const walk = (nodes: DesignerNode[], parentNumber: string, depth: number) => {
    nodes.forEach((node, i) => {
      const number = parentNumber ? `${parentNumber}.${i + 1}` : `${i + 1}`;
      rows.push({
        id: node.id,
        number,
        level: Math.min(depth, MAX_LEVEL),
        title: node.title.trim() || '（未命名章节）',
        purpose: node.purpose.trim(),
        slotText: previewSlotText(node),
      });
      if (node.children.length > 0) walk(node.children, number, depth + 1);
    });
  };
  walk(tree, '', 1);
  return rows;
}
