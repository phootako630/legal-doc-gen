// 第三步：审核确认——先消解 agent 断点（pending），无待决后进入字段审核并生成起诉状。
// 冲突/确认断点经 /api/resume 往返（可多轮）；材料缺失断点为终止态，引导律师返回上传。
import { useRef, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Loader2, TriangleAlert, ArrowLeft, ArrowRight } from 'lucide-react';
import { FieldTable, type FieldTableHandle } from './FieldTable';
import { ValidationReport } from './ValidationReport';
import { HighlightList } from './HighlightList';
import { PendingResolver } from './PendingResolver';
import { generateComplaint, resumeCase } from '@/lib/api';
import { isAllFieldsMissing } from '@/lib/buildReviewFields';
import type { CaseState } from '@/lib/types';

interface ReviewStepProps {
  caseState: CaseState;
  onBack: () => void;
  onDone: (complaintText: string) => void;
}

/** 起绪度徽标：按就绪度分档着色，帮律师一眼判断关键字段是否齐备 */
function ReadinessBadge({ readiness }: { readiness: number }) {
  const tone =
    readiness >= 80
      ? 'border-green-300 bg-green-50 text-green-700'
      : readiness >= 50
        ? 'border-amber-300 bg-amber-50 text-amber-700'
        : 'border-red-300 bg-red-50 text-red-700';
  return (
    <Badge variant="outline" className={`font-normal ${tone}`}>
      起诉状就绪度 {readiness}%
    </Badge>
  );
}

export function ReviewStep({ caseState, onBack, onDone }: ReviewStepProps) {
  // 本地持有会话状态：resume 往返会替换它，字段表格/校验报告均从这里渲染
  const [current, setCurrent] = useState<CaseState>(caseState);
  const tableRef = useRef<FieldTableHandle>(null);
  const [genLoading, setGenLoading] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [resumeLoading, setResumeLoading] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);

  const pending = current.pending;

  const handleResume = async (decisions: Record<string, string>) => {
    setResumeLoading(true);
    setResumeError(null);
    try {
      // decisions 里空串按 null 提交（表示该字段仍缺失，交由后端校验重判）
      const normalized: Record<string, string | null> = {};
      for (const [k, v] of Object.entries(decisions)) normalized[k] = v === '' ? null : v;
      const next = await resumeCase(current.run_id, normalized);
      setCurrent(next); // 可能仍带 pending，则 PendingResolver 继续下一轮
    } catch (e) {
      setResumeError(e instanceof Error ? e.message : '提交失败，请重试');
    } finally {
      setResumeLoading(false);
    }
  };

  const handleGenerate = async () => {
    const editedFields = tableRef.current?.getEditedFields() ?? current.extracted_fields;
    setGenLoading(true);
    setGenError(null);
    try {
      const result = await generateComplaint(editedFields);
      onDone(result.complaint_text);
    } catch (e) {
      setGenError(e instanceof Error ? e.message : '生成失败，请重试');
    } finally {
      setGenLoading(false);
    }
  };

  // ── 断点：材料缺失（终止态，无法 resume，引导律师返回上传补齐）──────────────
  if (pending && pending.kind === 'missing') {
    return (
      <div className="flex flex-col gap-4">
        <Alert variant="destructive" className="border-red-300 bg-red-50 text-red-900">
          <TriangleAlert className="h-4 w-4 text-red-600" />
          <AlertDescription className="whitespace-pre-wrap">{pending.question}</AlertDescription>
        </Alert>
        <div className="flex justify-start rounded-xl border border-border bg-white px-6 py-4 shadow-sm">
          <Button variant="outline" onClick={onBack} className="gap-2">
            <ArrowLeft className="h-4 w-4" />
            返回上传补齐材料
          </Button>
        </div>
      </div>
    );
  }

  // ── 断点：冲突 / 确认（可 resume，往返直至 pending=null）────────────────────
  if (pending) {
    return (
      <PendingResolver
        pending={pending}
        fields={current.extracted_fields}
        loading={resumeLoading}
        error={resumeError}
        onResume={handleResume}
        onBack={onBack}
      />
    );
  }

  // ── 无待决：常规字段审核 + 生成 ────────────────────────────────────────────
  const allMissing = isAllFieldsMissing(current.extracted_fields);

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
            <div className="flex items-center justify-between gap-3">
              <div>
                <CardTitle className="text-base">字段审核</CardTitle>
                <CardDescription>点击任意值可直接编辑，修改后显示蓝色标记</CardDescription>
              </div>
              <ReadinessBadge readiness={current.readiness} />
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <FieldTable
              ref={tableRef}
              extractedFields={current.extracted_fields}
              validations={current.validations}
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
                  <ValidationReport
                    report={current.validation_report}
                    validations={current.validations}
                  />
                </TabsContent>
                <TabsContent value="highlight" className="mt-3 max-h-[65vh] overflow-y-auto">
                  <HighlightList highlightList={current.highlight_list} />
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>
        </div>
      </div>

      {/* 底部操作栏 */}
      <div className="flex items-center justify-between rounded-xl border border-border bg-white px-6 py-4 shadow-sm">
        <Button variant="outline" onClick={onBack} disabled={genLoading} className="gap-2">
          <ArrowLeft className="h-4 w-4" />
          返回上传
        </Button>

        <div className="flex flex-col items-end gap-1.5">
          {genError && (
            <p className="flex items-center gap-1.5 text-xs text-destructive">
              <TriangleAlert className="h-3.5 w-3.5" />
              {genError}
            </p>
          )}
          {genLoading && (
            <p className="text-xs text-muted-foreground">正在按模板生成起诉状…</p>
          )}
          <Button onClick={handleGenerate} disabled={genLoading} size="lg" className="min-w-44 gap-2 shadow-sm">
            {genLoading ? (
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
