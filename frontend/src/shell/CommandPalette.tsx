import { Empty, Input, Modal, Spin, Tag, theme } from 'antd';
import type { InputRef } from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import { searchApi, type SearchHitRead, type SearchResultsRead } from '../api/search';
import { highlightSnippet, searchEntityMeta } from '../view-models/search';
import { SearchOutlined } from '../ui/icons';

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  onNavigate: (hit: SearchHitRead) => void;
}

const DEBOUNCE_MS = 250;
const PER_GROUP = 6;

/**
 * ⌘K 全局命令面板（工作包 05 篇 P3）：跨项目检索五类资产、分组结果、键盘导航、深链跳转。
 * 只叠加一个入口，不重设计任何工作台内部交互（README 不变式 8）。导航由父级 onNavigate 注入
 * （P3 打桩、P4 接实跨项目深链）。
 */
export function CommandPalette({ open, onClose, onNavigate }: CommandPaletteProps) {
  const { token } = theme.useToken();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResultsRead | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<InputRef>(null);
  const rowRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // 扁平化命中列表（跨组）供 ↑↓/Enter 键盘导航寻址。
  const flatHits = useMemo<SearchHitRead[]>(
    () => (results?.groups ?? []).flatMap((g) => g.hits ?? []),
    [results],
  );

  // 打开时重置状态并聚焦输入。
  useEffect(() => {
    if (open) {
      setQuery('');
      setResults(null);
      setActiveIndex(0);
      setLoading(false);
      // Modal 挂载后聚焦（autoFocus 在部分 antd 版本对 Modal 内 Input 不稳，显式聚焦兜底）。
      const t = window.setTimeout(() => inputRef.current?.focus(), 60);
      return () => window.clearTimeout(t);
    }
    return undefined;
  }, [open]);

  // 去抖检索（250ms）；cancelled 忽略过期响应（本仓通用 idiom）。
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults(null);
      setLoading(false);
      return undefined;
    }
    setLoading(true);
    let cancelled = false;
    const timer = window.setTimeout(() => {
      searchApi
        .search({ q, limit: PER_GROUP })
        .then((res) => {
          if (!cancelled) {
            setResults(res);
            setActiveIndex(0);
          }
        })
        .catch(() => {
          if (!cancelled) setResults({ query: q, groups: [], total: 0 });
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  // 高亮行滚入视口。
  useEffect(() => {
    rowRefs.current[activeIndex]?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, Math.max(0, flatHits.length - 1)));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const hit = flatHits[activeIndex];
      if (hit) onNavigate(hit);
    }
  };

  const trimmed = query.trim();
  const showEmpty = !loading && results !== null && flatHits.length === 0;

  // 组渲染时维护跨组连续下标，与 flatHits 对齐。
  let runningIndex = -1;

  return (
    <Modal
      open={open}
      onCancel={onClose}
      footer={null}
      closable={false}
      destroyOnHidden
      width="min(40rem, 92vw)"
      style={{ top: '9vh' }}
      styles={{ body: { padding: 0 } }}
      aria-label="全局检索命令面板"
    >
      <div onKeyDown={handleKeyDown}>
        <Input
          ref={inputRef}
          size="large"
          variant="borderless"
          allowClear
          prefix={<SearchOutlined aria-hidden="true" style={{ color: token.colorTextTertiary }} />}
          placeholder="搜索需求条目、知识项、图表、文档、材料（跨全部项目）"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{
            padding: '0.75rem 1rem',
            borderBottom: `1px solid ${token.colorBorderSecondary}`,
          }}
        />

        <div
          role="listbox"
          aria-label="检索结果"
          style={{ maxHeight: '58vh', overflowY: 'auto', padding: '0.25rem' }}
        >
          {loading && (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '2rem' }}>
              <Spin />
            </div>
          )}

          {!loading && !trimmed && (
            <div style={{ padding: '2rem 1rem', color: token.colorTextTertiary, textAlign: 'center' }}>
              键入关键词或编号（如 REQ-001）开始检索
            </div>
          )}

          {showEmpty && (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="未找到匹配结果"
              style={{ padding: '2rem 0' }}
            />
          )}

          {!loading &&
            (results?.groups ?? []).map((group) => {
              const hits = group.hits ?? [];
              const total = group.total ?? hits.length;
              return (
                <div key={group.entity_type} style={{ marginBottom: '0.25rem' }}>
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      padding: '0.5rem 0.75rem 0.25rem',
                      fontSize: '0.75rem',
                      color: token.colorTextTertiary,
                      fontWeight: 600,
                    }}
                  >
                    <span>{group.label}</span>
                    <span>{total > hits.length ? `${hits.length} / ${total}` : total}</span>
                  </div>

                  {hits.map((hit) => {
                    runningIndex += 1;
                    const index = runningIndex;
                    const meta = searchEntityMeta(hit.entity_type);
                    const Icon = meta.icon;
                    const active = index === activeIndex;
                    return (
                      <button
                        key={`${hit.project_id}:${hit.entity_type}:${hit.ref}`}
                        ref={(el) => {
                          rowRefs.current[index] = el;
                        }}
                      type="button"
                      role="option"
                      aria-selected={active}
                      onMouseEnter={() => setActiveIndex(index)}
                      onClick={() => onNavigate(hit)}
                      style={{
                        display: 'flex',
                        alignItems: 'flex-start',
                        gap: '0.625rem',
                        width: '100%',
                        textAlign: 'left',
                        border: 'none',
                        cursor: 'pointer',
                        padding: '0.5rem 0.75rem',
                        borderRadius: token.borderRadius,
                        background: active ? token.controlItemBgActive : 'transparent',
                      }}
                    >
                      <Icon style={{ color: token.colorTextSecondary, marginTop: '0.2rem' }} />
                      <span style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                          <span
                            style={{
                              flex: 1,
                              minWidth: 0,
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                              color: token.colorText,
                            }}
                          >
                            {hit.title}
                          </span>
                          <Tag color={meta.tagColor} style={{ marginInlineEnd: 0, flexShrink: 0 }}>
                            {hit.project_name}
                          </Tag>
                        </span>
                        {hit.snippet && (
                          <span
                            style={{
                              display: 'block',
                              fontSize: '0.8125rem',
                              color: token.colorTextTertiary,
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {highlightSnippet(hit.snippet, trimmed).map((part, i) =>
                              part.hit ? (
                                <mark
                                  key={i}
                                  style={{ background: 'transparent', color: token.colorPrimary, padding: 0, fontWeight: 600 }}
                                >
                                  {part.text}
                                </mark>
                              ) : (
                                <span key={i}>{part.text}</span>
                              ),
                            )}
                          </span>
                        )}
                      </span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
        </div>
      </div>
    </Modal>
  );
}
