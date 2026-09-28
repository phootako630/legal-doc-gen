// 原告信息表历史版本：管理员可把某个旧版本恢复为当前表（传错文件时回退）
import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { fetchBranchHistory, formatBranchDate } from '@/lib/branch-api';
import type { BranchHistoryItem } from '@/lib/types';
import { History } from 'lucide-react';

interface BranchHistoryProps {
  refreshKey: string | null; // 当前版本更新时间变化 → 重新拉取历史
  busy: boolean;
  onRestore: (item: BranchHistoryItem) => void;
}

export function BranchHistory({ refreshKey, busy, onRestore }: BranchHistoryProps) {
  const [items, setItems] = useState<BranchHistoryItem[]>([]);

  useEffect(() => {
    fetchBranchHistory()
      .then(setItems)
      .catch(() => setItems([]));
  }, [refreshKey]);

  if (items.length === 0) return null;

  return (
    <div className="space-y-2">
      <p className="flex items-center gap-1.5 text-sm font-medium">
        <History className="h-4 w-4" />
        历史版本
      </p>
      <ul className="divide-y divide-border rounded-md border border-border text-sm">
        {items.slice(0, 10).map((h) => (
          <li key={h.id} className="flex items-center justify-between gap-3 px-3 py-2">
            <span className="text-muted-foreground">
              {formatBranchDate(h.updated_at)} · {h.source_file ?? '未知文件'} · {h.count} 家
            </span>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => onRestore(h)}>
              恢复此版本
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
