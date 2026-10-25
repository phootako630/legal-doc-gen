// 前端错误上报（#76）：生产构建里把未捕获异常、接口 5xx 报给 /api/client-error。
// 只报错误类型、所在步骤、哪个接口、状态码和 run_id，绝不报错误消息原文（里面可能带字段取值）。
// 上报本身失败一律忽略；每次页面加载最多报 MAX_REPORTS 条，后端另有限流，报错循环不会刷满日志。
import type { FlowStep } from './types';

export type ClientErrorKind = 'uncaught' | 'unhandled_rejection' | 'api_5xx';
export type ClientApi =
  | 'upload'
  | 'analyze'
  | 'analyze_result'
  | 'resume'
  | 'case'
  | 'generate'
  | 'feedback'
  | 'extract'
  | 'other';

export interface ClientErrorReport {
  kind: ClientErrorKind;
  step: FlowStep | 'other';
  run_id?: string;
  api?: ClientApi;
  status?: number;
}

const ENDPOINT = '/api/client-error';
const MAX_REPORTS = 20;

let context: { step: FlowStep | 'other'; runId?: string } = { step: 'other' };
let sent = 0;
// 测试里打开；正常只在生产构建上报（开发时报错看控制台就行）
let enabled = import.meta.env.PROD;

/** 流程步骤或会话变化时更新，之后的上报都带上 */
export function setErrorContext(step: FlowStep, runId?: string): void {
  context = { step, runId };
}

/** 接口路径 → 固定枚举（不报完整 URL：查询参数里有进度编号等） */
export function apiName(url: string): ClientApi {
  const path = url.split('?')[0].replace(/^\/api\//, '');
  if (path === 'analyze/result') return 'analyze_result';
  if (path.startsWith('case/')) return 'case';
  const known: ClientApi[] = ['upload', 'analyze', 'resume', 'generate', 'feedback', 'extract'];
  return known.find((k) => path === k) ?? 'other';
}

export function reportClientError(kind: ClientErrorKind, extra?: Pick<ClientErrorReport, 'api' | 'status'>): void {
  if (!enabled || sent >= MAX_REPORTS) return;
  sent += 1;
  const body: ClientErrorReport = { kind, step: context.step, ...extra };
  if (context.runId) body.run_id = context.runId;
  // 直接用 fetch，不走 api.ts 的 safeFetch：上报接口自己出错不能再触发上报
  fetch(ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    keepalive: true,
  }).catch(() => undefined);
}

/** 后端返回 5xx 时调用（api.ts 的 safeFetch 统一处理） */
export function noteServerError(url: string, res: Response): void {
  if (res.status >= 500 && res.status <= 599 && !url.startsWith(ENDPOINT)) {
    reportClientError('api_5xx', { api: apiName(url), status: res.status });
  }
}

/** 应用入口调用一次：监听未捕获异常与未处理的 Promise 拒绝 */
export function installErrorReporting(target: Window = window): void {
  target.addEventListener('error', () => reportClientError('uncaught'));
  target.addEventListener('unhandledrejection', () => reportClientError('unhandled_rejection'));
}

/** 仅供测试：打开上报并清零计数 */
export function resetErrorReportingForTest(on = true): void {
  enabled = on;
  sent = 0;
  context = { step: 'other' };
}
