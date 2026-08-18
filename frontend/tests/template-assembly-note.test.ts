// 模板定制器「模板默认文本 / 人工撰稿」两个勾选的三态说明（T20260720 · D 项）
// 两者可同选是用户拍板的既定行为；本组测试盯住的是「说明有没有把三种组合各自的结果讲清」。
import { describe, expect, it } from 'vitest';
import { assemblyNoteFor } from '../src/view-models/template-designer';

describe('内容装配三态说明', () => {
  it('只勾默认文本：说明原样进文档、每次一样', () => {
    const note = assemblyNoteFor(['boilerplate']);
    expect(note?.kind).toBe('boilerplate_only');
    expect(note?.description).toContain('原样');
    expect(note?.description).toContain('每次发布都一样');
  });

  it('只勾人工撰稿：说明没有底稿、由人写或 AI 起草，且初稿不自动确认', () => {
    const note = assemblyNoteFor(['authored_text']);
    expect(note?.kind).toBe('authored_only');
    expect(note?.description).toContain('没有底稿');
    expect(note?.description).toContain('不会自动确认');
  });

  it('两者同选：说明默认文本作预填底稿、改写后覆盖之', () => {
    const note = assemblyNoteFor(['boilerplate', 'authored_text']);
    expect(note?.kind).toBe('both');
    expect(note?.description).toContain('底稿');
    expect(note?.description).toContain('预先填好');
    expect(note?.description).toContain('不再出现');
    // 没人动过时的行为也要讲——否则用户不知道不改会怎样
    expect(note?.description).toContain('没人动过');
  });

  it('勾选顺序不影响判定', () => {
    expect(assemblyNoteFor(['authored_text', 'boilerplate'])?.kind).toBe('both');
  });

  it('和条目/图表/材料一起勾选时，仍按这两个勾选定三态', () => {
    expect(assemblyNoteFor(['requirement_item:functional', 'boilerplate'])?.kind).toBe('boilerplate_only');
    expect(assemblyNoteFor(['chart', 'material', 'authored_text'])?.kind).toBe('authored_only');
  });

  it('两者都没勾：不显示说明（本章由装配的资产成文，与这两个勾选无关）', () => {
    expect(assemblyNoteFor([])).toBeNull();
    expect(assemblyNoteFor(['requirement_item:functional', 'chart'])).toBeNull();
  });

  it('三态标题各不相同（用户一眼能看出勾选变了）', () => {
    const titles = [
      assemblyNoteFor(['boilerplate'])!.title,
      assemblyNoteFor(['authored_text'])!.title,
      assemblyNoteFor(['boilerplate', 'authored_text'])!.title,
    ];
    expect(new Set(titles).size).toBe(3);
  });

  it('文案里不出现内部词（boilerplate / authored_text 等）', () => {
    const all = [['boilerplate'], ['authored_text'], ['boilerplate', 'authored_text']]
      .map((types) => assemblyNoteFor(types)!)
      .flatMap((note) => [note.title, note.description])
      .join('');
    for (const jargon of ['boilerplate', 'authored_text', '槽位', '装配']) {
      expect(all).not.toContain(jargon);
    }
  });
});
