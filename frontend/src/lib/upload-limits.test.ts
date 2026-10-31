import { describe, expect, it } from 'vitest';
import { mergeSelectedFiles } from './upload-limits';

const file = (name: string, body: string, lastModified: number) =>
  new File([body], name, { type: 'application/pdf', lastModified });

describe('mergeSelectedFiles', () => {
  it('重复选中同一份文件只留一份', () => {
    const a = file('虚构安装合同.pdf', 'abc', 1000);
    const again = file('虚构安装合同.pdf', 'abc', 1000);
    expect(mergeSelectedFiles([a], [again])).toHaveLength(1);
  });

  it('分批添加同名同大小、但不是同一份的材料：两份都保留，交给后端编号', () => {
    const first = file('虚构安装合同.pdf', 'abc', 1000);
    const other = file('虚构安装合同.pdf', 'xyz', 2000);
    const merged = mergeSelectedFiles([first], [other]);
    expect(merged).toHaveLength(2);
    expect(merged[1]).toBe(other);
  });

  it('同一批里重复的文件也去重', () => {
    const a = file('虚构审批表.pdf', 'abc', 1000);
    expect(mergeSelectedFiles([], [a, file('虚构审批表.pdf', 'abc', 1000)])).toHaveLength(1);
  });
});
