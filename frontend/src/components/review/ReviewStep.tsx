// 第三步：审核确认——字段表格 + 校验报告，确认后调用 /api/generate
import { useRef, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Loader2, TriangleAlert, ArrowLeft, ArrowRight } from 'lucide-react';
import { FieldTable, type FieldTableHandle } from './FieldTable';
import { ValidationReport } from './ValidationReport';
import { HighlightList } from './HighlightList';
import { generateComplaint } from '@/lib/api';
import { isAllFieldsMissing } from '@/lib/buildReviewFields';
import type { ExtractResponse } from '@/lib/types';

interface ReviewStepProps {
  extractResult: ExtractResponse;
  onBack: () => void;
  onDone: (complaintText: string) => void;
}

export function ReviewStep({ extractResult, onBack, onDone }: ReviewStepProps) {
  const tableRef = useRef<FieldTableHandle>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const allMissing = isAllFieldsMissing(extractResult.extracted_fields);

  const handleGenerate = async () => {
    const editedFields = tableRef.current?.getEditedFields() ?? extractResult.extracted_fields;
    setLoading(true);
    setError(null);
    try {
      const result = await generateComplaint(editedFields);
      onDone(result.complaint_text);
    } catch (e) {
      setError(e instanceof Error ? e.message : '生成失败，请重试');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* 全字段缺失警告 */}
      {allMissing && (
        <Alert variant="destructive" className="border-red-300 bg-red-50 text-red-900">
          <TriangleAlert className="h-4 w-4 text-red-600" />
          <AlertDescription>
            无法从上传的文件中识别有效信息——所有字段均为缺失状态。
            请检查文件内容是否完整，或返回重新上传。
          </AlertDescription>
        </Alert>
      )}

      {/* 主体：左右分栏 */}
      <div className="grid gap-4 md:grid-cols-[1fr_320px]">
        {/* 左侧：字段审核表格 */}
        <Card className="shadow-sm">
          <CardHeader className="border-b border-border pb-4">
            <CardTitle className="text-base">字段审核</CardTitle>
            <CardDescription>点击任意值可直接编辑，修改后显示蓝色标记</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <FieldTable
              ref={tableRef}
              extractedFields={extractResult.extracted_fields}
            />
          </CardContent>
        </Card>

        {/* 右侧：校验报告 + 高亮列表 */}
        <div className="md:sticky md:top-[112px] h-fit">
          <Card className="shadow-sm">
            <CardContent className="p-4">
              <Tabs defaultValue="report">
                <TabsList className="w-full">
                  <TabsTrigger value="report" className="flex-1 text-xs">校验报告</TabsTrigger>
                  <TabsTrigger value="highlight" className="flex-1 text-xs">高亮列表</TabsTrigger>
                </TabsList>
                <TabsContent value="report" className="mt-3 max-h-[65vh] overflow-y-auto">
                  <ValidationReport report={extractResult.validation_report} />
                </TabsContent>
                <TabsContent value="highlight" className="mt-3 max-h-[65vh] overflow-y-auto">
                  <HighlightList highlightList={extractResult.highlight_list} />
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* 底部操作栏 */}
      <div className="flex items-center justify-between rounded-xl border border-border bg-white px-6 py-4 shadow-sm">
        <Button variant="outline" onClick={onBack} disabled={loading} className="gap-2">
          <ArrowLeft className="h-4 w-4" />
          返回上传
        </Button>

        <div className="flex flex-col items-end gap-1.5">
          {error && (
            <p className="flex items-center gap-1.5 text-xs text-destructive">
              <TriangleAlert className="h-3.5 w-3.5" />
              {error}
            </p>
          )}
          <Button onClick={handleGenerate} disabled={loading} size="lg" className="min-w-44 gap-2 shadow-sm">
            {loading ? (
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
    </div>
  );
}
