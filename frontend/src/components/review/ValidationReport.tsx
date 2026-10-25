// 校验报告：顶部为确定性代码校验结论（权威），下方为 LLM 生成的自然语言说明
import type { ValidationCheck } from '@/lib/types';

interface ValidationReportProps {
  report: string;
  validations?: ValidationCheck[];
}

/** 确定性校验结论小面板：绿=通过，琥珀=通过但需留意，橙=冲突，灰=信息不足未校验 */
function DeterministicChecks({ checks }: { checks: ValidationCheck[] }) {
  if (checks.length === 0) return null;

  return (
    <div className="mb-3 rounded-lg border border-border bg-white p-3 shadow-sm">
      <p className="mb-2 text-xs font-semibold text-muted-foreground">系统自动校验（代码判定）</p>
      <ul className="flex flex-col gap-1.5">
        {checks.map((c) => {
          const color = !c.applicable
            ? 'bg-gray-300'
            : !c.passed
              ? 'bg-status-conflict'
              : c.needs_review
                ? 'bg-status-review'
                : 'bg-status-ok';
          const textColor = c.is_conflict
            ? 'text-status-conflict font-medium'
            : c.needs_review
              ? 'text-status-review'
              : 'text-foreground/80';
          return (
            <li key={c.key} className="flex items-start gap-2 text-sm">
              <span className={`mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full ${color}`} />
              <span className={textColor}>{c.message}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function ValidationReport({ report, validations = [] }: ValidationReportProps) {
  const hasReport = report.trim().length > 0;

  if (!hasReport && validations.length === 0) {
    return <p className="text-sm text-muted-foreground">无校验报告</p>;
  }

  return (
    <div>
      <DeterministicChecks checks={validations} />
      {hasReport && (
        <div className="rounded-lg border border-border bg-white p-4 shadow-sm">
          <p className="whitespace-pre-wrap text-sm text-foreground/85">{report}</p>
        </div>
      )}
    </div>
  );
}
