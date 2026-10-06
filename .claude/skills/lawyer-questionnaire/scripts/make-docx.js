// 把律师确认单（题目 JSON）生成为 Word 文档：律师在每题的回复框里填字母或写说明后发回。
// 用法：node make-docx.js <题目.json> <输出.docx>
// 依赖 docx（npm 包）：npm install --prefix "$SCRATCH/qdeps" docx@9，运行时设 NODE_PATH="$SCRATCH/qdeps/node_modules"
// 题目 JSON 结构见同目录 example.json：meta（标题、副标题、导语）+ GROUPS（分组）+ Q（题目）
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  WidthType, BorderStyle, ShadingType, AlignmentType, LevelFormat, HeadingLevel,
} = require("docx");

const [INPUT, OUTPUT] = process.argv.slice(2);
if (!INPUT || !OUTPUT) {
  console.error("用法：node make-docx.js <题目.json> <输出.docx>");
  process.exit(2);
}
const { meta = {}, GROUPS, Q } = JSON.parse(fs.readFileSync(INPUT, "utf8"));
const TITLE = meta.title || "起诉状规则确认单";
const SUBTITLE = meta.subtitle || "民事起诉状自动生成";
const INTRO = meta.intro || "以下问题需要律师确认写法或取值规则。";
const FIRST_NOS = Q.filter((q) => q.first).map((q) => q.n);

const FONT = { ascii: "Times New Roman", hAnsi: "Times New Roman", eastAsia: "宋体" };
const HEAD_FONT = { ascii: "Times New Roman", hAnsi: "Times New Roman", eastAsia: "黑体" };
const INK = "1C2638";
const SOFT = "4A5568";
const SEAL = "B3261E";
const RULE = "C9D0DB";

// A4，页边距 2.5cm（1417 DXA）→ 正文宽 11906 - 2*1417 = 9072
const CONTENT_W = 9072;
const LETTERS = "ABCDEFG";

const t = (text, opts = {}) => new TextRun({ text, font: FONT, size: 22, color: INK, ...opts });

function para(children, opts = {}) {
  return new Paragraph({ children, spacing: { after: 80, line: 360 }, ...opts });
}

function replyBox(hasOpts) {
  const border = { style: BorderStyle.SINGLE, size: 6, color: RULE };
  const lines = [
    para([t(hasOpts ? "选择（填字母）：" : "做法：", { bold: true })], { spacing: { after: 60 } }),
    para([t("说明：", { bold: true })], { spacing: { after: 60 } }),
    para([t("")]),
    para([t("")]),
  ];
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: [CONTENT_W],
    rows: [
      new TableRow({
        children: [
          new TableCell({
            width: { size: CONTENT_W, type: WidthType.DXA },
            borders: { top: border, bottom: border, left: border, right: border },
            shading: { type: ShadingType.CLEAR, color: "auto", fill: "F7F8FA" },
            margins: { top: 100, bottom: 100, left: 160, right: 160 },
            children: lines,
          }),
        ],
      }),
    ],
  });
}

const children = [];

children.push(
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 120 },
    children: [new TextRun({ text: TITLE, font: HEAD_FONT, size: 36, bold: true, color: INK })],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 300 },
    children: [t(SUBTITLE, { size: 20, color: SOFT })],
  }),
  para([t(INTRO)]),
  para([
    t("填写方法：", { bold: true }),
    t("在每题下方的回复框中写上所选选项的字母（如 A），并在「说明」处补充；不适用或另有做法的，直接在「说明」写明即可。填好后保存并发回本文档。"),
  ]),
  ...(FIRST_NOS.length === 0 ? [] : [para([
    t("标有"),
    t("【建议先答】", { bold: true, color: SEAL }),
    t(`的第 ${FIRST_NOS.join("、")} 题影响大多数案件，请优先回复。`),
  ], { spacing: { after: 240, line: 360 } })]),
);

for (const g of GROUPS) {
  children.push(
    new Paragraph({
      heading: HeadingLevel.HEADING_1,
      spacing: { before: 360, after: 80 },
      border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: RULE, space: 4 } },
      children: [new TextRun({ text: g.title, font: HEAD_FONT, size: 28, bold: true, color: INK })],
    }),
  );
  if (g.note) children.push(para([t(g.note, { size: 20, color: SOFT })], { spacing: { after: 160 } }));

  for (const q of Q.filter((x) => x.g === g.id)) {
    const head = [new TextRun({ text: `第 ${q.n} 题　`, font: HEAD_FONT, size: 23, bold: true, color: SOFT })];
    if (q.first) head.push(new TextRun({ text: "【建议先答】", font: HEAD_FONT, size: 23, bold: true, color: SEAL }));
    head.push(new TextRun({ text: q.title, font: HEAD_FONT, size: 23, bold: true, color: INK }));
    children.push(
      new Paragraph({
        heading: HeadingLevel.HEADING_2,
        keepNext: true,
        spacing: { before: 280, after: 100, line: 360 },
        children: head,
      }),
    );
    children.push(para([t("背景：", { bold: true, size: 21, color: SOFT })], { keepNext: true, spacing: { after: 40 } }));
    for (const line of q.ctx) {
      children.push(
        new Paragraph({
          numbering: { reference: "ctx", level: 0 },
          keepNext: true,
          spacing: { after: 40, line: 340 },
          children: [t(line, { size: 21, color: SOFT })],
        }),
      );
    }
    if (q.opts) {
      children.push(para([t("选项：", { bold: true, size: 21 })], { keepNext: true, spacing: { before: 80, after: 40 } }));
      q.opts.forEach((o, i) => {
        children.push(
          new Paragraph({
            keepNext: true,
            indent: { left: 360 },
            spacing: { after: 40, line: 340 },
            children: [t(`${LETTERS[i]}. `, { bold: true, size: 21 }), t(o, { size: 21 })],
          }),
        );
      });
    }
    children.push(new Paragraph({ spacing: { after: 60 }, keepNext: true, children: [] }));
    children.push(replyBox(!!q.opts));
  }
}

children.push(
  para([t("")], { spacing: { before: 360 } }),
  para([t("回复人：____________　　　日期：____________", { color: SOFT })]),
);

const doc = new Document({
  creator: "起诉状自动生成系统",
  title: TITLE,
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: HEAD_FONT, size: 28, bold: true }, paragraph: { outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: HEAD_FONT, size: 23, bold: true }, paragraph: { outlineLevel: 1 } },
    ],
  },
  numbering: {
    config: [
      {
        reference: "ctx",
        levels: [
          { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 360, hanging: 240 } } } },
        ],
      },
    ],
  },
  sections: [
    {
      properties: {
        page: {
          size: { width: 11906, height: 16838 },
          margin: { top: 1417, bottom: 1417, left: 1417, right: 1417 },
        },
      },
      children,
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  fs.mkdirSync(path.dirname(path.resolve(OUTPUT)), { recursive: true });
  fs.writeFileSync(OUTPUT, buf);
  console.log("已生成", path.resolve(OUTPUT), buf.length, "字节");
});
