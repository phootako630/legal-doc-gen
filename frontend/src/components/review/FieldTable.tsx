// 字段审核表格：管理 ReviewField[] 状态，按组渲染 FieldRow，斑马纹 + 摘要行 +「只看需处理的」筛选
import { useState, useCallback, useImperativeHandle, forwardRef } from 'react';
import { FieldRow } from './FieldRow';
import { buildReviewFields, applyEdits } from '@/lib/buildReviewFields';
import { groupReviewFields, needsAttention } from '@/lib/field-groups';
import type { ExtractedFields, ReviewField, ValidationCheck } from '@/lib/types';

export interface FieldTableHandle {
  getEditedFields: () => ExtractedFields;
}

interface FieldTableProps {
  extractedFields: ExtractedFields;
  validations?: ValidationCheck[];
  /** 行内「反馈」按钮回调；不传则不显示 */
  onFeedback?: (field: ReviewField) => void;
  /** 之前已做的修改（字段 key → 值）：从预览页返回时恢复 */
  initialEdits?: Record<string, string>;
  /** 每次修改后回报全部修改，由流程状态保存 */
  onEditsChange?: (edits: Record<string, string>) => void;
  /** 会话编号：OCR 来源的字段据此「查看原件」 */
  runId?: string;
}

function collectEdits(rows: ReviewField[]): Record<string, string> {
  const edits: Record<string, string> = {};
  for (const r of rows) if (r.editedValue !== undefined) edits[r.key] = r.editedValue;
  return edits;
}

export const FieldTable = forwardRef<FieldTableHandle, FieldTableProps>(
  function FieldTable(
    { extractedFields, validations = [], onFeedback, initialEdits, onEditsChange, runId },
    ref,
  ) {
    const [rows, setRows] = useState<ReviewField[]>(() =>
      buildReviewFields(extractedFields, validations).map((r) =>
        initialEdits && r.key in initialEdits ? { ...r, editedValue: initialEdits[r.key] } : r,
      ),
    );

    const handleSave = useCallback(
      (key: string, value: string) => {
        const next = rows.map((r) => (r.key === key ? { ...r, editedValue: value } : r));
        setRows(next);
        onEditsChange?.(collectEdits(next));
      },
      [rows, onEditsChange],
    );

    useImperativeHandle(ref, () => ({
      getEditedFields: () => applyEdits(extractedFields, rows),
    }));

    const counts = rows.reduce(
      (acc, r) => { acc[r.status] = (acc[r.status] ?? 0) + 1; return acc; },
      {} as Record<string, number>,
    );
    const attentionCount = rows.filter(needsAttention).length;
    // 只看缺失 / 冲突 / 待核实的字段。状态按分析结果定、律师修改后不变，行不会在编辑时消失
    const [onlyAttention, setOnlyAttention] = useState(false);
    const groups = groupReviewFields(onlyAttention ? rows.filter(needsAttention) : rows);

    return (
      <div className="flex flex-col gap-3">
        {/* 摘要行 */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 pt-3 text-xs">
          <span className="text-muted-foreground">共 {rows.length} 个字段</span>
          {(counts.missing   ?? 0) > 0 && (
            <span className="flex items-center gap-1 font-medium text-status-missing">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-status-missing" />
              {counts.missing} 项缺失
            </span>
          )}
          {(counts.conflict  ?? 0) > 0 && (
            <span className="flex items-center gap-1 font-medium text-status-conflict">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-status-conflict" />
              {counts.conflict} 项冲突
            </span>
          )}
          {(counts.ocr_uncertain ?? 0) > 0 && (
            <span className="flex items-center gap-1 font-medium text-status-review">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-status-review" />
              {counts.ocr_uncertain} 项待核实
            </span>
          )}
          {attentionCount > 0 && (
            <label className="ml-auto flex cursor-pointer items-center gap-1.5 text-foreground">
              <input
                type="checkbox"
                checked={onlyAttention}
                onChange={(e) => setOnlyAttention(e.target.checked)}
                className="h-3.5 w-3.5 accent-primary"
              />
              只看需处理的（{attentionCount}）
            </label>
          )}
        </div>

        {/* 表格 */}
        <div className="overflow-auto border-t border-border">
          <table className="w-full table-fixed text-sm">
            <colgroup>
              <col className="w-[160px]" />
              <col />
              <col className="w-[200px]" />
              <col className="w-[84px]" />
            </colgroup>
            <thead>
              <tr className="border-b border-border bg-muted/60">
                <th className="py-2.5 pl-4 pr-3 text-left text-xs font-semibold tracking-wide text-muted-foreground">
                  字段
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold tracking-wide text-muted-foreground">
                  值&nbsp;<span className="font-normal">(点击编辑)</span>
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-semibold tracking-wide text-muted-foreground">
                  来源
                </th>
                <th className="py-2.5 pr-4 text-left text-xs font-semibold tracking-wide text-muted-foreground">
                  状态
                </th>
              </tr>
            </thead>
            {groups.map((g) => (
              <tbody key={g.title}>
                <tr className="border-b border-border bg-secondary">
                  <th
                    colSpan={4}
                    scope="colgroup"
                    className="py-2 pl-4 text-left text-sm font-semibold text-secondary-foreground"
                  >
                    {g.title}
                    <span className="ml-2 font-normal text-muted-foreground">
                      {g.rows.length} 项
                      {!onlyAttention &&
                        g.rows.some(needsAttention) &&
                        `，${g.rows.filter(needsAttention).length} 项需处理`}
                    </span>
                  </th>
                </tr>
                {g.rows.map((row, idx) => (
                  <FieldRow
                    key={row.key}
                    field={row}
                    onSave={handleSave}
                    isEven={idx % 2 === 0}
                    onFeedback={onFeedback}
                    runId={runId}
                  />
                ))}
              </tbody>
            ))}
          </table>
        </div>
      </div>
    );
  },
);
