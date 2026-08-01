// 三个子步骤的进度展示：pending → active（spinner）→ done（绿勾）→ error（红叉 + 摘要）
import { cn } from '@/lib/utils';
import { CheckCircle2, XCircle, Loader2, Circle } from 'lucide-react';
import { Button } from '@/components/ui/button';

export type SubStepStatus = 'pending' | 'active' | 'done' | 'error';

export interface SubStep {
  label: string;
  status: SubStepStatus;
  summary?: string;   // 完成时显示的摘要文字
  error?: string;     // 失败时的错误信息
}

interface StepProgressProps {
  steps: SubStep[];
  onRetry?: () => void;
}

function StatusIcon({ status }: { status: SubStepStatus }) {
  if (status === 'done')    return <CheckCircle2 className="h-5 w-5 text-emerald-500" />;
  if (status === 'error')   return <XCircle className="h-5 w-5 text-destructive" />;
  if (status === 'active')  return <Loader2 className="h-5 w-5 animate-spin text-primary" />;
  return <Circle className="h-5 w-5 text-border" />;
}

export function StepProgress({ steps, onRetry }: StepProgressProps) {
  return (
    <div className="flex flex-col gap-0">
      {steps.map((step, idx) => {
        const isLast = idx === steps.length - 1;
        return (
          <div key={step.label} className="flex gap-5">
            {/* 左侧：图标 + 连接线 */}
            <div className="flex flex-col items-center">
              <div className="flex h-5 w-5 shrink-0 items-center justify-center">
                <StatusIcon status={step.status} />
              </div>
              {!isLast && (
                <div
                  className={cn(
                    'mt-2 w-px flex-1 rounded-full transition-colors duration-700',
                    step.status === 'done' ? 'bg-emerald-400' : 'bg-border',
                  )}
                  style={{ minHeight: '2.5rem' }}
                />
              )}
            </div>

            {/* 右侧：标签 + 摘要/错误 */}
            <div className={cn('pb-7', isLast && 'pb-0')}>
              <p
                className={cn(
                  'text-sm font-semibold leading-5',
                  step.status === 'active'  && 'text-primary',
                  step.status === 'done'    && 'text-foreground',
                  step.status === 'error'   && 'text-destructive',
                  step.status === 'pending' && 'text-muted-foreground/50',
                )}
              >
                {step.label}
                {step.status === 'active' && (
                  <span className="ml-2 text-xs font-normal text-muted-foreground">处理中…</span>
                )}
              </p>

              {step.status === 'done' && step.summary && (
                <p className="mt-0.5 text-xs text-emerald-600">{step.summary}</p>
              )}

              {step.status === 'error' && (
                <div className="mt-2 flex flex-col gap-2.5">
                  {step.error && (
                    <p className="rounded-md border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive">
                      {step.error}
                    </p>
                  )}
                  {onRetry && (
                    <Button size="sm" variant="outline" onClick={onRetry} className="w-fit border-primary/30 text-primary hover:bg-primary/5">
                      重新尝试
                    </Button>
                  )}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
