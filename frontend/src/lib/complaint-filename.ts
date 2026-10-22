// 下载文件名：从生成的文书正文取原告 / 申请人与被告 / 被申请人名称，拼成「原告诉被告起诉状_日期.doc」
//
// 正文里的填空值带高亮标记（⟦…⟧）与状态前缀（⚠️ 待核实：、【高亮冲突：…】），取名前先去掉；
// 名称还是【待补充】时不拼进文件名，退回「起诉状_日期.doc」。

const FORBIDDEN = /[\\/:*?"<>|\r\n]/g;

function cleanName(raw: string | undefined): string | null {
  if (!raw) return null;
  const name = raw
    .replace(/[⟦⟧]/g, '')
    .replace(/⚠️\s*待核实[:：]\s*/g, '')
    .replace(/【高亮冲突[:：]([^】]*)】/g, '$1')
    .replace(FORBIDDEN, '')
    .trim();
  if (!name || name.includes('待补充')) return null;
  return name;
}

function partyName(text: string, labels: string[]): string | null {
  for (const label of labels) {
    const m = text.match(new RegExp(`^\\s*${label}[:：]\\s*(.+)$`, 'm'));
    const name = cleanName(m?.[1]);
    if (name) return name;
  }
  return null;
}

function yyyymmdd(date: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}${p(date.getMonth() + 1)}${p(date.getDate())}`;
}

/** 文书下载文件名（必须以 .doc 结尾：Word / WPS 靠扩展名选用 Office HTML 解析器） */
export function complaintFileName(text: string, date: Date = new Date()): string {
  const isArbitration = text.trimStart().startsWith('仲 裁');
  const docName = isArbitration ? '仲裁申请书' : '起诉状';
  // 仲裁申请书写「申请人 / 被申请人」：先匹配「被申请人」，避免「申请人」误配到它
  const plaintiff = isArbitration ? partyName(text, ['申请人']) : partyName(text, ['原告']);
  const defendant = isArbitration ? partyName(text, ['被申请人']) : partyName(text, ['被告']);
  const parties = plaintiff && defendant ? `${plaintiff}诉${defendant}` : '';
  return `${parties}${docName}_${yyyymmdd(date)}.doc`;
}
