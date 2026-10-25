// 前端错误上报（#76）：生产构建才上报；未捕获异常、接口 5xx 上报固定枚举，不带错误消息原文
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { fetchAnalyzeProgress, resumeCase } from './api';
import { fetchAuthStatus } from './auth-api';
import { fetchBranchTable } from './branch-api';
import {
  apiName,
  installErrorReporting,
  reportClientError,
  resetErrorReportingForTest,
  setErrorContext,
} from './error-report';

type Call = { url: string; body: Record<string, unknown> };

function stubFetch(status = 204) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/client-error') calls.push({ url, body: JSON.parse(String(init?.body)) });
    return new Response(status === 204 ? null : '{"detail":"服务器错误"}', { status });
  });
  vi.stubGlobal('fetch', fetchMock);
  return calls;
}

beforeEach(() => resetErrorReportingForTest(true));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('上报内容', () => {
  it('接口 5xx：报接口名、状态码、步骤与 run_id', async () => {
    const calls = stubFetch(502);
    setErrorContext('review', 'a'.repeat(32));
    await expect(resumeCase('a'.repeat(32), { total_amount: 894934 })).rejects.toThrow();
    expect(calls).toEqual([
      {
        url: '/api/client-error',
        body: { kind: 'api_5xx', step: 'review', run_id: 'a'.repeat(32), api: 'resume', status: 502 },
      },
    ]);
  });

  it('进度轮询、原告信息表、登录接口的 5xx 同样上报', async () => {
    const calls = stubFetch(503);
    expect(await fetchAnalyzeProgress('k1')).toBeNull(); // 轮询失败仍静默
    await expect(fetchBranchTable()).rejects.toThrow();
    await expect(fetchAuthStatus()).rejects.toThrow();
    expect(calls.map((c) => c.body.api)).toEqual(['progress', 'branches', 'auth']);
  });

  it('lib 里每个直接发起的 fetch 都经过 5xx 检查（防止新加的请求漏报）', () => {
    const dir = fileURLToPath(new URL('.', import.meta.url));
    for (const name of readdirSync(dir)) {
      if (!name.endsWith('.ts') || name.endsWith('.test.ts') || name === 'error-report.ts') continue;
      const src = readFileSync(`${dir}${name}`, 'utf-8');
      const fetches = src.match(/\bfetch\(/g)?.length ?? 0;
      const checks = src.match(/noteServerError\(/g)?.length ?? 0;
      expect(checks, `${name}：${fetches} 处 fetch，${checks} 处 noteServerError`).toBeGreaterThanOrEqual(fetches);
    }
  });

  it('4xx 不上报', async () => {
    const calls = stubFetch(422);
    await expect(resumeCase('r1', {})).rejects.toThrow();
    expect(calls).toEqual([]);
  });

  it('未捕获异常与未处理的 Promise 拒绝：只报类型和步骤，不带消息原文', () => {
    const calls = stubFetch();
    const target = new EventTarget();
    installErrorReporting(target as unknown as Window);
    setErrorContext('upload');
    const err = new Event('error') as Event & { message?: string };
    err.message = 'Cannot read 虚构置业有限公司';
    target.dispatchEvent(err);
    target.dispatchEvent(new Event('unhandledrejection'));
    expect(calls.map((c) => c.body)).toEqual([
      { kind: 'uncaught', step: 'upload' },
      { kind: 'unhandled_rejection', step: 'upload' },
    ]);
    expect(JSON.stringify(calls)).not.toContain('虚构');
  });

  it('每次页面加载最多报 20 条，上报失败不抛出', () => {
    const fetchMock = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    });
    vi.stubGlobal('fetch', fetchMock);
    for (let i = 0; i < 30; i += 1) reportClientError('uncaught');
    expect(fetchMock).toHaveBeenCalledTimes(20);
  });

  it('接口路径映射为固定枚举，不带查询参数', () => {
    expect(apiName('/api/analyze/result?key=abc')).toBe('analyze_result');
    expect(apiName('/api/case/abc123')).toBe('case');
    expect(apiName('/api/upload')).toBe('upload');
    expect(apiName('/api/upload/progress?progress_id=x')).toBe('progress');
    expect(apiName('/api/branches/preview')).toBe('branches');
    expect(apiName('/api/auth/me')).toBe('auth');
    expect(apiName('/api/somewhere-new')).toBe('other');
  });
});

describe('只在生产构建上报', () => {
  it('开发 / 测试构建默认不上报', async () => {
    vi.resetModules();
    vi.stubEnv('PROD', false);
    const calls = stubFetch();
    const mod = await import('./error-report');
    mod.reportClientError('uncaught');
    expect(calls).toEqual([]);
  });

  it('生产构建默认上报', async () => {
    vi.resetModules();
    vi.stubEnv('PROD', true);
    const calls = stubFetch();
    const mod = await import('./error-report');
    mod.reportClientError('uncaught');
    expect(calls).toHaveLength(1);
  });
});
