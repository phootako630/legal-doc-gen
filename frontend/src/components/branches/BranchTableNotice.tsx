// 上传页提醒：原告信息表未上传或长期未更新时提示（负责人可能已变更）；读取失败不打扰
import { useEffect, useState } from 'react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { fetchBranchTable, formatBranchDate } from '@/lib/branch-api';
import type { BranchTable } from '@/lib/types';
import { TriangleAlert } from 'lucide-react';

export function BranchTableNotice({ onOpen }: { onOpen: () => void }) {
  const [table, setTable] = useState<BranchTable | null>(null);

  useEffect(() => {
    fetchBranchTable()
      .then(setTable)
      .catch(() => setTable(null));
  }, []);

  if (!table || !table.stale) return null;
  const empty = table.entries.length === 0;

  return (
    <Alert className="border-amber-200 bg-amber-50">
      <TriangleAlert className="h-4 w-4 text-amber-600" />
      <AlertDescription className="text-amber-900">
        {empty
          ? '尚未上传原告信息表，起诉状中原告的信用代码、负责人、住址将留【待补充】。'
          : `原告信息表上次更新于 ${formatBranchDate(table.updated_at)}，已超过 ${table.stale_days} 天，负责人可能已变更。`}
        <button
          type="button"
          onClick={onOpen}
          className="ml-2 font-medium underline underline-offset-2 hover:text-amber-950"
        >
          查看原告信息表
        </button>
      </AlertDescription>
    </Alert>
  );
}
