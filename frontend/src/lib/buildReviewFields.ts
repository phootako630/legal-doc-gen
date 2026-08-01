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

    // contacts 是数组，特殊处理。
    // LLM 返回的结构不可信：条目本身、name/phone、其 value 都可能是 null 或缺失，
    // 必须防御性访问——曾因某条目 name 为 null 直接读 .value 导致审核页整页白屏
    if (key === 'contacts') {
      type LooseFieldValue = { value?: string | number | null } | null | undefined;
      type LooseContact = { name?: LooseFieldValue; phone?: LooseFieldValue } | null | undefined;
      const list = (fields.contacts ?? []) as LooseContact[];
      const parts: string[] = [];
      for (const c of list) {
        const name = c?.name?.value;
        const phone = c?.phone?.value;
        // 姓名电话都拿不到的条目没有展示价值，直接丢弃
        if (name == null && phone == null) continue;
        parts.push(`${name ?? '？'}（${phone ?? '？'}）`);
      }
      const value = parts.length > 0 ? parts.join('；') : null;
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
      // 律师已人工核实修改，清除旧的 src（可能含"冲突"/"OCR"等提示词），
      // 避免这些提示原封不动地传入 /api/generate，导致起诉状里出现已解决问题的警示标记
      (fv as Record<string, unknown>).src = '律师人工确认修改';
    }
  }

  return result as unknown as ExtractedFields;
}
