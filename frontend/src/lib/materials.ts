// 上传材料的类型选项与齐全判断（上传页律师改了文件类型后在前端重算，与后端 /api/upload 口径一致）
import type { DocType, ParsedFile } from './types';

export interface DocTypeOption {
  value: DocType;
  label: string;
  hint: string;
}

/** 下拉框选项：顺序即显示顺序 */
export const DOC_TYPE_OPTIONS: DocTypeOption[] = [
  { value: '审批表', label: '审批表', hint: '诉讼审批表 / 诉讼流程表' },
  { value: '合同', label: '合同', hint: '本案的安装合同或买卖合同' },
  { value: '补充协议', label: '补充协议', hint: '补充协议、修改单、变更协议' },
  { value: '验收报告', label: '验收报告', hint: '监督检验报告、验收报告' },
  {
    value: '战略合同',
    label: '战略 / 框架合同',
    hint: '主合同引用的战略合同、框架协议',
  },
  { value: '未知', label: '其他 / 不确定', hint: '系统不会按特定材料处理' },
];

export function docTypeLabel(type: DocType): string {
  return DOC_TYPE_OPTIONS.find((o) => o.value === type)?.label ?? type;
}

/** 必须有的材料：缺了不能直接开始分析（验收报告缺失只提醒） */
const REQUIRED: DocType[] = ['审批表', '合同'];
const EXPECTED: DocType[] = ['审批表', '合同', '验收报告'];

export interface MaterialStatus {
  missing: DocType[]; // 缺少的材料类型（含验收报告）
  canProceed: boolean; // 审批表与合同都有
  unknownCount: number; // 类型为「其他 / 不确定」的文件数
}

export function materialStatus(files: ParsedFile[]): MaterialStatus {
  const present = new Set(files.map((f) => f.identified_type));
  return {
    missing: EXPECTED.filter((t) => !present.has(t)),
    canProceed: files.length > 0 && REQUIRED.every((t) => present.has(t)),
    unknownCount: files.filter((f) => f.identified_type === '未知').length,
  };
}

/** 刚上传完：记下系统识别的类型，之后律师改了也能看出改过 */
export function withDetectedTypes(files: ParsedFile[]): ParsedFile[] {
  return files.map((f) => ({
    ...f,
    detected_type: f.detected_type ?? f.identified_type,
  }));
}
