// 上传处理进度：扫描件 OCR 显示真实页数进度条，普通解析显示不确定态
import { Loader2, ScanText } from 'lucide-react';
import { Progress } from '@/components/ui/progress';
import type { UploadProgress } from '@/lib/types';

export function UploadProgressPanel({ progress }: { progress: UploadProgress | null }) {
  return (
    <div className="flex flex-col gap-2.5 rounded-lg border border-border bg-muted/30 px-4 py-3.5">
      <div className="flex items-center gap-2.5 text-sm">
        {progress?.stage === 'OCR识别中' ? (
          <ScanText className="h-4 w-4 shrink-0 text-primary" />
        ) : (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
        )}
        <span className="font-medium">
          {progress === null
            ? '正在上传文件…'
            : progress.stage === 'OCR识别中'
              ? `正在识别扫描件：${progress.filename ?? ''}`
              : `正在解析：${progress.filename ?? ''}`}
        </span>
        {progress !== null && progress.total_files > 1 && (
          <span className="ml-auto text-xs text-muted-foreground tabular-nums">
            第 {progress.file_index}/{progress.total_files} 个文件
          </span>
        )}
      </div>
      {progress?.stage === 'OCR识别中' && progress.total_pages > 0 ? (
        <>
          <Progress value={Math.round((progress.done_pages / progress.total_pages) * 100)} />
          <p className="text-xs text-muted-foreground tabular-nums">
            已识别 {progress.done_pages}/{progress.total_pages} 页 （扫描件逐页识别，页数较多时约需
            1–2 分钟）
          </p>
        </>
      ) : (
        <Progress value={null} />
      )}
    </div>
  );
}
