// ExtractedFields → ReviewField[] 转换，以及将编辑结果写回 ExtractedFields
import type { ExtractedFields, FieldStatus, ReviewField } from './types';
import { fieldNameMap, reviewFieldKeys } from './field-map';

// 用于绕过严格类型，按 key 动态访问 ExtractedFields
type AnyFields = Record<string, unknown>;

function inferStatus(value: string | number | null, src: string): FieldStatus {
  if (value === null) return 'missing';
  const s = src.toLowerCase();
  if (s.includes('冲突') || s.includes('不一致')) return 'conflict';
  if (s.includes('ocr') || s.includes('扫描')) return 'ocr_uncertain';
  return 'normal';
}

/** 将后端 ExtractedFields 转换为审核表格所需的 ReviewField[] */
export function buildReviewFields(fields: ExtractedFields): ReviewField[] {
  const rows: ReviewField[] = [];
  const raw = fields as unknown as AnyFields;

  for (const key of reviewFieldKeys) {
    const label = fieldNameMap[key] ?? key;

    // contacts 是数组，特殊处理
    if (key === 'contacts') {
      const list = fields.contacts ?? [];
      const value =
        list.length > 0
          ? list
              .map((c) => `${c.name.value ?? '?'}（${c.phone.value ?? '?'}）`)
              .join('；')
          : null;
      rows.push({ key, label, value, src: '', status: value ? 'normal' : 'missing', isEditing: false });
      continue;
    }

    const fv = raw[key];
    if (!fv || typeof fv !== 'object' || !('value' in (fv as object))) continue;

    const { value, src } = fv as { value: string | number | null; src: string };
    rows.push({
      key,
      label,
      value: value !== null ? String(value) : null,
      src: src ?? '',
      status: inferStatus(value, src ?? ''),
      isEditing: false,
    });
  }

  return rows;
}

/** 检查是否所有可审核字段均为缺失状态（材料完全无法识别时使用） */
export function isAllFieldsMissing(fields: ExtractedFields): boolean {
  return buildReviewFields(fields).every((f) => f.status === 'missing');
}

/** 将 ReviewField[] 中律师编辑的值写回 ExtractedFields（深拷贝） */
export function applyEdits(original: ExtractedFields, reviewFields: ReviewField[]): ExtractedFields {
  const result = structuredClone(original) as unknown as AnyFields;

  for (const rf of reviewFields) {
    if (rf.editedValue === undefined || rf.key === 'contacts') continue;
    const fv = result[rf.key];
    if (fv && typeof fv === 'object' && 'value' in (fv as object)) {
      (fv as Record<string, unknown>).value = rf.editedValue;
    }
  }

  return result as unknown as ExtractedFields;
}
