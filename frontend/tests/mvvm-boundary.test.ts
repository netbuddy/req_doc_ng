import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { appShellFixture } from '../src/fixtures/app-shell';
import { navigationIconMap } from '../src/ui/icons';

const srcRoot = join(process.cwd(), 'src');

function collectFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    return statSync(path).isDirectory() ? collectFiles(path) : [path];
  });
}

describe('MVVM boundaries', () => {
  it('不创建 src/models 复制领域对象', () => {
    expect(existsSync(join(srcRoot, 'models'))).toBe(false);
  });

  it('workbench 视图不直接调用 fetch', () => {
    const files = collectFiles(join(srcRoot, 'workbenches'));
    const allWorkbenchCode = files.map((file) => readFileSync(file, 'utf8')).join('\n');

    expect(allWorkbenchCode).not.toContain('fetch(');
  });

  it('fixture 文件只构造 ViewModel 快照', () => {
    const files = collectFiles(join(srcRoot, 'fixtures'));
    const allFixtureCode = files.map((file) => readFileSync(file, 'utf8')).join('\n');

    expect(allFixtureCode).toContain('Workbench');
    expect(allFixtureCode).not.toContain('LDM007');
    expect(allFixtureCode).not.toContain('RequirementItemRead');
  });

  it('导航 ViewModel 的 iconKey 可以映射为图标', () => {
    const iconKeys = appShellFixture.navigationItems.map((item) => item.iconKey);

    expect(iconKeys).toHaveLength(6);
    expect(iconKeys.every((key) => key in navigationIconMap)).toBe(true);
  });
});
