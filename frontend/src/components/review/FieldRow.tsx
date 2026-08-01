// 审核表格的单行：字段名 | 值（可点击编辑）| 来源 | 状态 Badge
import { useState, useRef, useEffect } from 'react';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import type { ReviewField, FieldStatus } from '@/lib/types';
import { Pencil } from 'lucide-react';

const STATUS_CONFIG: Record<FieldStatus, { label: string; className: string }> = {
  normal:        { label: '正常',    className: 'border-emerald-200 bg-emerald-50  text-emerald-700 hover:bg-emerald-50' },
  missing:       { label: '缺失',    className: 'border-red-200    bg-red-50     text-red-700     hover:bg-red-50' },
  conflict:      { label: '冲突',    className: 'border-orange-200 bg-orange-50  text-orange-700  hover:bg-orange-50' },
  ocr_uncertain: { label: 'OCR 待核', className: 'border-amber-200  bg-amber-50   text-amber-700   hover:bg-amber-50' },
};

// 按状态着色的行背景（斑马纹叠加状态色）
const ROW_BG: Record<FieldStatus, { even: string; odd: string }> = {
  normal:        { even: 'bg-white',         odd: 'bg-muted/30' },
  missing:       { even: 'bg-red-50/60',     odd: 'bg-red-50/80' },
  conflict:      { even: 'bg-orange-50/60',  odd: 'bg-orange-50/80' },
  ocr_uncertain: { even: 'bg-amber-50/40',   odd: 'bg-amber-50/60' },
};

interface FieldRowProps {
  field: ReviewField;
  onSave: (key: string, value: string) => void;
  isEven: boolean;
}

export function FieldRow({ field, onSave, isEven }: FieldRowProps) {
  const displayValue = field.editedValue !== undefined ? field.editedValue : field.value;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const startEdit = () => { setDraft(displayValue ?? ''); setEditing(true); };

  const commit = () => {
    setEditing(false);
    if (draft !== (displayValue ?? '')) onSave(field.key, draft);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { setEditing(false); return; }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commit(); }
  };

  useEffect(() => { if (editing) inputRef.current?.focus(); }, [editing]);

  const cfg = STATUS_CONFIG[field.status];
  const bg  = ROW_BG[field.status];
  const isEdited = field.editedValue !== undefined;

  return (
    <tr
      className={cn(
        'group border-b border-border/60 transition-colors last:border-0',
        isEven ? bg.even : bg.odd,
      )}
    >
      {/* 字段名 */}
      <td className="py-3 pl-4 pr-3 align-top">
        <span className="text-xs font-semibold text-foreground/75 whitespace-nowrap">
          {field.label}
        </span>
      </td>

      {/* 值（可编辑） */}
      <td className="px-3 py-2.5 align-top min-w-0">
        {editing ? (
          <textarea
            ref={inputRef}
            value={draft}
            aria-label={`编辑${field.label}`}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={handleKeyDown}
            rows={Math.max(2, (draft.match(/\n/g)?.length ?? 0) + 1)}
            className="w-full resize-none rounded-md border border-primary bg-white px-2.5 py-1.5 text-xs leading-relaxed shadow-sm outline-none ring-2 ring-primary/25"
          />
        ) : (
          <button
            type="button"
            onClick={startEdit}
            title="点击编辑"
            className="group/val flex w-full items-start gap-1.5 rounded-md px-1.5 py-1 text-left hover:bg-white/80 hover:shadow-sm transition-all"
          >
            <span
              className={cn(
                'flex-1 break-words text-xs leading-relaxed',
                !displayValue && 'italic text-muted-foreground/60',
                isEdited && 'font-medium text-primary',
              )}
            >
              {displayValue ?? '【待补充】'}
            </span>
            <Pencil className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground/0 transition-all group-hover/val:text-primary/40" />
          </button>
        )}
      </td>

      {/* 来源（hover 显示完整文本） */}
      <td className="px-3 py-3 align-top">
        {field.src ? (
          <Tooltip>
            <TooltipTrigger
              render={<span className="line-clamp-2 cursor-default text-[11px] leading-relaxed text-muted-foreground/60" />}
            >
              {field.src}
            </TooltipTrigger>
            <TooltipContent
              side="left"
              className="max-w-72 whitespace-pre-wrap break-words text-xs leading-relaxed"
            >
              {field.src}
            </TooltipContent>
          </Tooltip>
        ) : (
          <span className="text-[11px] text-muted-foreground/40">—</span>
        )}
      </td>

      {/* Badge */}
      <td className="py-3 pr-4 align-top">
        <Badge variant="outline" className={cn('text-[10px] font-semibold px-1.5 py-0.5', cfg.className)}>
          {cfg.label}
        </Badge>
      </td>
    </tr>
  );
}
