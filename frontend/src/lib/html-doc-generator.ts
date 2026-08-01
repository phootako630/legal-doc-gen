// Word 兼容 HTML 起诉状导出：生成符合 MSO（Microsoft Office HTML）规范的 .doc 文件，带跨页页眉与页脚页码，供 Word / WPS 直接打开
import type { ComplaintDocument, DocMeta } from './types';

const FONT_FAMILY = "'仿宋_GB2312','仿宋',FangSong,serif";

// ── 安全：src 均来自模型对上传 PDF 的抽取/生成结果，属不可信输入，插值前必须转义 ──────

function escapeHtml(input: string): string {
  return input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ── 高亮标记清理 ──────────────────────────────────────────────────────────────
// 审核阶段的【高亮缺失：X】【高亮冲突：X】⚠️ 待核实：X 等标记只用于屏幕预览着色，
// 最终 Word 文件里只保留裸值；但字面量【待补充】保留——它是留给律师手填的可见提示。

function stripReviewMarkers(text: string): string {
  return text
    .replace(/【高亮缺失：([^】]*)】/g, '$1')
    .replace(/【高亮冲突：([^】]*)】/g, '$1')
    .replace(/⚠️\s*待核实[：:]?\s*/g, '');
}

// ── 行分类：按中国民事起诉状惯例，不同行需要不同排版（首行缩进/悬挂缩进/不缩进） ────

type LineKind = 'title' | 'heading' | 'claim' | 'noIndent' | 'body';

// 编号诉讼请求项：一、 / （一） / (一) / 1. 等
const CLAIM_RE = /^([一二三四五六七八九十百]+[、.．]|[（(][一二三四五六七八九十]+[)）]|\d+[.、．])/;
// 标准小节标题
const HEADING_RE = /^(诉讼请求|事实与理由|事实和理由)[:：]?\s*$/;
// 此致敬语（其下一行通常是法院名称，同样不缩进，见 classifyLine 的 prevWasSalutation 参数）
const SALUTATION_RE = /^此致[:：]?\s*$/;
// 当事人信息行、落款、日期——起诉状惯例中这些行不首行缩进
const NO_INDENT_RE =
  /^(原告|被告|具状人|起诉人|上诉人)[:：]|^\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*$/;

function isSalutationLine(line: string): boolean {
  return SALUTATION_RE.test(line);
}

function classifyLine(line: string, isFirstNonEmpty: boolean, prevWasSalutation: boolean): LineKind {
  if (isFirstNonEmpty) return 'title';
  if (isSalutationLine(line)) return 'noIndent';
  if (prevWasSalutation) return 'noIndent';
  if (HEADING_RE.test(line)) return 'heading';
  if (NO_INDENT_RE.test(line)) return 'noIndent';
  if (CLAIM_RE.test(line)) return 'claim';
  return 'body';
}

// ── 排版：Word 不读常规 CSS 布局属性，缩进必须用 em（中文字体下 1em = 一个字符宽度），
//    不能用 pt——这正是"首行缩进两字符"这句话的字面含义。字号/行距等用 pt，不用 px。 ──

function paragraphStyle(kind: LineKind): string {
  const base = `font-family:${FONT_FAMILY};font-size:14pt;line-height:28pt;margin:0;`;
  switch (kind) {
    case 'title':
      return `font-family:${FONT_FAMILY};font-size:24pt;font-weight:bold;text-align:center;line-height:28pt;margin:0 0 18pt 0;`;
    case 'heading':
      return `font-family:${FONT_FAMILY};font-size:14pt;font-weight:bold;line-height:28pt;margin:12pt 0 6pt 0;`;
    case 'claim':
      // 悬挂缩进：整段左移两字符，首行再反向缩回两字符，实现"编号顶格、后续行对齐编号后文字"的效果
      return `${base}margin-left:2em;text-indent:-2em;`;
    case 'noIndent':
      return base;
    case 'body':
    default:
      return `${base}text-indent:2em;`;
  }
}

function buildBodyHtml(doc: ComplaintDocument): string {
  const cleanedText = stripReviewMarkers(doc);
  const lines = cleanedText.split('\n');

  let firstNonEmptySeen = false;
  let prevWasSalutation = false;
  const paragraphs: string[] = [];

  for (const rawLine of lines) {
    const line = rawLine.trim();
    const isFirst = !firstNonEmptySeen && line.length > 0;
    if (line) firstNonEmptySeen = true;

    const kind = classifyLine(line, isFirst, prevWasSalutation);
    prevWasSalutation = isSalutationLine(line);

    const content = line ? escapeHtml(line) : '&nbsp;';
    paragraphs.push(`<p style="${paragraphStyle(kind)}">${content}</p>`);
  }

  return paragraphs.join('\n');
}

// 左右两栏页眉（案件名 + 律所名）用表格实现——Word/WPS 对 flex/float 类多栏布局支持不可靠，
// 表格是 MSO HTML 里最稳妥的多栏排版方式
function buildHeaderHtml(meta: DocMeta): string {
  const headerText = escapeHtml(meta.headerText).trim() || '&nbsp;';

  if (!meta.headerRight) {
    return `<div style="mso-element:header" id="h1">
  <p style="margin:0; font-family:${FONT_FAMILY};
            font-size:9pt; text-align:center; border-bottom:1pt solid #000;
            padding-bottom:2pt;">
    ${headerText}
  </p>
</div>`;
  }

  const headerRight = escapeHtml(meta.headerRight).trim() || '&nbsp;';
  return `<div style="mso-element:header" id="h1">
  <table style="width:100%;border-collapse:collapse;border-bottom:1pt solid #000;" cellpadding="0" cellspacing="0">
    <tr>
      <td style="width:34%;font-family:${FONT_FAMILY};font-size:9pt;text-align:left;padding-bottom:2pt;">&nbsp;</td>
      <td style="width:32%;font-family:${FONT_FAMILY};font-size:9pt;text-align:center;padding-bottom:2pt;">${headerText}</td>
      <td style="width:34%;font-family:${FONT_FAMILY};font-size:9pt;text-align:right;padding-bottom:2pt;">${headerRight}</td>
    </tr>
  </table>
</div>`;
}

/**
 * 将起诉状全文构建为 Word 兼容的 Office HTML 字符串。
 *
 * 注意：以下 MSO 专有机制不是可省略的装饰，删掉任何一处都会导致 Word 打开后
 * 没有页眉/页脚或页码不递增——不要用"看起来等价"的常规 CSS 替换它们：
 *   - xmlns:o / xmlns:w 命名空间声明：Word 靠它识别这是一份 Office 文档
 *   - <meta charset="utf-8">：没有它 Word 会按 GBK 解码，中文全部变乱码
 *   - @page Section1 + div.Section1{page:Section1}：页面设置/页眉页脚绑定只能通过
 *     命名的 @page 规则表达，不能写成内联样式
 *   - mso-header / mso-footer 必须与页眉页脚 div 的 id 一致
 *   - 页码用 mso-field-code:PAGE / NUMPAGES 写在空 span 上，是 Word 域代码，
 *     span 里不能塞占位文字，否则域不会被 Word 识别为"自动页码"
 */
export function buildComplaintHtml(doc: ComplaintDocument, meta: DocMeta): string {
  const bodyHtml = buildBodyHtml(doc);
  const headerHtml = buildHeaderHtml(meta);

  return `<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">
<head>
<meta charset="utf-8">
<title>民事起诉状</title>
<style>
@page Section1 {
  size: 21cm 29.7cm;
  margin: 2.54cm 3.17cm 2.54cm 3.17cm;
  mso-header-margin: 1.5cm;
  mso-footer-margin: 1.75cm;
  mso-header: h1;
  mso-footer: f1;
  mso-paper-source: 0;
}
div.Section1 { page: Section1; }
</style>
<!--[if gte mso 9]><xml>
  <w:WordDocument>
    <w:View>Print</w:View>
    <w:Zoom>100</w:Zoom>
  </w:WordDocument>
</xml><![endif]-->
</head>
<body>
<div class="Section1">
${bodyHtml}
</div>
${headerHtml}
<div style="mso-element:footer" id="f1">
  <p style="margin:0; font-family:${FONT_FAMILY};
            font-size:9pt; text-align:center;">
    第 <span style="mso-field-code:PAGE"></span> 页
    共 <span style="mso-field-code:NUMPAGES"></span> 页
  </p>
</div>
</body>
</html>`;
}

/** 触发浏览器下载：文件名必须以 .doc 结尾——Word / WPS 都是靠扩展名识别要用 Office HTML 解析器打开 */
export function downloadAsWord(html: string, filename: string): void {
  // UTF-8 BOM 是第二道防线：meta charset 已声明 utf-8，BOM 防止部分 Word/WPS 版本
  // 仍按系统默认编码（GBK）猜测文件编码，导致中文乱码。用 fromCharCode 构造
  // 而非在源码里写一个字面的不可见字符，避免源文件在不同编辑器/编码下被误处理。
  const BOM = String.fromCharCode(0xfeff);
  const blob = new Blob([BOM + html], { type: 'application/msword' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
