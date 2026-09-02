// agent 断点决策 UI：把 interrupt 抛出的 pending（冲突/确认）呈现给律师，
// 律师逐字段确定取值后提交，经 /api/resume 恢复 agent（可多轮往返直至 pending=null）。
import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Loader2, TriangleAlert, ArrowLeft, Check } from 'lucide-react';
import { fieldNameMap } from '@/lib/field-map';
import type { ExtractedFields, PendingDecision } from '@/lib/types';

interface PendingResolverProps {
  pending: PendingDecision; // kind 为 'conflict' | 'confirm'
  fields: ExtractedFields;
  loading: boolean;
  error: string | null;
  onResume: (decisions: Record<string, string>) => void;
  onBack: () => void;
}

// 用于按 key 动态读取 ExtractedFields 中某字段的当前值（防御性，字段可能缺失）
type AnyFields = Record<string, unknown>;

function currentValue(fields: ExtractedFields, key: string): string {
  const fv = (fields as unknown as AnyFields)[key];
  if (fv && typeof fv === 'object' && 'value' in (fv as object)) {
    const v = (fv as { value: string | number | null }).value;
    return v === null || v === undefined ? '' : String(v);
  }
  return '';
}

export function PendingResolver({
  pending,
  fields,
  loading,
  error,
  onResume,
  onBack,
}: PendingResolverProps) {
  // 每个待决字段的输入值，初始化为当前抽取值，律师据冲突说明修正
  const [values, setValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const key of pending.field_keys) init[key] = currentValue(fields, key);
    return init;
  });

  const isConfirm = pending.kind === 'confirm';
  const title = isConfirm ? '请律师确认' : '请律师核实冲突';
  const desc = isConfirm
    ? '以下信息需要人工确认后方可继续生成'
    : '检测到数据冲突，请核对各来源后确定最终取值';

  const handleSubmit = () => {
    // 提交律师确认的取值；trim 后为空的字段按 null 提交（表示仍缺失）
    const decisions: Record<string, string> = {};
    for (const key of pending.field_keys) {
      decisions[key] = values[key]?.trim() ?? '';
    }
    onResume(decisions);
  };

  return (
    <Card className="border-amber-300 bg-amber-50/40 shadow-sm">
      <CardHeader className="border-b border-amber-200 pb-4">
        <CardTitle className="flex items-center gap-2 text-base text-amber-900">
          <TriangleAlert className="h-4 w-4 text-amber-600" />
          {title}
        </CardTitle>
        <CardDescription className="text-amber-800/80">{desc}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5 p-6">
        {/* agent 抛出的中文说明（含各来源具体数值） */}
        <p className="whitespace-pre-wrap rounded-md border border-amber-200 bg-white px-4 py-3 text-sm text-foreground">
          {pending.question}
        </p>

        {/* 逐字段取值 */}
        <div className="space-y-4">
          {pending.field_keys.map((key) => (
            <div key={key} className="space-y-1.5">
              <label className="flex items-center gap-2 text-sm font-medium text-foreground">
                {fieldNameMap[key] ?? key}
                <Badge variant="outline" className="font-normal text-[11px] text-muted-foreground">
                  {key}
                </Badge>
              </label>
              <Input
                value={values[key] ?? ''}
                disabled={loading}
                placeholder="请输入确认后的取值（留空表示该字段缺失）"
                onChange={(e) => setValues((s) => ({ ...s, [key]: e.target.value }))}
              />
              {/* 若 agent 提供候选值，作为快捷填充 */}
              {pending.options.length > 0 && (
                <div className="flex flex-wrap gap-1.5 pt-0.5">
                  {pending.options.map((opt) => (
                    <button
                      key={opt}
                      type="button"
                      disabled={loading}
                      onClick={() => setValues((s) => ({ ...s, [key]: opt }))}
                      className="rounded border border-border bg-white px-2 py-0.5 text-xs text-muted-foreground hover:border-primary hover:text-primary disabled:opacity-50"
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        {error && (
          <p className="flex items-center gap-1.5 text-xs text-destructive">
            <TriangleAlert className="h-3.5 w-3.5" />
            {error}
          </p>
        )}

        <div className="flex items-center justify-between pt-1">
          <Button variant="outline" onClick={onBack} disabled={loading} className="gap-2">
            <ArrowLeft className="h-4 w-4" />
            返回上传
          </Button>
          <Button onClick={handleSubmit} disabled={loading} className="min-w-40 gap-2 shadow-sm">
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                提交中…
              </>
            ) : (
              <>
                <Check className="h-4 w-4" />
                提交并继续
              </>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
