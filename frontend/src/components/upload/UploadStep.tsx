// 第一步：上传案件材料
import { useState, useCallback, useEffect, useRef } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { FileDropzone } from './FileDropzone';
import { FileList } from './FileList';
import { MaterialReview } from './MaterialReview';
import { UploadProgressPanel } from './UploadProgressPanel';
import { BranchTableNotice } from '@/components/branches/BranchTableNotice';
import { uploadFiles, fetchUploadProgress } from '@/lib/api';
import { newRequestKey } from '@/lib/request-key';
import { materialStatus, withDetectedTypes } from '@/lib/materials';
import type { DocType, UploadResponse, UploadProgress } from '@/lib/types';
import { Globe, Loader2, TriangleAlert, ArrowRight } from 'lucide-react';

interface UploadStepProps {
  onDone: (result: UploadResponse, internetAllowed: boolean) => void;
  onOpenBranches: () => void;
}

export function UploadStep({ onDone, onOpenBranches }: UploadStepProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [internetAllowed, setInternetAllowed] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadResult, setUploadResult] = useState<UploadResponse | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  // 本次上传的进度 id：轮询只读自己这次上传的进度，多人同时上传互不干扰
  const progressIdRef = useRef<string | null>(null);

  // 上传期间每秒轮询后端进度；上传结束（loading 变 false）自动停止并清空
  useEffect(() => {
    if (loading) {
      pollTimer.current = setInterval(async () => {
        const pid = progressIdRef.current;
        if (!pid) return;
        const p = await fetchUploadProgress(pid);
        // 轮询可能在上传请求返回后才响应，只在任务仍活跃时更新，避免残留旧进度
        if (p?.active) setProgress(p);
      }, 1000);
    } else {
      setProgress(null);
    }
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
      pollTimer.current = null;
    };
  }, [loading]);

  const handleNewFiles = useCallback((incoming: File[]) => {
    setFiles((prev) => {
      const existing = new Set(prev.map((f) => `${f.name}|${f.size}`));
      const deduped = incoming.filter((f) => !existing.has(`${f.name}|${f.size}`));
      return [...prev, ...deduped];
    });
    setError(null);
    setUploadResult(null);
  }, []);

  const removeFile = useCallback((idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
    setUploadResult(null);
  }, []);

  const handleSubmit = async () => {
    if (files.length === 0) { setError('请至少上传一个文件'); return; }
    setLoading(true);
    setError(null);
    setUploadResult(null);
    try {
      const progressId = newRequestKey();
      progressIdRef.current = progressId;
      const result = await uploadFiles(files, progressId);
      // 上传后先停在材料确认：律师核对每份文件的类型（认错可改）、看到解析失败的文件，再开始分析
      setUploadResult({ ...result, files: withDetectedTypes(result.files) });
    } catch (e) {
      setError(e instanceof Error ? e.message : '上传失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  const changeType = (idx: number, type: DocType) =>
    setUploadResult((r) =>
      r
        ? {
            ...r,
            files: r.files.map((f, i) => (i === idx ? { ...f, identified_type: type } : f)),
          }
        : r,
    );

  // 按律师确认后的类型重算材料是否齐全，再进入分析
  const status = uploadResult ? materialStatus(uploadResult.files) : null;
  const confirm = () => {
    if (!uploadResult || !status) return;
    onDone(
      {
        ...uploadResult,
        can_proceed: status.canProceed,
        missing_materials: status.missing,
      },
      internetAllowed,
    );
  };

  return (
    <Card className="shadow-sm">
      <CardHeader className="border-b border-border pb-5">
        <CardTitle className="text-base">上传案件材料</CardTitle>
        <CardDescription>
          请上传<strong className="font-medium text-foreground/80">审批表</strong>、
          <strong className="font-medium text-foreground/80">安装合同</strong>及
          <strong className="font-medium text-foreground/80">验收报告</strong>，
          支持 PDF 和 Word 格式
        </CardDescription>
      </CardHeader>

      <CardContent className="flex flex-col gap-5 pt-6">
        <BranchTableNotice onOpen={onOpenBranches} />
        <FileDropzone onFiles={handleNewFiles} />

        {files.length > 0 && <FileList files={files} onRemove={removeFile} />}

        {uploadResult && <MaterialReview files={uploadResult.files} onChangeType={changeType} />}

        {/* 联网查询开关 */}
        <div className="flex items-center justify-between rounded-lg border border-border bg-muted/30 px-4 py-3.5">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Globe className="h-4 w-4" />
            </div>
            <div>
              <p className="text-sm font-medium leading-tight">联网查询原告信息</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                允许 AI 通过企业名称在线补全统一社会信用代码等信息
              </p>
            </div>
          </div>
          <Switch
            checked={internetAllowed}
            onCheckedChange={setInternetAllowed}
            aria-label="联网查询开关"
          />
        </div>

        {loading && <UploadProgressPanel progress={progress} />}

        {/* 错误提示 */}
        {error && (
          <div className="flex items-start gap-2.5 rounded-lg border border-destructive/25 bg-destructive/8 px-4 py-3 text-sm text-destructive">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* 文件处理失败提示（如 OCR 出错）：必须让律师看到具体原因，而不是只看到"材料缺失" */}
        {uploadResult && uploadResult.warnings.length > 0 && (
          <Alert variant="destructive" className="border-red-300 bg-red-50 text-red-900">
            <TriangleAlert className="h-4 w-4 text-red-600" />
            <AlertDescription>
              <span className="font-semibold">以下文件处理失败，内容可能没有读到：</span>
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                {uploadResult.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}

        {/* 提交 */}
        <div className="flex items-center justify-between border-t border-border pt-4">
          <p className="text-xs text-muted-foreground">
            {files.length === 0 ? '尚未选择文件' : `已选择 ${files.length} 个文件`}
          </p>
          <Button
            onClick={uploadResult ? confirm : handleSubmit}
            disabled={loading || files.length === 0}
            className="min-w-32 gap-2"
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                上传中…
              </>
            ) : (
              <>
                {!uploadResult
                  ? '上传并识别'
                  : status?.canProceed
                    ? '确认材料，开始分析'
                    : '材料不全，仍然分析'}
                <ArrowRight className="h-4 w-4" />
              </>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
