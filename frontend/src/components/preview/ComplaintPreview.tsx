// 起诉状全文预览：解析高亮标记并按类型着色，模拟 A4 纸张排版
import { cn } from '@/lib/utils';

// ── 内联段落解析 ──────────────────────────────────────────────────────────────

type SegType = 'normal' | 'missing' | 'conflict' | 'uncertain';

interface Seg {
  text: string;
  type: SegType;
}

const HIGHLIGHT_RE =
  /【高亮缺失：([^】]*)】|【待补充】|【高亮冲突：([^】]*)】|⚠️\s*待核实[：:]\s*([^【⚠️\n]*)/g;

function parseSegments(line: string): Seg[] {
  const segs: Seg[] = [];
  let last = 0;

  for (const m of line.matchAll(HIGHLIGHT_RE)) {
    const start = m.index!;
    if (start > last) segs.push({ text: line.slice(last, start), type: 'normal' });

    if (m[1] !== undefined)      segs.push({ text: m[1] || '待补充',   type: 'missing' });
    else if (m[0] === '【待补充】') segs.push({ text: '待补充',           type: 'missing' });
    else if (m[2] !== undefined) segs.push({ text: m[2],              type: 'conflict' });
    else                         segs.push({ text: '⚠️ ' + m[3].trim(), type: 'uncertain' });

    last = start + m[0].length;
  }

  if (last < line.length) segs.push({ text: line.slice(last), type: 'normal' });
  return segs;
}

// ── 行类型判断 ────────────────────────────────────────────────────────────────

type LineKind = 'title' | 'section' | 'empty' | 'body';

const SECTION_RE = /^[一二三四五六七八九十百]+[、.．]|^\([一二三四五六七八九十]+\)|^\d+[.、．]/;

function classifyLine(line: string, isFirstNonEmpty: boolean): LineKind {
  if (!line.trim()) return 'empty';
  if (isFirstNonEmpty) return 'title';
  if (SECTION_RE.test(line.trim())) return 'section';
  return 'body';
}

// ── 高亮颜色 ──────────────────────────────────────────────────────────────────

const SEG_CLASS: Record<SegType, string> = {
  normal:    '',
  missing:   'rounded bg-red-100 px-0.5 text-red-800 ring-1 ring-red-200',
  conflict:  'rounded bg-orange-100 px-0.5 text-orange-800 ring-1 ring-orange-200',
  uncertain: 'rounded bg-amber-100 px-0.5 text-amber-800 ring-1 ring-amber-200',
};

// ── 渲染 ──────────────────────────────────────────────────────────────────────

interface ComplaintPreviewProps {
  text: string;
}

export function ComplaintPreview({ text }: ComplaintPreviewProps) {
  const lines = text.split('\n');
  let firstNonEmptySeen = false;

  return (
    <div className="a4-paper mx-auto">
      {lines.map((line, idx) => {
        const trimmed = line.trim();
        const isFirst = !firstNonEmptySeen && trimmed.length > 0;
        if (trimmed) firstNonEmptySeen = true;

        const kind = classifyLine(trimmed, isFirst);

        if (kind === 'empty') {
          return <div key={idx} className="h-5" />;
        }

        const segs = parseSegments(line);

        return (
          <p
            key={idx}
            className={cn(
              'min-h-[1.8em]',
              kind === 'title'   && 'mb-2 text-center text-[16px] font-bold tracking-wider',
              kind === 'section' && 'mt-3 font-bold',
              kind === 'body'    && 'indent-[2em]',
            )}
          >
            {segs.map((seg, si) =>
              seg.type === 'normal' ? (
                <span key={si}>{seg.text}</span>
              ) : (
                <mark key={si} className={cn('not-italic', SEG_CLASS[seg.type])}>
                  {seg.text}
                </mark>
              ),
            )}
          </p>
        );
      })}
    </div>
  );
}
