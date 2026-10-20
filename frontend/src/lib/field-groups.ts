// 审核表格的字段分组：57 个字段按律师核对的顺序分成当事人 / 合同与金额 / 付款与违约条款 / 管辖与文书
import type { ReviewField } from './types';

export interface FieldGroup {
  title: string;
  rows: ReviewField[];
}

const PARTY_KEYS = new Set([
  'plaintiff_name_final',
  'plaintiff_credit_code',
  'plaintiff_person_in_charge',
  'plaintiff_address',
  'plaintiff_phone',
  'defendant_name',
  'defendant_credit_code',
  'defendant_legal_rep',
  'defendant_address',
  'defendant_address_ref',
  'contacts',
  'contract_party_a',
  'contract_party_b',
]);

const CLAUSE_KEYS = new Set([
  'payment_cite_location',
  'payment_cite_text',
  'payment_clause_location',
  'payment_clause_summary',
  'payment_clause_text',
  'breach_interest_clause_location',
  'breach_clause_text',
  'interest_term',
  'breach_interest_rate_text',
  'interest_rate_basis',
  'penalty_cap',
  'penalty_amount',
  'penalty_daily_text',
  'penalty_start_date',
  'penalty_until_date',
  'warranty_period',
  'warranty_expiry_sentence',
]);

const JURISDICTION_KEYS = new Set([
  'document_kind',
  'dispute_clause_location',
  'dispute_clause_text',
  'project_site',
  'delivery_place',
  'contract_sign_place',
  'contract_sign_place_cover',
  'install_address',
  'court_district',
  'jurisdiction_text',
  'arbitration_institution',
  'addressee',
]);

const GROUP_TITLES = ['当事人', '合同与金额', '付款与违约条款', '管辖与文书'] as const;

function groupOf(key: string): (typeof GROUP_TITLES)[number] {
  if (PARTY_KEYS.has(key)) return '当事人';
  if (CLAUSE_KEYS.has(key)) return '付款与违约条款';
  if (JURISDICTION_KEYS.has(key)) return '管辖与文书';
  // 合同编号、台数、金额、质保金、移交等，以及以后新增未归类的字段
  return '合同与金额';
}

/** 按组归类，组内保持原有顺序；没有字段的组不返回 */
export function groupReviewFields(rows: ReviewField[]): FieldGroup[] {
  const byTitle = new Map<string, ReviewField[]>(GROUP_TITLES.map((t) => [t, []]));
  for (const r of rows) byTitle.get(groupOf(r.key))?.push(r);
  return GROUP_TITLES.map((title) => ({ title, rows: byTitle.get(title) ?? [] })).filter(
    (g) => g.rows.length > 0,
  );
}

/** 需要律师处理的字段：缺失、冲突、待核实 */
export function needsAttention(row: ReviewField): boolean {
  return row.status !== 'normal';
}
