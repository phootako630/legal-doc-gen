// 「原告信息表」页面：所有人可查看、下载；管理员可上传新版本或恢复历史版本
import { useEffect, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button, buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { BRANCH_EXPORT_URL, fetchBranchTable, formatBranchDate } from '@/lib/branch-api';
import type { BranchTable } from '@/lib/types';
import { BranchAdminPanel } from './BranchAdminPanel';
import { BranchList } from './BranchList';
import { ArrowLeft, CircleCheck, Download, Loader2, TriangleAlert } from 'lucide-react';

export function BranchTablePage({ onBack }: { onBack: () => void }) {
  const [table, setTable] = useState<BranchTable | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    fetchBranchTable()
      .then(setTable)
      .catch((e: Error) => setError(e.message));
  }, []);

  const hasTable = !!table && table.entries.length > 0;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>原告信息表</CardTitle>
          <CardDescription>
            生成起诉状时，原告的统一社会信用代码、法定代表人 / 负责人、住所地都从这张表自动填入。
            全所共用一份，由管理员维护。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {error && <p className="text-sm text-destructive">{error}</p>}
          {!table && !error && <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />}

          {table && (
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-muted-foreground">
                {hasTable
                  ? `当前版本：${formatBranchDate(table.updated_at)} 更新 · 来源《${table.source_file ?? '未知文件'}》 · 共 ${table.entries.length} 家`
                  : '尚未上传原告信息表，起诉状中原告的这几项会留【待补充】'}
              </p>
              {hasTable && (
                <a
                  href={BRANCH_EXPORT_URL}
                  download
                  className={cn(buttonVariants({ variant: 'outline' }), 'gap-2')}
                >
                  <Download className="h-4 w-4" />
                  下载当前 Excel
                </a>
              )}
            </div>
          )}

          {hasTable && table.stale && (
            <Alert className="border-amber-200 bg-amber-50">
              <TriangleAlert className="h-4 w-4 text-amber-600" />
              <AlertDescription className="text-amber-900">
                此表已超过 {table.stale_days} 天未更新，负责人可能已变更，请管理员核对后上传新版本。
              </AlertDescription>
            </Alert>
          )}

          {notice && (
            <p className="flex items-center gap-1.5 text-sm text-emerald-700">
              <CircleCheck className="h-4 w-4" />
              {notice}
            </p>
          )}

          <div className="border-t border-border pt-4">
            <BranchAdminPanel
              currentVersion={table?.updated_at ?? null}
              onUpdated={(t, msg) => {
                setTable(t);
                setNotice(msg);
              }}
            />
          </div>
        </CardContent>
      </Card>

      {hasTable && (
        <Card>
          <CardContent className="pt-2">
            <BranchList entries={table.entries} />
          </CardContent>
        </Card>
      )}

      <Button variant="outline" onClick={onBack} className="gap-2">
        <ArrowLeft className="h-4 w-4" />
        返回起诉状生成
      </Button>
    </div>
  );
}
