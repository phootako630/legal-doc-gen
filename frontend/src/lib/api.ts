// 后端 API 客户端：封装 /api/upload、/api/extract、/api/generate 的 fetch 调用
import type {
  UploadResponse,
  UploadProgress,
  LlmProgress,
  ExtractResponse,
  GenerateResponse,
  ExtractedFields,
  ParsedFile,
  CaseState,
} from './types';

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

/** 查询上传处理进度（上传期间轮询用；失败静默返回 null，不打断上传流程） */
export async function fetchUploadProgress(): Promise<UploadProgress | null> {
  try {
    const res = await fetch(`${BASE}/upload/progress`);
    if (!res.ok) return null;
    return (await res.json()) as UploadProgress;
  } catch {
    return null;
  }
}

/** 查询抽取任务的 LLM 处理阶段（抽取期间轮询用；失败静默返回 null，不打断流程） */
export async function fetchExtractProgress(): Promise<LlmProgress | null> {
  try {
    const res = await fetch(`${BASE}/extract/progress`);
    if (!res.ok) return null;
    return (await res.json()) as LlmProgress;
  } catch {
    return null;
  }
}

/** 调用 LLM 完成抽取与校验；携带文件名/类型/是否扫描件，供后端结构化拼装与 OCR 标记 */
export async function extractFields(
  files: ParsedFile[],
  internetAllowed: boolean,
): Promise<ExtractResponse> {
  const res = await safeFetch(`${BASE}/extract`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      files: files.map((f) => ({
        filename: f.filename,
        text: f.text,
        is_scanned: f.is_scanned,
        identified_type: f.identified_type,
        pages: f.pages ?? [],
        file_id: f.file_id ?? null,
      })),
      internet_allowed: internetAllowed,
    }),
  });
  if (!res.ok) throw await extractError(res, '字段抽取失败');
  return res.json() as Promise<ExtractResponse>;
}

/** 查询 agent 任务的处理阶段（analyze/resume 期间轮询用；失败静默返回 null，不打断流程） */
export async function fetchAnalyzeProgress(): Promise<LlmProgress | null> {
  try {
    const res = await fetch(`${BASE}/analyze/progress`);
    if (!res.ok) return null;
    return (await res.json()) as LlmProgress;
  } catch {
    return null;
  }
}

/** 启动 agent 分析（清点→抽取→代码校验）；命中断点时返回带 pending 的 CaseState */
export async function analyzeCase(
  files: ParsedFile[],
  internetAllowed: boolean,
): Promise<CaseState> {
  const res = await safeFetch(`${BASE}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      files: files.map((f) => ({
        filename: f.filename,
        text: f.text,
        is_scanned: f.is_scanned,
        identified_type: f.identified_type,
        pages: f.pages ?? [],
        file_id: f.file_id ?? null,
      })),
      internet_allowed: internetAllowed,
    }),
  });
  if (!res.ok) throw await extractError(res, '分析失败');
  return res.json() as Promise<CaseState>;
}

/** 在断点处提交律师决定并恢复 agent；可能再次返回 pending，直至 pending=null */
export async function resumeCase(
  runId: string,
  decisions: Record<string, string | number | null>,
): Promise<CaseState> {
  const res = await safeFetch(`${BASE}/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, decisions }),
  });
  if (!res.ok) throw await extractError(res, '恢复失败');
  return res.json() as Promise<CaseState>;
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
