// 审核表格的单行：字段名 | 值（可点击编辑）| 来源 | 状态 Badge + 反馈按钮
import { useState, useRef, useEffect } from 'react';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import type { ReviewField, FieldStatus } from '@/lib/types';
import { MessageSquareWarning, Pencil } from 'lucide-react';

const STATUS_CONFIG: Record<FieldStatus, { label: string; className: string }> = {
  normal:        { label: '正常',   className: 'border-status-ok-border       bg-status-ok-bg       text-status-ok       hover:bg-status-ok-bg' },
  missing:       { label: '缺失',   className: 'border-status-missing-border  bg-status-missing-bg  text-status-missing  hover:bg-status-missing-bg' },
  conflict:      { label: '冲突',   className: 'border-status-conflict-border bg-status-conflict-bg text-status-conflict hover:bg-status-conflict-bg' },
  ocr_uncertain: { label: '待核实', className: 'border-status-review-border   bg-status-review-bg   text-status-review   hover:bg-status-review-bg' },
};

// 按状态着色的行背景（斑马纹叠加状态色）
const ROW_BG: Record<FieldStatus, { even: string; odd: string }> = {
  normal:        { even: 'bg-white',         odd: 'bg-muted/30' },
  missing:       { even: 'bg-status-missing-bg/70',  odd: 'bg-status-missing-bg' },
  conflict:      { even: 'bg-status-conflict-bg/70', odd: 'bg-status-conflict-bg' },
  ocr_uncertain: { even: 'bg-status-review-bg/70',   odd: 'bg-status-review-bg' },
};

interface FieldRowProps {
  field: ReviewField;
  onSave: (key: string, value: string) => void;
  isEven: boolean;
  /** 律师觉得该字段有问题时点「反馈」；不传则不显示按钮 */
  onFeedback?: (field: ReviewField) => void;
}

export function FieldRow({ field, onSave, isEven, onFeedback }: FieldRowProps) {
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
        {/* 字段名列定宽，长名称（如「引用的付款节点（写入起诉状）」）换行，不压到值列 */}
        <span className="block break-words text-sm font-medium leading-snug text-foreground/80">
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
            className="w-full resize-none rounded-md border border-primary bg-white px-2.5 py-1.5 text-sm tabular-nums shadow-sm outline-none ring-2 ring-primary/25"
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
                'flex-1 break-words text-sm tabular-nums',
                !displayValue && 'text-status-missing/70',
                isEdited && 'font-medium text-primary',
              )}
            >
              {displayValue ?? '【待补充】'}
            </span>
            <Pencil className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground/0 transition-all group-hover/val:text-primary/40" />
          </button>
        )}
      </td>

      {/* 来源（hover 显示完整文本）；页码为后端逐页锚定验证所得 */}
      <td className="px-3 py-3 align-top">
        {field.src ? (
          <div className="flex flex-col gap-1">
            <Tooltip>
              <TooltipTrigger
                render={<span className="line-clamp-2 cursor-default text-xs text-muted-foreground" />}
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
            {field.page != null && (
              <Badge
                variant="outline"
                className="w-fit border-sky-200 bg-sky-50 px-1.5 py-0 text-xs font-normal text-sky-700"
              >
                第 {field.page} 页
              </Badge>
            )}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground/40">—</span>
        )}
      </td>

      {/* Badge */}
      <td className="py-3 pr-4 align-top">
        <Badge variant="outline" className={cn('text-xs font-semibold px-1.5 py-0.5', cfg.className)}>
          {cfg.label}
        </Badge>
        {/* 常驻但低调：律师不熟悉“悬停才出现”的按钮，所以始终可见，悬停时加深 */}
        {onFeedback && (
          <button
            type="button"
            onClick={() => onFeedback(field)}
            title={`反馈「${field.label}」的问题`}
            aria-label={`反馈「${field.label}」的问题`}
            className="mt-1.5 flex items-center gap-0.5 text-xs text-muted-foreground/60 transition-colors hover:text-primary group-hover:text-muted-foreground focus-visible:text-primary"
          >
            <MessageSquareWarning className="h-3 w-3" />
            反馈
          </button>
        )}
      </td>
    </tr>
  );
}
