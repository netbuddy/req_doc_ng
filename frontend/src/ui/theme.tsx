import { ConfigProvider, theme as antdTheme } from 'antd';
import type { ThemeConfig } from 'antd';
import type { Locale } from 'antd/es/locale';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

/**
 * 界面风格（主题）切换机制：data-theme + CSS 变量 + localStorage（04A §9.1 / UINV-26）。
 * 外观偏好是浏览器本地偏好：不落后端、不写配置存储、不留审计、不产生治理事实。
 * 主题只改配色令牌；界面结构、图标与布局跨主题一致。
 */

export type ThemeKey = 'a-qingkong' | 'b-xuanye' | 'c-dianqing' | 'd-qingbi' | 'e-baolan';

export const DEFAULT_THEME: ThemeKey = 'a-qingkong';
export const DARK_THEME: ThemeKey = 'b-xuanye';
/** 与 index.html 防闪烁内联脚本共用的存储键。 */
export const THEME_STORAGE_KEY = 'rx-theme';
export const THEME_FOLLOW_STORAGE_KEY = 'rx-theme-follow';
/** 字体大小档位（百分比，白名单封闭集）：与 index.html 内联脚本共用存储键。 */
export const FONT_SCALE_STORAGE_KEY = 'rx-font-scale';
export const DEFAULT_FONT_SCALE = 100;
export const FONT_SCALE_OPTIONS = [
  { value: 90, label: '小' },
  { value: 100, label: '标准' },
  { value: 110, label: '大' },
  { value: 125, label: '特大' },
] as const;

export type FontScale = (typeof FONT_SCALE_OPTIONS)[number]['value'];

export function isFontScale(value: unknown): value is FontScale {
  return FONT_SCALE_OPTIONS.some((option) => option.value === value);
}

/** 解析初始字号档位（与 index.html 内联脚本同一套规则）：白名单外/缺省一律回落标准。 */
export function resolveInitialFontScale(storage: Pick<Storage, 'getItem'> = window.localStorage): FontScale {
  const saved = Number(storage.getItem(FONT_SCALE_STORAGE_KEY));
  return isFontScale(saved) ? saved : DEFAULT_FONT_SCALE;
}

/** 字号档位落到 html 根字号乘数（styles.css 的 --user-font-scale），全站 rem 与 antd 种子随之缩放。 */
export function applyFontScaleToDocument(scale: FontScale): void {
  if (scale === DEFAULT_FONT_SCALE) {
    document.documentElement.style.removeProperty('--user-font-scale');
    return;
  }
  document.documentElement.style.setProperty('--user-font-scale', String(scale / 100));
}

export interface ThemeSchemeMeta {
  key: ThemeKey;
  /** 完整名称，如「方案 A · 晴空蓝」 */
  name: string;
  /** 一句定位说明（方案卡副题） */
  description: string;
  mode: 'light' | 'dark';
  /** 方案卡迷你预览用色（对齐 04A_设置工作台_外观设置原型.html 的 tp 结构） */
  preview: {
    page: string;
    top: string;
    nav: string;
    chip: string;
    card: string;
    border: string;
  };
}

/** 五套方案元数据（顺序即展示顺序；键名/顺序已拍板，勿改）。 */
export const THEME_SCHEMES: ThemeSchemeMeta[] = [
  {
    key: 'a-qingkong',
    name: '方案 A · 晴空蓝',
    description: '现行基线 · Ant Design 系（默认）',
    mode: 'light',
    preview: { page: '#eef1f6', top: '#ffffff', nav: '#ffffff', chip: '#1677ff', card: '#ffffff', border: '#e5e9f0' },
  },
  {
    key: 'b-xuanye',
    name: '方案 B · 玄夜',
    description: '专业暗色 · 长时间盯屏/投屏',
    mode: 'dark',
    preview: { page: '#0f1216', top: '#171c23', nav: '#171c23', chip: '#1668dc', card: '#171c23', border: '#2a3340' },
  },
  {
    key: 'c-dianqing',
    name: '方案 C · 靛青纸墨',
    description: '沉稳商务 · 深靛双色壳',
    mode: 'light',
    preview: { page: '#f2f2f7', top: '#ffffff', nav: '#232150', chip: '#4f46e5', card: '#ffffff', border: '#e3e3ee' },
  },
  {
    key: 'd-qingbi',
    name: '方案 D · 青碧山水',
    description: '清逸自然 · 政企绿系',
    mode: 'light',
    preview: { page: '#eff5f3', top: '#ffffff', nav: '#ffffff', chip: '#0e7f74', card: '#ffffff', border: '#dde8e4' },
  },
  {
    key: 'e-baolan',
    name: '方案 E · 宝蓝领航',
    description: '深蓝侧栏 · 配色取自追溯原型',
    mode: 'light',
    preview: { page: '#eef1f6', top: '#ffffff', nav: '#04387a', chip: '#005efd', card: '#ffffff', border: '#e5e9f0' },
  },
];

