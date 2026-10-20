// 上传后的材料确认：逐个列出系统识别的文件类型，认错了律师可下拉改；认不出的标琥珀色提醒
import { CircleHelp, ScanText, TriangleAlert } from 'lucide-react';
import { DOC_TYPE_OPTIONS, docTypeLabel, materialStatus } from '@/lib/materials';
import type { DocType, ParsedFile } from '@/lib/types';

interface MaterialReviewProps {
  files: ParsedFile[];
  onChangeType: (index: number, type: DocType) => void;
}

export function MaterialReview({ files, onChangeType }: MaterialReviewProps) {
  const status = materialStatus(files);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-muted/30 px-4 py-3.5">
      <div>
        <p className="text-sm font-medium">请确认每份材料的类型</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          系统已自动识别，有误请在下拉框中改正，后续按您选的类型读取材料。
        </p>
      </div>

      <ul className="flex flex-col gap-2">
        {files.map((f, idx) => {
          const unknown = f.identified_type === '未知';
          const detected = f.detected_type;
          const changed = detected !== undefined && detected !== f.identified_type;
          return (
            <li
              key={`${f.filename}-${idx}`}
              className={`flex flex-col gap-1.5 rounded-md border px-3 py-2 sm:flex-row sm:items-center sm:gap-3 ${
                unknown ? 'border-amber-300 bg-amber-50' : 'border-border bg-white'
              }`}
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground" title={f.filename}>
                  {f.filename}
                </p>
                <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                  {f.is_scanned && (
                    <span className="inline-flex items-center gap-1">
                      <ScanText className="h-3 w-3" />
                      扫描件
                    </span>
                  )}
                  {unknown && (
                    <span className="inline-flex items-center gap-1 text-amber-700">
                      <CircleHelp className="h-3 w-3" />
                      未能识别材料类型，请选择
                    </span>
                  )}
                  {changed && detected && (
                    <span className="text-primary">
                      已改（系统识别为「{docTypeLabel(detected)}」）
                    </span>
                  )}
                </p>
              </div>
              <select
                aria-label={`${f.filename} 的材料类型`}
                value={f.identified_type}
                onChange={(e) => onChangeType(idx, e.target.value as DocType)}
                className="h-8 w-full rounded-md border border-input bg-white px-2 text-sm sm:w-auto focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                {DOC_TYPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value} title={o.hint}>
                    {o.label}
                  </option>
                ))}
              </select>
            </li>
          );
        })}
      </ul>

      {status.missing.length > 0 && (
        <p
          className={`flex items-start gap-1.5 text-xs ${
            status.canProceed ? 'text-muted-foreground' : 'text-orange-700'
          }`}
        >
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            按当前选择，缺少{status.missing.join('、')}
            {status.canProceed ? '（验收报告缺失不影响开始分析）' : '，建议补充后再分析'}。
          </span>
        </p>
      )}
    </div>
  );
}
