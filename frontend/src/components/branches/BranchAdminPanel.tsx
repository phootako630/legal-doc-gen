// 管理员操作区：登录后可上传新版 Excel（先预览变更再确认）、恢复历史版本
import { useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { useAdminToken } from '@/hooks/useAdminToken';
import {
  formatBranchDate,
  previewBranchFile,
  restoreBranchVersion,
  saveBranchTable,
} from '@/lib/branch-api';
import type { BranchHistoryItem, BranchPreview, BranchTable } from '@/lib/types';
import { AdminLogin } from './AdminLogin';
import { BranchImportPreview } from './BranchImportPreview';
import { BranchHistory } from './BranchHistory';
import { Loader2, LogOut, Upload } from 'lucide-react';

interface BranchAdminPanelProps {
  currentVersion: string | null;
  onUpdated: (table: BranchTable, message: string) => void;
}

export function BranchAdminPanel({ currentVersion, onUpdated }: BranchAdminPanelProps) {
  const { token, login, logout } = useAdminToken();
  const inputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<BranchPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!token) return <AdminLogin onLogin={login} />;

  /** 统一处理：口令失效（被改）时退出登录，其余错误显示中文信息 */
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      const msg = err instanceof Error ? err.message : '操作失败';
      if (msg.includes('口令')) logout();
      setError(msg);
    } finally {
      setBusy(false);
    }
  };

  const onFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (file) void run(async () => setPreview(await previewBranchFile(file, token)));
  };

  const confirm = () =>
    run(async () => {
      if (!preview) return;
      const table = await saveBranchTable(preview.entries, preview.source_file, token);
      setPreview(null);
      onUpdated(table, `已更新为《${preview.source_file}》，共 ${table.entries.length} 家`);
    });

  const restore = (item: BranchHistoryItem) => {
    const label = `${formatBranchDate(item.updated_at)} 的版本（${item.source_file ?? '未知文件'}）`;
    if (!window.confirm(`确定把原告信息表恢复为 ${label} 吗？当前版本会存入历史。`)) return;
    void run(async () => {
      const table = await restoreBranchVersion(item.id, token);
      onUpdated(table, `已恢复为 ${label}`);
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => inputRef.current?.click()} disabled={busy} className="gap-2">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
          上传新版本（Excel）
        </Button>
        <Button
          variant="ghost"
          onClick={logout}
          disabled={busy}
          className="gap-1.5 text-muted-foreground"
        >
          <LogOut className="h-4 w-4" />
          退出管理员
        </Button>
        <input
          ref={inputRef}
          type="file"
          accept=".xls,.xlsx"
          aria-label="选择原告信息表"
          className="hidden"
          onChange={onFile}
        />
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {preview && (
        <BranchImportPreview
          preview={preview}
          saving={busy}
          onConfirm={() => void confirm()}
          onCancel={() => setPreview(null)}
        />
      )}
      <BranchHistory refreshKey={currentVersion} busy={busy} onRestore={restore} />
    </div>
  );
}
