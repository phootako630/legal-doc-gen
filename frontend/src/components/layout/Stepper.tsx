// 顶部步骤条：显示 4 步流程的当前进度
import { cn } from '@/lib/utils';
import type { FlowStep } from '@/lib/types';

const STEPS: { key: FlowStep; label: string; desc: string }[] = [
  { key: 'upload',     label: '上传材料', desc: '案件文件' },
  { key: 'processing', label: 'AI 处理',  desc: '自动抽取' },
  { key: 'review',     label: '审核确认', desc: '核对字段' },
  { key: 'preview',    label: '预览下载', desc: '生成文书' },
];

const STEP_INDEX: Record<FlowStep, number> = {
  upload: 0, processing: 1, review: 2, preview: 3,
};

interface StepperProps {
  currentStep: FlowStep;
}

export function Stepper({ currentStep }: StepperProps) {
  const currentIdx = STEP_INDEX[currentStep];

  return (
    <nav aria-label="流程步骤" className="flex items-start justify-center gap-0">
      {STEPS.map((step, idx) => {
        const isDone   = idx < currentIdx;
        const isActive = idx === currentIdx;

        return (
          <div key={step.key} className="flex items-start">
            {/* 步骤：圆圈 + 标签 */}
            <div className="flex flex-col items-center gap-2 px-1">
              <div
                className={cn(
                  'flex h-9 w-9 items-center justify-center rounded-full text-sm font-bold transition-all duration-300',
                  isDone   && 'bg-primary text-primary-foreground shadow-sm',
                  isActive && 'bg-primary text-primary-foreground shadow-md ring-4 ring-primary/15',
                  !isDone && !isActive && 'border-2 border-border bg-white text-muted-foreground',
                )}
              >
                {isDone ? (
                  <svg className="h-4.5 w-4.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} aria-hidden>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                ) : (
                  <span>{idx + 1}</span>
                )}
              </div>
              <div className="flex flex-col items-center">
                <span
                  className={cn(
                    'text-xs font-semibold whitespace-nowrap transition-colors',
                    isActive  ? 'text-primary'
                    : isDone  ? 'text-primary/60'
                    : 'text-muted-foreground/60',
                  )}
                >
                  {step.label}
                </span>
                <span className="text-[10px] text-muted-foreground/50 whitespace-nowrap">
                  {step.desc}
                </span>
              </div>
            </div>

            {/* 连接线 */}
            {idx < STEPS.length - 1 && (
              <div className="mx-2 mt-4 flex-1">
                <div
                  className={cn(
                    'h-px w-20 transition-colors duration-500',
                    idx < currentIdx ? 'bg-primary' : 'bg-border',
                  )}
                />
              </div>
            )}
          </div>
        );
      })}
    </nav>
  );
}
