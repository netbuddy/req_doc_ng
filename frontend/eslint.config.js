// 坏味道警戒线·前端（docs/v2/drafts/坏味道治理方案-讨论稿.md 第一层，2026-08-07 用户定案）。
// 规则三类：认知复杂度（sonarjs，红线 15）、文件体量（500 行）、函数体量（60 行，仅 .ts——
// .tsx 组件函数天然含大段 JSX，函数行数规则会整片误伤，改由认知复杂度与文件体量约束）。
// react-hooks 只启用两条经典规则；其 v6 新增的实验性规则组（set-state-in-effect、refs 等，
// 首扫 91 处）不入门禁，已记入热点档案观察。
// 存量违规按「只减不增」列入文末 legacy-baseline 段；新文件触线即红。跑法：npm run smell。
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import sonarjs from 'eslint-plugin-sonarjs';
import reactHooks from 'eslint-plugin-react-hooks';

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**', 'src/api/generated/**'] },
  {
    files: ['src/**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    plugins: { sonarjs, 'react-hooks': reactHooks },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'sonarjs/cognitive-complexity': ['error', 15],
      'max-lines': ['error', { max: 500, skipBlankLines: true, skipComments: true }],
      // 中文界面文本与注释里允许全角空格
      'no-irregular-whitespace': ['error', {
        skipStrings: true, skipTemplates: true, skipComments: true, skipJSXText: true,
      }],
      // 未用变量交给 tsc（本仓 tsc --noEmit 已是常跑检查），避免双报
      '@typescript-eslint/no-unused-vars': 'off',
    },
  },
  {
    files: ['src/**/*.ts'],
    rules: {
      'max-lines-per-function': ['error', { max: 60, skipBlankLines: true, skipComments: true }],
    },
  },
  // ---- 存量基线（2026-08-07 实测；按文件豁免、只许减不许增，清干净即从此清单删除）----
  {
    name: 'legacy-baseline',
    files: [
      // 超体量/超复杂度的存量文件（热点档案已登记；处置时机=对应模块的重构批次）
      'src/workbenches/*.tsx',
      'src/App.tsx',
      'src/api/client.ts',
      'src/ui/MarkdownPreview.tsx',
      'src/hooks/useAgentRunWatcher.ts',
      'src/chat-widget/action-dispatch.ts',
      'src/view-models/publication.ts',
      'src/view-models/requirement-analysis.ts',
      'src/view-models/requirement-item-review.ts',
      'src/view-models/requirement-item-formation.ts',
      'src/view-models/requirement-management.ts',
      'src/view-models/requirement-quality.ts',
      'src/view-models/requirement-assets.ts',
      'src/view-models/runtime-status.ts',
      'src/view-models/item-review-thread.ts',
      'src/view-models/settings.ts',
      'src/view-models/overview.ts',
    ],
    rules: {
      'max-lines': 'off',
      'max-lines-per-function': 'off',
      'sonarjs/cognitive-complexity': 'off',
    },
  },
);
