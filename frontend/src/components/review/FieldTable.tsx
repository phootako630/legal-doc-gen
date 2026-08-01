// 字段审核表格：管理 ReviewField[] 状态，渲染所有 FieldRow，斑马纹 + 摘要行
import { useState, useCallback, useImperativeHandle, forwardRef } from 'react';
import { FieldRow } from './FieldRow';
import { buildReviewFields, applyEdits } from '@/lib/buildReviewFields';
import type { ExtractedFields, ReviewField } from '@/lib/types';

export interface FieldTableHandle {
  getEditedFields: () => ExtractedFields;
}

interface FieldTableProps {
  extractedFields: ExtractedFields;
}

export const FieldTable = forwardRef<FieldTableHandle, FieldTableProps>(
  function FieldTable({ extractedFields }, ref) {
    const [rows, setRows] = useState<ReviewField[]>(() => buildReviewFields(extractedFields));

    const handleSave = useCallback((key: string, value: string) => {
      setRows((prev) =>
        prev.map((r) => (r.key === key ? { ...r, editedValue: value } : r)),
      );
    }, []);

    useImperativeHandle(ref, () => ({
      getEditedFields: () => applyEdits(extractedFields, rows),
    }));

    const counts = rows.reduce(
      (acc, r) => { acc[r.status] = (acc[r.status] ?? 0) + 1; return acc; },
      {} as Record<string, number>,
    );

    return (
      <div className="flex flex-col gap-3">
        {/* 摘要行 */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
          <span className="text-muted-foreground">共 {rows.length} 个字段</span>
          {(counts.missing   ?? 0) > 0 && (
            <span className="flex items-center gap-1 font-medium text-red-600">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-red-500" />
              {counts.missing} 项缺失
            </span>
          )}
          {(counts.conflict  ?? 0) > 0 && (
            <span className="flex items-center gap-1 font-medium text-orange-600">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-orange-500" />
              {counts.conflict} 项冲突
            </span>
          )}
          {(counts.ocr_uncertain ?? 0) > 0 && (
            <span className="flex items-center gap-1 font-medium text-amber-600">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-400" />
              {counts.ocr_uncertain} 项 OCR 待核实
            </span>
          )}
        </div>

        {/* 表格 */}
        <div className="overflow-auto rounded-lg border border-border shadow-sm">
          <table className="w-full table-fixed text-sm">
            <colgroup>
              <col className="w-[140px]" />
              <col />
              <col className="w-[130px]" />
              <col className="w-[76px]" />
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
            <tbody>
              {rows.map((row, idx) => (
                <FieldRow key={row.key} field={row} onSave={handleSave} isEven={idx % 2 === 0} />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  },
);
