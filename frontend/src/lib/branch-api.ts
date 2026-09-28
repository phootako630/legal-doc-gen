// 原告信息表 API 客户端：查看 / 下载（所有人）与预览、保存、回退（须管理员口令）
import type { BranchEntry, BranchHistoryItem, BranchPreview, BranchTable } from './types';

const BASE = '/api/branches';

/** 下载当前表的地址（浏览器直接打开即下载 .xlsx） */
export const BRANCH_EXPORT_URL = `${BASE}/export`;

async function request<T>(url: string, opts: RequestInit, fallback: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, opts);
  } catch {
    throw new Error('网络连接失败，请检查网络设置后重试');
  }
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = ((await res.json()) as { detail?: unknown }).detail;
    } catch {
      // 忽略 JSON 解析失败
    }
    throw new Error(typeof detail === 'string' ? detail : `${fallback}（HTTP ${res.status}）`);
  }
  return res.json() as Promise<T>;
}

/** 请求头只能是 latin-1：口令可能含中文，编码后发送，后端解码比对 */
function adminHeaders(token: string, json = false): HeadersInit {
  const headers: Record<string, string> = { 'X-Admin-Token': encodeURIComponent(token) };
  if (json) headers['Content-Type'] = 'application/json';
  return headers;
}

export function fetchBranchTable(): Promise<BranchTable> {
  return request<BranchTable>(BASE, {}, '读取原告信息表失败');
}

export function fetchBranchHistory(): Promise<BranchHistoryItem[]> {
  return request<BranchHistoryItem[]>(`${BASE}/history`, {}, '读取历史版本失败');
}

/** 核对管理员口令；口令错误时抛出中文错误 */
export async function checkAdminToken(token: string): Promise<void> {
  await request(
    `${BASE}/admin-check`,
    { method: 'POST', headers: adminHeaders(token) },
    '口令核对失败',
  );
}

/** 上传 Excel 预览：只解析和比对，不保存 */
export function previewBranchFile(file: File, token: string): Promise<BranchPreview> {
  const form = new FormData();
  form.append('file', file);
  return request<BranchPreview>(
    `${BASE}/preview`,
    { method: 'POST', body: form, headers: adminHeaders(token) },
    '表格解析失败',
  );
}

/** 确认更新：保存为当前表（旧版本自动存档） */
export function saveBranchTable(
  entries: BranchEntry[],
  sourceFile: string,
  token: string,
): Promise<BranchTable> {
  return request<BranchTable>(
    BASE,
    {
      method: 'POST',
      headers: adminHeaders(token, true),
      body: JSON.stringify({ entries, source_file: sourceFile }),
    },
    '保存失败',
  );
}

/** 把某个历史版本恢复为当前表 */
export function restoreBranchVersion(versionId: string, token: string): Promise<BranchTable> {
  return request<BranchTable>(
    `${BASE}/restore`,
    {
      method: 'POST',
      headers: adminHeaders(token, true),
      body: JSON.stringify({ version_id: versionId }),
    },
    '恢复失败',
  );
}

/** 「2026-09-29T10:00:00+08:00」→「2026年9月29日」 */
export function formatBranchDate(iso: string | null): string {
  const m = iso?.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[1]}年${Number(m[2])}月${Number(m[3])}日` : '未知';
}
