// 审核页「原件第 N 页」按钮 + 弹窗（#111）：OCR 来源的字段直接对照扫描件原件，不用另开文件翻页。
// 图片按需取（点开才请求），关闭即释放；点图片在「适应窗口 / 原始大小」之间切换，方便看清小字。
import { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { FileImage, Loader2, TriangleAlert } from 'lucide-react';
import { fetchSourcePage } from '@/lib/api';
import { loadSourcePageUrl, type SourcePageTarget } from '@/lib/source-page';
import { cn } from '@/lib/utils';

interface SourcePageButtonProps {
  runId: string;
  fieldKey: string;
  fieldLabel: string;
  target: SourcePageTarget;
}

type Load = { kind: 'loading' } | { kind: 'ok'; url: string } | { kind: 'error'; message: string };

export function SourcePageButton({ runId, fieldKey, fieldLabel, target }: SourcePageButtonProps) {
  const [open, setOpen] = useState(false);
  const [load, setLoad] = useState<Load>({ kind: 'loading' });
  const [zoomed, setZoomed] = useState(false);

  useEffect(() => {
    if (!open) return;
    const ctrl = new AbortController();
    let url: string | null = null;
    setLoad({ kind: 'loading' });
    setZoomed(false);
    loadSourcePageUrl(
      () => fetchSourcePage(runId, target.file, target.page, fieldKey, ctrl.signal),
      ctrl.signal,
    )
      .then((created) => {
        if (!created) return; // 弹窗已关闭，没建地址
        url = created;
        setLoad({ kind: 'ok', url });
      })
      .catch((e: unknown) => {
        if (ctrl.signal.aborted) return;
        setLoad({ kind: 'error', message: e instanceof Error ? e.message : '原件加载失败' });
      });
    return () => {
      ctrl.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [open, runId, fieldKey, target.file, target.page]);

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setLoad({ kind: 'loading' });
          setOpen(true);
        }}
        title={`查看《${target.file}》第 ${target.page} 页原件`}
        className="flex w-fit items-center gap-1 rounded-md border border-sky-200 bg-sky-50 px-1.5 py-0 text-xs text-sky-700 transition-colors hover:bg-sky-100"
      >
        <FileImage className="h-3 w-3" />
        原件第 {target.page} 页
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[92vh] sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>核对原件：{fieldLabel}</DialogTitle>
            <DialogDescription>
              《{target.file}》第 {target.page} 页。点图片可放大 / 缩小。
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[75vh] overflow-auto rounded-md border bg-muted/30">
            {load.kind === 'loading' && (
              <div className="flex h-64 items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在加载原件…
              </div>
            )}
            {load.kind === 'error' && (
              <div className="flex h-40 items-center justify-center gap-2 px-4 text-sm text-status-missing">
                <TriangleAlert className="h-4 w-4 shrink-0" />
                {load.message}
              </div>
            )}
            {load.kind === 'ok' && (
              <img
                src={load.url}
                alt={`《${target.file}》第 ${target.page} 页原件`}
                onClick={() => setZoomed((z) => !z)}
                className={cn('block', zoomed ? 'max-w-none cursor-zoom-out' : 'w-full cursor-zoom-in')}
              />
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