const THEME_KEYS = THEME_SCHEMES.map((scheme) => scheme.key);

export function isThemeKey(value: unknown): value is ThemeKey {
  return typeof value === 'string' && (THEME_KEYS as string[]).includes(value);
}

/**
 * antd ConfigProvider 主题映射（各方案文档 §5）：A/E 不覆盖颜色，仅注入缩放种子令牌。
 * 恒返回对象：ConfigProvider 的 theme 在 undefined ↔ 对象 间切换会重挂载子树（丢工作台内部状态）。
 * uiScale：antd v6 强制 CSS 变量模式，尺寸值落在 --ant-* 定义块，px2remTransformer 触及不到；
 * 故按视口缩放因子（html 流体根字号 ÷ 16，见 styles.css 全局自适应基准）换算种子令牌，
 * fontSize/sizeUnit/sizeStep/controlHeight/borderRadius 缩放后，全部派生尺寸令牌随之等比生成。
 */
export function antdThemeFor(key: ThemeKey, uiScale = 1): ThemeConfig {
  const s = (value: number) => Math.round(value * uiScale * 100) / 100;
  const seed = {
    fontSize: s(14),
    sizeUnit: s(4),
    sizeStep: s(4),
    controlHeight: s(32),
    borderRadius: s(6),
  };
  switch (key) {
    case 'b-xuanye':
      return {
        algorithm: antdTheme.darkAlgorithm,
        token: { ...seed, colorPrimary: '#1668dc', colorBgBase: '#0f1216', colorTextBase: '#e8edf4' },
      };
    case 'c-dianqing':
      return { token: { ...seed, colorPrimary: '#4f46e5', colorInfo: '#4f46e5', borderRadius: s(8) } };
    case 'd-qingbi':
      return { token: { ...seed, colorPrimary: '#0e7f74', colorSuccess: '#4b9c2f', borderRadius: s(11) } };
    default:
      return { token: { ...seed } };
  }
}

/** 视口缩放因子 = html 流体根字号 ÷ 16（styles.css 全局自适应基准）。 */
function readUiScale(): number {
  if (typeof window === 'undefined') {
    return 1;
  }
  const px = Number.parseFloat(window.getComputedStyle(document.documentElement).fontSize);
  return Number.isFinite(px) && px > 0 ? px / 16 : 1;
}

export function systemPrefersDark(): boolean {
  return typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)').matches
    : false;
}

export interface ThemePreference {
  themeKey: ThemeKey;
  followSystem: boolean;
}

/**
 * 解析初始偏好（与 index.html 内联脚本同一套规则）：
 * 跟随系统开启 → 按系统深浅色在 a/b 间取值；否则取本地保存的手动选择；无效/缺省回落默认。
 */
export function resolveInitialTheme(storage: Pick<Storage, 'getItem'> = window.localStorage): ThemePreference {
  const followSystem = storage.getItem(THEME_FOLLOW_STORAGE_KEY) === '1';
  if (followSystem) {
    return { themeKey: systemPrefersDark() ? DARK_THEME : DEFAULT_THEME, followSystem: true };
  }
  const saved = storage.getItem(THEME_STORAGE_KEY);
  return { themeKey: isThemeKey(saved) ? saved : DEFAULT_THEME, followSystem: false };
}

export function applyThemeToDocument(key: ThemeKey): void {
  document.documentElement.dataset.theme = key;
}

