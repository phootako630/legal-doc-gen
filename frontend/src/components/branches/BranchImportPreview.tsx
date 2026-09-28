// 上传新表后的变更预览：列出错误（阻止保存）、提醒和相对当前版本的改动，管理员确认后才生效
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import type { BranchPreview } from '@/lib/types';
import { Check, Loader2, TriangleAlert, X } from 'lucide-react';

interface BranchImportPreviewProps {
  preview: BranchPreview;
  saving: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const KIND_LABEL = { added: '新增', removed: '删除', changed: '变更' } as const;
const KIND_STYLE = {
  added: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  removed: 'bg-red-50 text-red-700 border-red-200',
  changed: 'bg-amber-50 text-amber-800 border-amber-200',
} as const;

export function BranchImportPreview({
  preview,
  saving,
  onConfirm,
  onCancel,
}: BranchImportPreviewProps) {
  const blocked = preview.errors.length > 0;
  // 首次上传时每一行都是「新增」，逐行列出没有信息量，只给一句总结
  const firstUpload =
    preview.diff.length === preview.entries.length && preview.diff.every((d) => d.kind === 'added');
  const summary = firstUpload
    ? ' 首次上传'
    : preview.diff.length === 0
      ? ' 与当前版本相同'
      : ` ${preview.diff.length} 处变更`;

  return (
    <div className="space-y-3 rounded-lg border border-border bg-muted/30 p-4">
      <p className="text-sm font-medium">
        《{preview.source_file}》共 {preview.entries.length} 家 ·{summary}
      </p>

      {blocked && (
        <Alert variant="destructive" className="border-red-300 bg-red-50 text-red-900">
          <TriangleAlert className="h-4 w-4 text-red-600" />
          <AlertDescription>
            <span className="font-semibold">以下问题需先在 Excel 中修正，才能保存：</span>
            <ul className="mt-1 list-disc pl-5">
              {preview.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      {preview.warnings.length > 0 && (
        <Alert className="border-amber-200 bg-amber-50">
          <TriangleAlert className="h-4 w-4 text-amber-600" />
          <AlertDescription className="text-amber-900">
            <span className="font-semibold">请核对（不影响保存）：</span>
            <ul className="mt-1 list-disc pl-5">
              {preview.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      {!firstUpload && preview.diff.length > 0 && (
        <ul className="divide-y divide-border rounded-md border border-border bg-white text-sm">
          {preview.diff.map((d) => (
            <li key={`${d.kind}-${d.name}`} className="px-3 py-2">
              <span className={`mr-2 rounded border px-1.5 py-0.5 text-xs ${KIND_STYLE[d.kind]}`}>
                {KIND_LABEL[d.kind]}
              </span>
              <span className="font-medium">{d.name}</span>
              {d.changes.map((c) => (
                <p key={c.field} className="mt-1 pl-12 text-muted-foreground">
                  {c.label}：<span className="line-through">{c.old || '（空）'}</span>
                  {' → '}
                  <span className="font-medium text-foreground">{c.new || '（空）'}</span>
                </p>
              ))}
            </li>
          ))}
        </ul>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel} disabled={saving} className="gap-1.5">
          <X className="h-4 w-4" />
          取消
        </Button>
        <Button onClick={onConfirm} disabled={saving || blocked} className="gap-1.5">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          确认更新
        </Button>
      </div>
    </div>
  );
}
