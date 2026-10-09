// 审核页底部操作栏：返回上传 / 整体反馈 / 确认并生成起诉状（含生成中与出错提示）
import { Button } from '@/components/ui/button';
import { ArrowLeft, ArrowRight, Loader2, MessageSquareWarning, TriangleAlert } from 'lucide-react';

interface ReviewActionBarProps {
  generating: boolean;
  error: string | null;
  onBack: () => void;
  onFeedback: () => void;
  onGenerate: () => void;
}

export function ReviewActionBar({
  generating,
  error,
  onBack,
  onFeedback,
  onGenerate,
}: ReviewActionBarProps) {
  return (
    <div className="flex items-center justify-between rounded-xl border border-border bg-white px-6 py-4 shadow-sm">
      <div className="flex items-center gap-2">
        <Button variant="outline" onClick={onBack} disabled={generating} className="gap-2">
          <ArrowLeft className="h-4 w-4" />
          返回上传
        </Button>
        <Button
          variant="ghost"
          onClick={onFeedback}
          className="gap-1.5 text-muted-foreground hover:text-foreground"
        >
          <MessageSquareWarning className="h-4 w-4" />
          反馈问题
        </Button>
      </div>

      <div className="flex flex-col items-end gap-1.5">
        {error && (
          <p className="flex items-center gap-1.5 text-xs text-destructive">
            <TriangleAlert className="h-3.5 w-3.5" />
            {error}
          </p>
        )}
        {generating && <p className="text-xs text-muted-foreground">正在按模板生成起诉状…</p>}
        <Button onClick={onGenerate} disabled={generating} size="lg" className="min-w-44 gap-2 shadow-sm">
          {generating ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              生成中…
            </>
          ) : (
            <>
              确认并生成起诉状
              <ArrowRight className="h-4 w-4" />
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
