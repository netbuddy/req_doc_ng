#!/usr/bin/env bash
# 坏味道警戒线·后端（docs/v2/drafts/坏味道治理方案-讨论稿.md 第一层，2026-08-07 用户定案）。
# 三道门，全部本地即取即用（uvx）、零常驻服务：
#   1) ruff       —— 语法卫生＋圈复杂度红线 15（规则集与存量基线在 pyproject.toml [tool.ruff.lint]）
#   2) lint-imports — 分层契约（契约与存量基线在 pyproject.toml [tool.importlinter]）
#   3) vulture    —— 死代码「只减不增」：与 tools/vulture_baseline.txt 比对，新增即红；
#                    若是消除了存量（比基线少），按提示重新生成基线文件一并提交。
set -e
cd "$(dirname "$0")/.."

echo "== 1/3 ruff（语法卫生＋复杂度红线）"
uvx ruff check app

echo "== 2/3 import-linter（分层契约）"
uvx --from import-linter lint-imports

echo "== 3/3 vulture（死代码只减不增）"
current=$(mktemp)
uvx vulture app --min-confidence 90 2>/dev/null | sed 's/:[0-9]*:/:/' | sort > "$current"
if ! diff tools/vulture_baseline.txt "$current" > /dev/null; then
  echo "死代码清单与基线不一致："
  diff tools/vulture_baseline.txt "$current" || true
  echo "→ 若为新增死代码：删掉它；若为消除存量：更新基线并提交："
  echo "  uvx vulture app --min-confidence 90 | sed 's/:[0-9]*:/:/' | sort > tools/vulture_baseline.txt"
  rm -f "$current"
  exit 1
fi
rm -f "$current"
echo "全部通过。"
