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
  FeedbackRequest,
} from './types';
import { noteLoginRequired } from './auth-api';
import { noteServerError } from './error-report';

const BASE = '/api';

/** fetch 包装：将网络层异常（TypeError: Failed to fetch）转为中文错误；登录失效时通知回登录页；
 * 后端 5xx 上报（只报接口名与状态码） */
async function safeFetch(url: string, opts?: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(url, opts);
  } catch {
    throw new Error('网络连接失败，请检查网络设置后重试');
  }
  noteLoginRequired(res);
  noteServerError(url, res);
  return res;
}

/** 进度轮询用：失败由调用方静默处理，但后端 5xx 仍上报（只报接口名与状态码） */
async function pollFetch(url: string): Promise<Response> {
  const res = await fetch(url);
  noteServerError(url, res);
  return res;
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
export async function uploadFiles(files: File[], progressId: string): Promise<UploadResponse> {
  const form = new FormData();
  files.forEach((f) => form.append('files', f));
  // 进度 id：后端按它隔离进度，多人同时上传时进度条互不串
  form.append('progress_id', progressId);
  const res = await safeFetch(`${BASE}/upload`, { method: 'POST', body: form });
  // 超过服务器上限时 nginx 直接返回 413（英文 HTML 页，没有 detail）：给中文提示
  if (res.status === 413) {
    throw new Error('上传的文件总大小超过服务器上限，请删除不需要的文件或压缩扫描件后重试');
  }
  if (!res.ok) throw await extractError(res, '上传失败');
  return res.json() as Promise<UploadResponse>;
}

/** 查询某次上传的处理进度（上传期间轮询用；失败静默返回 null，不打断上传流程） */
export async function fetchUploadProgress(progressId: string): Promise<UploadProgress | null> {
  try {
    const res = await pollFetch(`${BASE}/upload/progress?progress_id=${encodeURIComponent(progressId)}`);
    if (!res.ok) return null;
    return (await res.json()) as UploadProgress;
  } catch {
    return null;
  }
}

/** 查询抽取任务的 LLM 处理阶段（抽取期间轮询用；失败静默返回 null，不打断流程） */
export async function fetchExtractProgress(): Promise<LlmProgress | null> {
  try {
    const res = await pollFetch(`${BASE}/extract/progress`);
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

/**
 * 查询某次分析的处理阶段（分析期间轮询用；失败静默返回 null，不打断流程）。
 * requestKey 即该次分析的请求编号（Idempotency-Key），后端按它隔离进度。
 */
export async function fetchAnalyzeProgress(requestKey: string): Promise<LlmProgress | null> {
  try {
    const res = await pollFetch(`${BASE}/analyze/progress?progress_id=${encodeURIComponent(requestKey)}`);
    if (!res.ok) return null;
    return (await res.json()) as LlmProgress;
  } catch {
    return null;
  }
}

/**
 * 启动 agent 分析（清点→抽取→代码校验）；命中断点时返回带 pending 的 CaseState。
 * requestKey：同一批材料的请求编号。重试时传同一个，后端不会重复分析（只返回已有结果）。
 */
export async function analyzeCase(
  files: ParsedFile[],
  internetAllowed: boolean,
  requestKey: string,
): Promise<CaseState> {
  const res = await safeFetch(`${BASE}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': requestKey },
    body: JSON.stringify({
      files: files.map((f) => ({
        filename: f.filename,
        text: f.text,
        is_scanned: f.is_scanned,
        identified_type: f.identified_type,
        detected_type: f.detected_type ?? null,
        pages: f.pages ?? [],
        file_id: f.file_id ?? null,
      })),
      internet_allowed: internetAllowed,
    }),
  });
  if (!res.ok) throw await extractError(res, '分析失败');
  return res.json() as Promise<CaseState>;
}

/**
 * 处理页刷新后接回同一次分析：后端还在跑就等它跑完（可能几分钟），已跑完直接返回；
 * 找不到（失败 / 服务重启 / 过期）时抛中文错误。
 */
export async function fetchAnalyzeResult(requestKey: string, signal?: AbortSignal): Promise<CaseState> {
  const res = await safeFetch(`${BASE}/analyze/result?key=${encodeURIComponent(requestKey)}`, { signal });
  if (!res.ok) throw await extractError(res, '取回上次的分析失败');
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

/** 取回会话当前状态（刷新页面后继续上次的案件）；过期或不存在时抛中文错误 */
export async function fetchCase(runId: string, signal?: AbortSignal): Promise<CaseState> {
  const res = await safeFetch(`${BASE}/case/${encodeURIComponent(runId)}`, { signal });
  if (!res.ok) throw await extractError(res, '取回上次的案件失败');
  return res.json() as Promise<CaseState>;
}

/** 调用 LLM 生成起诉状全文 */
export async function generateComplaint(
  validatedJson: ExtractedFields,
  runId?: string, // 可选：关联 agent 会话，后端据此统计律师改动
): Promise<GenerateResponse> {
  const res = await safeFetch(`${BASE}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ validated_json: validatedJson, run_id: runId }),
  });
  if (!res.ok) throw await extractError(res, '起诉状生成失败');
  return res.json() as Promise<GenerateResponse>;
}

/** 提交律师对某个字段（或整体）的问题反馈 */
export async function submitFeedback(req: FeedbackRequest): Promise<void> {
  const res = await safeFetch(`${BASE}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
  if (!res.ok) throw await extractError(res, '反馈提交失败');
}
