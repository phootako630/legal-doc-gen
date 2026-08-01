// 第一步：上传案件材料
import { useState, useCallback } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { FileDropzone } from './FileDropzone';
import { FileList } from './FileList';
import { uploadFiles } from '@/lib/api';
import type { UploadResponse } from '@/lib/types';
import { Globe, Loader2, TriangleAlert, ArrowRight } from 'lucide-react';

interface UploadStepProps {
  onDone: (result: UploadResponse, internetAllowed: boolean) => void;
}

export function UploadStep({ onDone }: UploadStepProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [internetAllowed, setInternetAllowed] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadResult, setUploadResult] = useState<UploadResponse | null>(null);

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
      const result = await uploadFiles(files);
      if (result.can_proceed) {
        onDone(result, internetAllowed);
      } else {
        setUploadResult(result);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '上传失败，请重试');
    } finally {
      setLoading(false);
    }
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
        <FileDropzone onFiles={handleNewFiles} />

        {files.length > 0 && <FileList files={files} onRemove={removeFile} />}

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

        {/* 错误提示 */}
        {error && (
          <div className="flex items-start gap-2.5 rounded-lg border border-destructive/25 bg-destructive/8 px-4 py-3 text-sm text-destructive">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* 材料缺失警告（上传成功但材料不齐） */}
        {uploadResult && !uploadResult.can_proceed && (
          <Alert className="border-orange-200 bg-orange-50">
            <TriangleAlert className="h-4 w-4 text-orange-500" />
            <AlertDescription className="text-orange-800">
              <span className="font-semibold">材料不完整：</span>
              缺少{uploadResult.missing_materials.filter((m) => ['审批表', '合同'].includes(m)).join('、')}，
              建议补充后再提交。
              <button
                type="button"
                className="ml-2 font-medium underline underline-offset-2 hover:text-orange-900"
                onClick={() => onDone(uploadResult, internetAllowed)}
              >
                仍然继续
              </button>
            </AlertDescription>
          </Alert>
        )}

        {/* 提交 */}
        <div className="flex items-center justify-between border-t border-border pt-4">
          <p className="text-xs text-muted-foreground">
            {files.length === 0
              ? '尚未选择文件'
              : `已选择 ${files.length} 个文件`}
          </p>
          <Button
            onClick={handleSubmit}
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
                开始分析
                <ArrowRight className="h-4 w-4" />
              </>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
