// 校验报告：渲染后端返回的 validation_report 文本
interface ValidationReportProps {
  report: string;
}

export function ValidationReport({ report }: ValidationReportProps) {
  if (!report.trim()) {
    return <p className="text-sm text-muted-foreground italic">无校验报告</p>;
  }

  return (
    <div className="rounded-lg border border-border bg-white p-4 shadow-sm">
      <p className="whitespace-pre-wrap text-[13px] leading-7 text-foreground/85">{report}</p>
    </div>
  );
}
