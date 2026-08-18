import {
  AimOutlined,
  ApiOutlined,
  AppstoreOutlined,
  ArrowLeftOutlined,
  BarChartOutlined,
  BellOutlined,
  BranchesOutlined,
  BulbOutlined,
  CheckCircleFilled,
  CheckCircleOutlined,
  CloseCircleFilled,
  CloseCircleOutlined,
  CloudServerOutlined,
  CloudUploadOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  DownOutlined,
  EditFilled,
  EditOutlined,
  HomeOutlined,
  LinkOutlined,
  LockOutlined,
  MoreOutlined,
  PlaySquareOutlined,
  PlusOutlined,
  ProjectOutlined,
  QuestionCircleOutlined,
  ReadOutlined,
  ReloadOutlined,
  RocketOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  SearchOutlined,
  SendOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { ReactNode } from 'react';
import type { NavigationIconKey } from '../view-models/app-shell';

export { BellOutlined, DownOutlined, QuestionCircleOutlined, ReloadOutlined, SearchOutlined };
export { ArrowLeftOutlined };

export const navigationIconMap = {
  overview: HomeOutlined,
  management: ProjectOutlined,
  traceability: BranchesOutlined,
  diagram: BarChartOutlined,
  release: SendOutlined,
  settings: SettingOutlined,
} satisfies Record<NavigationIconKey, typeof HomeOutlined>;

const actionIconMap = {
  back: ArrowLeftOutlined,
  create: PlusOutlined,
  management: ProjectOutlined,
  traceability: BranchesOutlined,
  diagram: BarChartOutlined,
  release: CloudUploadOutlined,
  edit: EditOutlined,
  close: CloseCircleOutlined,
  confirm: CheckCircleOutlined,
  save: SaveOutlined,
  link: LinkOutlined,
  more: MoreOutlined,
  launch: RocketOutlined,
  project: AppstoreOutlined,
} as const;

export function renderNavigationIcon(iconKey: NavigationIconKey): ReactNode {
  const Icon = navigationIconMap[iconKey];
  return <Icon aria-hidden="true" />;
}

export function renderActionIcon(iconKey?: string): ReactNode {
  if (!iconKey) {
    return null;
  }

  const Icon = actionIconMap[iconKey as keyof typeof actionIconMap];
  return Icon ? <Icon aria-hidden="true" /> : null;
}

// 知识项类型图标（图标=类型；颜色由两翼决定，见 .analysis-type-icon--{wing}）。
// 图形对齐 docs/proposals/knowledge-item-upgrade 两翼化原型的语义（雷电=功能、仪表=质量…）。
const elementTypeIconMap = {
  functional_requirement: ThunderboltOutlined,
  quality_attribute: DashboardOutlined,
  constraint: LockOutlined,
  data_requirement: DatabaseOutlined,
  interface_requirement: ApiOutlined,
  goal: AimOutlined,
  scenario: PlaySquareOutlined,
  term: ReadOutlined,
  business_rule: SafetyCertificateOutlined,
  assumption: BulbOutlined,
  role: UserOutlined,
  external_system: CloudServerOutlined,
} as const;

export function renderElementTypeIcon(typeCode: string): ReactNode {
  const Icon = elementTypeIconMap[typeCode as keyof typeof elementTypeIconMap] ?? ReadOutlined;
  return <Icon aria-hidden="true" />;
}

// 知识项状态前导标记（区3）：与类型正交，勾=已确认、叉=已撤销、修订笔=有修订稿。
const elementStatusIconMap = {
  confirmed: CheckCircleFilled,
  revoked: CloseCircleFilled,
  has_draft: EditFilled,
} as const;

export function renderElementStatusIcon(markKey: 'confirmed' | 'revoked' | 'has_draft'): ReactNode {
  const Icon = elementStatusIconMap[markKey];
  return <Icon aria-hidden="true" />;
}
