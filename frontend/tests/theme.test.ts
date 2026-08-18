// 主题机制纯测（04A §9.1 / UINV-26）：初始化优先级 手动 > 跟随系统 > 默认；antd 映射。
import { afterEach, describe, expect, it } from 'vitest';
import { theme as antdTheme } from 'antd';
import {
  antdThemeFor,
  applyFontScaleToDocument,
  FONT_SCALE_OPTIONS,
  isFontScale,
  isThemeKey,
  resolveInitialFontScale,
  resolveInitialTheme,
  THEME_SCHEMES,
} from '../src/ui/theme';

function storageOf(entries: Record<string, string>): Pick<Storage, 'getItem'> {
  return { getItem: (key: string) => entries[key] ?? null };
}

afterEach(() => {
  window.localStorage.clear();
});

describe('THEME_SCHEMES', () => {
  it('五套方案键名与顺序已拍板：a-qingkong(默认)/b-xuanye(暗色)/c-dianqing/d-qingbi/e-baolan', () => {
    expect(THEME_SCHEMES.map((scheme) => scheme.key)).toEqual([
      'a-qingkong',
      'b-xuanye',
      'c-dianqing',
      'd-qingbi',
      'e-baolan',
    ]);
    expect(THEME_SCHEMES.find((scheme) => scheme.key === 'b-xuanye')?.mode).toBe('dark');
  });
});

describe('resolveInitialTheme', () => {
  it('无本地偏好 → 默认晴空蓝，不跟随系统', () => {
    expect(resolveInitialTheme(storageOf({}))).toEqual({
      themeKey: 'a-qingkong',
      followSystem: false,
    });
  });

  it('手动选择持久化后按 rx-theme 恢复', () => {
    expect(resolveInitialTheme(storageOf({ 'rx-theme': 'd-qingbi' }))).toEqual({
      themeKey: 'd-qingbi',
      followSystem: false,
    });
  });

  it('非法 rx-theme 值回落默认（不因脏数据白屏）', () => {
    expect(resolveInitialTheme(storageOf({ 'rx-theme': 'hacker-theme' })).themeKey).toBe('a-qingkong');
  });

  it('跟随系统开启时按 prefers-color-scheme 在 a/b 间取值（jsdom mock=浅色 → 晴空蓝）', () => {
    const preference = resolveInitialTheme(
      storageOf({ 'rx-theme-follow': '1', 'rx-theme': 'c-dianqing' }),
    );
    // 跟随系统时忽略手动值（开关状态优先决定来源）
    expect(preference).toEqual({ themeKey: 'a-qingkong', followSystem: true });
  });
});

describe('antdThemeFor（各方案文档 §5 映射）', () => {
  it('B 玄夜：darkAlgorithm + colorPrimary #1668dc', () => {
    const config = antdThemeFor('b-xuanye');
    expect(config.algorithm).toBe(antdTheme.darkAlgorithm);
    expect(config.token).toMatchObject({ colorPrimary: '#1668dc' });
  });

  it('C 靛青 #4f46e5；D 青碧 #0e7f74 + colorSuccess #4b9c2f', () => {
    expect(antdThemeFor('c-dianqing').token).toMatchObject({ colorPrimary: '#4f46e5' });
    expect(antdThemeFor('d-qingbi').token).toMatchObject({
      colorPrimary: '#0e7f74',
      colorSuccess: '#4b9c2f',
    });
  });

  it('A/E 不覆盖 antd 主题（空 token；恒为对象避免 ConfigProvider 重挂载）', () => {
    expect(antdThemeFor('a-qingkong')).toEqual({ token: {} });
    expect(antdThemeFor('e-baolan')).toEqual({ token: {} });
  });
});

describe('isThemeKey', () => {
  it('只认五个已拍板键名', () => {
    expect(isThemeKey('b-xuanye')).toBe(true);
    expect(isThemeKey('dark')).toBe(false);
    expect(isThemeKey(null)).toBe(false);
  });
});

describe('字体大小档位（rx-font-scale 本地偏好）', () => {
  it('四档白名单：90/100/110/125', () => {
    expect(FONT_SCALE_OPTIONS.map((option) => option.value)).toEqual([90, 100, 110, 125]);
    expect(isFontScale(110)).toBe(true);
    expect(isFontScale(80)).toBe(false);
  });

  it('缺省/非法值一律回落标准 100', () => {
    expect(resolveInitialFontScale(storageOf({}))).toBe(100);
    expect(resolveInitialFontScale(storageOf({ 'rx-font-scale': '999' }))).toBe(100);
    expect(resolveInitialFontScale(storageOf({ 'rx-font-scale': 'abc' }))).toBe(100);
    expect(resolveInitialFontScale(storageOf({ 'rx-font-scale': '125' }))).toBe(125);
  });

  it('applyFontScaleToDocument：非标准档写 --user-font-scale，标准档移除', () => {
    applyFontScaleToDocument(125);
    expect(document.documentElement.style.getPropertyValue('--user-font-scale')).toBe('1.25');
    applyFontScaleToDocument(100);
    expect(document.documentElement.style.getPropertyValue('--user-font-scale')).toBe('');
  });
});
