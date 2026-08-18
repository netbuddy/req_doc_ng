import type { RequirementManagementWorkbenchVM } from '../view-models/requirement-management';

/** 创建流程视图的五区骨架文案（稳定视图配置，非业务数据）；维护列表/需求卡片走资产读侧 AEP。 */
export const requirementManagementWorkbenchFixture: RequirementManagementWorkbenchVM = {
  viewMode: 'maintenance',
  creationFlow: {
    title: '材料接入',
    description: '面向本次新增需求输入，完成材料来源登记、正文提交、接入判断和结果承接。',
    returnAction: {
      key: 'return-maintenance',
      label: '放弃本次输入',
      iconKey: 'back',
    },
    flow: {
      stageText: '材料接入 → 知识抽取 → 条目形成 → 条目评审',
      steps: [
        { key: 's1', label: '材料接入', value: '进行中', tone: 'processing' },
        { key: 's2', label: '知识抽取', value: '待处理', tone: 'neutral' },
        { key: 's3', label: '条目形成', value: '待处理', tone: 'neutral' },
        { key: 's4', label: '条目评审', value: '待处理', tone: 'neutral' },
      ],
      inputListVM: {
        title: '区1 本次材料来源',
        body: '只呈现本次新增需求输入的来源登记，不展示历史材料列表。',
      },
      toolbarVM: {
        title: '区2 导航 + 工具栏',
        body: '提供提交接入判断、保存草稿、清空文本和放弃本次输入。',
      },
      sourceCanvasVM: {
        title: '区3 材料正文（来源画布）',
        body: '承载本次输入正文，用户修订后仍通过区2重新提交。',
      },
      detailEvidenceVM: {
        title: '区4 接入结论与模型证据',
        body: '展示模型判断依据、材料引用、退回或排除原因。',
      },
      outputListVM: {
        title: '区5 本次接入结果',
        body: '展示本次接入状态、下一步入口和过程记录，不提供其他操作区。',
      },
    },
  },
};
