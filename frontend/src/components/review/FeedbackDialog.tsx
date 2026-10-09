// 审核页反馈弹窗：律师对某个字段（或整体）选一个问题分类、可选写几句说明，提交给后端
//
// 分类是固定选项：后端只把「字段 + 分类」写进运行日志用于统计；说明文字可能含案件内容，
// 随案件数据保存在服务器、到期自动删除。
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { CheckCircle2, Loader2, TriangleAlert } from 'lucide-react';
import { submitFeedback } from '@/lib/api';
import { cn } from '@/lib/utils';
import type { FeedbackCategory } from '@/lib/types';

/** 反馈针对的字段；null 表示整体反馈 */
export interface FeedbackTarget {
  key: string;
  label: string;
  value: string | null;
}

interface FeedbackDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  runId: string;
  target: FeedbackTarget | null;
}

const CATEGORIES: { value: FeedbackCategory; label: string }[] = [
  { value: 'wrong_value', label: '值不对' },
  { value: 'missing', label: '缺失 / 没抽到' },
  { value: 'wrong_source', label: '来源或页码不对' },
  { value: 'other', label: '其他' },
];

export function FeedbackDialog({ open, onOpenChange, runId, target }: FeedbackDialogProps) {
  const [category, setCategory] = useState<FeedbackCategory | null>(null);
  const [comment, setComment] = useState('');
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 每次关闭即结束一个「弹窗会话」：旧请求晚到的结果、旧的自动关闭定时器都不能动下一次打开的草稿
  const sessionRef = useRef(0);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearCloseTimer = () => {
    if (closeTimerRef.current !== null) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };
  useEffect(() => clearCloseTimer, []);

  // 关闭时清空，下次打开是新的一条反馈
  const handleOpenChange = (next: boolean) => {
    if (!next) {
      sessionRef.current += 1;
      clearCloseTimer();
      setCategory(null);
      setComment('');
      setSending(false);
      setDone(false);
      setError(null);
    }
    onOpenChange(next);
  };

  const handleSubmit = async () => {
    if (!category) return;
    const session = sessionRef.current;
    setSending(true);
    setError(null);
    try {
      await submitFeedback({
        run_id: runId,
        field_key: target?.key ?? null,
        category,
        comment: comment.trim() || null,
      });
      if (session !== sessionRef.current) return;
      setDone(true);
      clearCloseTimer();
      closeTimerRef.current = setTimeout(() => {
        if (session === sessionRef.current) handleOpenChange(false);
      }, 1200);
    } catch (e) {
      if (session !== sessionRef.current) return;
      setError(e instanceof Error ? e.message : '反馈提交失败，请重试');
    } finally {
      if (session === sessionRef.current) setSending(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{target ? `反馈：${target.label}` : '反馈问题'}</DialogTitle>
          <DialogDescription>
            {target
              ? `当前显示：${target.value ?? '【待补充】'}`
              : '对本案抽取结果的整体问题，或表格里没有的字段'}
          </DialogDescription>
        </DialogHeader>

        {done ? (
          <p className="flex items-center gap-2 py-4 text-sm text-emerald-700">
            <CheckCircle2 className="h-4 w-4" />
            已收到，谢谢！
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="问题类型">
              {CATEGORIES.map((c) => (
                <button
                  key={c.value}
                  type="button"
                  role="radio"
                  aria-checked={category === c.value}
                  onClick={() => setCategory(c.value)}
                  className={cn(
                    'rounded-md border px-3 py-1.5 text-xs transition-colors',
                    category === c.value
                      ? 'border-primary bg-primary text-primary-foreground'
                      : 'border-border bg-white text-foreground hover:border-primary/50',
                  )}
                >
                  {c.label}
                </button>
              ))}
            </div>
            <Textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              maxLength={1000}
              rows={3}
              placeholder="补充说明（选填）：比如正确的值应该是什么、在哪一页"
              aria-label="补充说明"
            />
            <p className="text-[11px] text-muted-foreground">
              说明只保存在服务器上，随案件数据一起到期自动删除。
            </p>
            {error && (
              <p className="flex items-center gap-1.5 text-xs text-destructive">
                <TriangleAlert className="h-3.5 w-3.5" />
                {error}
              </p>
            )}
          </div>
        )}

        {!done && (
          <DialogFooter>
            <Button variant="outline" onClick={() => handleOpenChange(false)} disabled={sending}>
              取消
            </Button>
            <Button onClick={handleSubmit} disabled={!category || sending} className="gap-2">
              {sending && <Loader2 className="h-4 w-4 animate-spin" />}
              提交反馈
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
