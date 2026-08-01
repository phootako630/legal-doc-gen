// Word 文档生成：用 docx-js 在浏览器端将起诉状全文打包为 .docx 并触发下载
// 格式规范：A4、宋体、标题居中加粗、段落首行缩进两字符、1.5 倍行距
import { Document, Packer, Paragraph, TextRun, AlignmentType, type IParagraphOptions } from 'docx';

// ── 常量 ──────────────────────────────────────────────────────────────────────

const FONT = '宋体';
const SIZE_TITLE = 32;   // 16pt（half-points）
const SIZE_SECTION = 28; // 14pt
const SIZE_BODY = 24;    // 12pt
const LINE_SPACING = 360; // 1.5倍行距（240 = 单倍）
const FIRST_LINE_INDENT = 480; // 2个12pt中文字符 = 480 twips

// 用于识别一级/二级条目编号的正则
const SECTION_RE = /^[一二三四五六七八九十百]+[、.．]|^\([一二三四五六七八九十]+\)|^\d+[.、．]/;

// ── 清除高亮标记（预览用标记不应写入 Word） ───────────────────────────────────

function stripHighlights(text: string): string {
  return text
    .replace(/【高亮缺失：([^】]*)】/g, '$1')
    .replace(/【高亮冲突：([^】]*)】/g, '$1')
    .replace(/⚠️\s*待核实[：:]?\s*/g, '');
}

// ── 行类型判断 ────────────────────────────────────────────────────────────────

type LineKind = 'title' | 'section' | 'empty' | 'body';

function classifyLine(line: string, isFirstNonEmpty: boolean): LineKind {
  if (!line.trim()) return 'empty';
  if (isFirstNonEmpty) return 'title';
  if (SECTION_RE.test(line.trim())) return 'section';
  return 'body';
}

// ── 构建段落 ──────────────────────────────────────────────────────────────────

function buildParagraph(text: string, kind: LineKind): Paragraph {
  const base: IParagraphOptions = {
    spacing: { line: LINE_SPACING },
    children: [],
  };

  switch (kind) {
    case 'empty':
      return new Paragraph({ ...base, children: [new TextRun({ text: '', font: FONT, size: SIZE_BODY })] });

    case 'title':
      return new Paragraph({
        ...base,
        alignment: AlignmentType.CENTER,
        spacing: { line: LINE_SPACING, after: 120 },
        children: [new TextRun({ text, font: FONT, size: SIZE_TITLE, bold: true })],
      });

    case 'section':
      return new Paragraph({
        ...base,
        children: [new TextRun({ text, font: FONT, size: SIZE_SECTION, bold: true })],
      });

    case 'body':
    default:
      return new Paragraph({
        ...base,
        indent: { firstLine: FIRST_LINE_INDENT },
        children: [new TextRun({ text, font: FONT, size: SIZE_BODY })],
      });
  }
}

// ── 公开接口 ──────────────────────────────────────────────────────────────────

/** 将起诉状文本生成 Word 文件并触发浏览器下载 */
export async function downloadComplaintDocx(
  complaintText: string,
  filename = '起诉状.docx',
): Promise<void> {
  const clean = stripHighlights(complaintText);
  const lines = clean.split('\n');

  let firstNonEmptySeen = false;
  const paragraphs: Paragraph[] = [];

  for (const rawLine of lines) {
    const line = rawLine.trim();
    const isFirst = !firstNonEmptySeen && line.length > 0;
    if (line) firstNonEmptySeen = true;

    const kind = classifyLine(line, isFirst);
    paragraphs.push(buildParagraph(line, kind));
  }

  const doc = new Document({
    sections: [
      {
        properties: {
          page: {
            size: { width: 11906, height: 16838 }, // A4（twips）
            margin: { top: 1440, right: 1800, bottom: 1440, left: 1800 },
          },
        },
        children: paragraphs,
      },
    ],
  });

  const blob = await Packer.toBlob(doc);
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