interface ThemeContextValue extends ThemePreference {
  /** 手动选择方案：即时生效、写本地偏好、手动优先于系统（自动关闭跟随开关）。 */
  selectTheme: (key: ThemeKey) => void;
  /** 跟随系统深浅色开关：仅在 a-qingkong ⇄ b-xuanye 间自动切换。 */
  setFollowSystem: (on: boolean) => void;
  antdThemeConfig: ThemeConfig;
  /** 视口缩放因子（外观预览按草稿主题构建 antdThemeFor 时复用）。 */
  uiScale: number;
  /** 用户字体大小档位（本地偏好，即时生效）。 */
  fontScale: FontScale;
  selectFontScale: (scale: FontScale) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState<ThemePreference>(resolveInitialTheme);
  // 初始挂载时 --user-font-scale 已由 index.html 内联脚本先行设置，readUiScale 实测值天然含档位因子。
  const [fontScale, setFontScale] = useState<FontScale>(resolveInitialFontScale);
  const [uiScale, setUiScale] = useState<number>(readUiScale);

  // 视口变化 → 流体根字号变化 → antd 种子令牌重算（rAF 合并，StrictMode 安全）。
  useEffect(() => {
    let frame = 0;
    const onResize = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => setUiScale(readUiScale()));
    };
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      window.cancelAnimationFrame(frame);
    };
  }, []);

  // data-theme 已由 index.html 内联脚本先行设置（防 FOUC）；此处保证 React 状态与其一致并跟随变更。
  useEffect(() => {
    applyThemeToDocument(preference.themeKey);
  }, [preference.themeKey]);

  // 跟随系统：监听 prefers-color-scheme（带 cleanup，StrictMode 安全）。
  useEffect(() => {
    if (!preference.followSystem || typeof window.matchMedia !== 'function') {
      return;
    }
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const sync = () => {
      setPreference((current) =>
        current.followSystem
          ? { ...current, themeKey: query.matches ? DARK_THEME : DEFAULT_THEME }
          : current,
      );
    };
    sync();
    query.addEventListener('change', sync);
    return () => {
      query.removeEventListener('change', sync);
    };
  }, [preference.followSystem]);

  const selectTheme = useCallback((key: ThemeKey) => {
    window.localStorage.setItem(THEME_STORAGE_KEY, key);
    window.localStorage.setItem(THEME_FOLLOW_STORAGE_KEY, '0');
    setPreference({ themeKey: key, followSystem: false });
  }, []);

  const selectFontScale = useCallback((scale: FontScale) => {
    window.localStorage.setItem(FONT_SCALE_STORAGE_KEY, String(scale));
    applyFontScaleToDocument(scale);
    setFontScale(scale);
    // getComputedStyle 同步反映新根字号 → antd 种子令牌立即重算（theme 恒为对象，不重挂载）。
    setUiScale(readUiScale());
  }, []);

  const setFollowSystem = useCallback(
    (on: boolean) => {
      window.localStorage.setItem(THEME_FOLLOW_STORAGE_KEY, on ? '1' : '0');
      if (on) {
        setPreference({ themeKey: systemPrefersDark() ? DARK_THEME : DEFAULT_THEME, followSystem: true });
        return;
      }
      // 关闭跟随：当前生效主题固化为手动偏好，刷新后保持。
      window.localStorage.setItem(THEME_STORAGE_KEY, preference.themeKey);
      setPreference((current) => ({ ...current, followSystem: false }));
    },
    [preference.themeKey],
  );

  const value = useMemo<ThemeContextValue>(
    () => ({
      ...preference,
      selectTheme,
      setFollowSystem,
      antdThemeConfig: antdThemeFor(preference.themeKey, uiScale),
      uiScale,
      fontScale,
      selectFontScale,
    }),
    [preference, selectTheme, setFollowSystem, uiScale, fontScale, selectFontScale],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme 必须在 ThemeProvider 内使用');
  }
  return context;
}

/** antd ConfigProvider 随主题联动的包装（供 App 根部使用）。 */
export function ThemedConfigProvider({ locale, children }: { locale: Locale; children: ReactNode }) {
  const { antdThemeConfig } = useTheme();
  return (
    <ConfigProvider locale={locale} theme={antdThemeConfig}>
      {children}
    </ConfigProvider>
  );
}

/** 读取当前主题生效的图表分类色板（--chart-1..6，各方案文档 §4）。 */
export function readChartPalette(): string[] {
  const style = getComputedStyle(document.documentElement);
  const palette = [1, 2, 3, 4, 5, 6]
    .map((index) => style.getPropertyValue(`--chart-${index}`).trim())
    .filter(Boolean);
  // 非浏览器环境（jsdom）读不到令牌时回落方案 A 色板
  return palette.length === 6 ? palette : ['#1677ff', '#0fa8a8', '#f08c00', '#8b5cf6', '#2f9e4f', '#d6409f'];
}
