// 上传页顶部：本标签页有未完成的案件（刷新过页面）时，询问是否继续
import { useState } from 'react';
import { History, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { fetchCase } from '@/lib/api';
import type { SavedCaseSession } from '@/lib/case-session';
import type { CaseState } from '@/lib/types';

interface ResumeCaseBannerProps {
  saved: SavedCaseSession;
  onResume: (caseState: CaseState, edits: Record<string, string>) => void;
  onDiscard: () => void;
}

function minutesAgo(ts: number): string {
  const min = Math.max(0, Math.round((Date.now() - ts) / 60000));
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  return `${Math.round(min / 60)} 小时前`;
}

export function ResumeCaseBanner({ saved, onResume, onDiscard }: ResumeCaseBannerProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editCount = Object.keys(saved.edits).length;

  const handleResume = async () => {
    setLoading(true);
    setError(null);
    try {
      onResume(await fetchCase(saved.runId), saved.edits);
    } catch (e) {
      setError(e instanceof Error ? e.message : '取回上次的案件失败');
      setLoading(false);
    }
  };

  return (
    <div className="mb-6 flex flex-col gap-3 rounded-lg border border-primary/30 bg-primary/5 px-4 py-3 sm:flex-row sm:items-center">
      <History className="hidden h-5 w-5 shrink-0 text-primary sm:block" />
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-medium text-foreground">继续上次的案件？</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {minutesAgo(saved.savedAt)}在审核页的案件尚未完成
          {editCount > 0 ? `，已修改 ${editCount} 个字段` : ''}。继续将回到审核页。
        </p>
        {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
      </div>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" onClick={onDiscard} disabled={loading}>
          放弃
        </Button>
        <Button size="sm" onClick={handleResume} disabled={loading} className="gap-1.5">
          {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          继续
        </Button>
      </div>
    </div>
  );
}
