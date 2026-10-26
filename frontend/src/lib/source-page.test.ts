// 审核页「查看原件」（#111）：只有出处带文件名、有页码、来自 OCR 的字段才显示原件按钮
import { describe, expect, it } from 'vitest';
import { sourcePageTarget } from './source-page';

describe('sourcePageTarget', () => {
  it('OCR 来源、有页码 → 文件名与页码', () => {
    expect(
      sourcePageTarget({ src: '《虚构安装合同.pdf》第三条（OCR识别，请核实）', page: 3 }),
    ).toEqual({ file: '虚构安装合同.pdf', page: 3 });
    expect(sourcePageTarget({ src: '《虚构安装合同.pdf》第三条', page: 2, channel: 'ocr' })).toEqual({
      file: '虚构安装合同.pdf',
      page: 2,
    });
  });

  it('文字版来源、没有页码、没有文件名 → 不显示', () => {
    expect(sourcePageTarget({ src: '《虚构审批表.pdf》', page: 1, channel: 'text' })).toBeNull();
    expect(sourcePageTarget({ src: '《虚构安装合同.pdf》（OCR识别，请核实）', page: null })).toBeNull();
    expect(sourcePageTarget({ src: '按律师规则：扫描件未识别', page: 1 })).toBeNull();
  });
});
