// 后端 API 客户端：封装 /api/upload、/api/extract、/api/generate 的 fetch 调用
import type { UploadResponse, ExtractResponse, GenerateResponse, ExtractedFields } from './types';

const BASE = '/api';

/** fetch 包装：将网络层异常（TypeError: Failed to fetch）转为中文错误 */
async function safeFetch(url: string, opts?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, opts);
  } catch {
    throw new Error('网络连接失败，请检查网络设置后重试');
  }
}

/** 从后端响应中提取中文错误信息 */
async function extractError(res: Response, fallback: string): Promise<Error> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === 'string') return new Error(detail);
  } catch {
    // 忽略 JSON 解析失败
  }
  return new Error(`${fallback}（HTTP ${res.status}）`);
}

/** 上传文件，返回解析结果 */
export async function uploadFiles(files: File[]): Promise<UploadResponse> {
  const form = new FormData();
  files.forEach((f) => form.append('files', f));
  const res = await safeFetch(`${BASE}/upload`, { method: 'POST', body: form });
  if (!res.ok) throw await extractError(res, '上传失败');
  return res.json() as Promise<UploadResponse>;
}

/** 调用 LLM 完成抽取与校验 */
export async function extractFields(
  filesText: string[],
  internetAllowed: boolean,
): Promise<ExtractResponse> {
  const res = await safeFetch(`${BASE}/extract`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ files_text: filesText, internet_allowed: internetAllowed }),
  });
  if (!res.ok) throw await extractError(res, '字段抽取失败');
  return res.json() as Promise<ExtractResponse>;
}

/** 调用 LLM 生成起诉状全文 */
export async function generateComplaint(
  validatedJson: ExtractedFields,
): Promise<GenerateResponse> {
  const res = await safeFetch(`${BASE}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ validated_json: validatedJson }),
  });
  if (!res.ok) throw await extractError(res, '起诉状生成失败');
  return res.json() as Promise<GenerateResponse>;
}
