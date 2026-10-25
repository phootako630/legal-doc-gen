// 登录 API 客户端：查询登录状态、登录、退出、修改密码；以及「登录已失效」的全局通知
//
// 会话令牌在 HttpOnly Cookie 里，前端读不到也不需要读；同源请求浏览器会自动带上。

import { noteServerError } from './error-report';

const BASE = '/api/auth';

/** 任一接口返回「需要登录」时广播此事件，useAuth 收到后切回登录页 */
export const LOGIN_REQUIRED_EVENT = 'auth:login-required';

/**
 * 检查响应是否表示登录已失效（会话过期、被管理员停用、在别处改了密码）。
 * 只认后端中间件带的 X-Login-Required 头：原告信息表「管理员口令不正确」同样是 401，但不该跳登录页。
 */
export function noteLoginRequired(res: Response): void {
  if (res.status === 401 && res.headers.get('X-Login-Required')) {
    window.dispatchEvent(new Event(LOGIN_REQUIRED_EVENT));
  }
}

export interface AuthStatus {
  auth_enabled: boolean;
  username: string | null;
}

async function post(path: string, body?: unknown): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error('网络连接失败，请检查网络设置后重试');
  }
  noteServerError(`${BASE}${path}`, res);
  return res;
}

async function errorOf(res: Response, fallback: string): Promise<Error> {
  try {
    const detail = ((await res.json()) as { detail?: unknown }).detail;
    if (typeof detail === 'string') return new Error(detail);
  } catch {
    // 忽略 JSON 解析失败
  }
  return new Error(`${fallback}（HTTP ${res.status}）`);
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/me`);
  } catch {
    throw new Error('网络连接失败，请检查网络设置后重试');
  }
  noteServerError(`${BASE}/me`, res);
  if (!res.ok) throw await errorOf(res, '读取登录状态失败');
  return res.json() as Promise<AuthStatus>;
}

export async function login(username: string, password: string): Promise<AuthStatus> {
  const res = await post('/login', { username, password });
  if (!res.ok) throw await errorOf(res, '登录失败');
  return res.json() as Promise<AuthStatus>;
}

export async function logout(): Promise<void> {
  // 退出失败也照样回登录页：最坏情况是服务器上的会话到期后自然失效
  try {
    await post('/logout');
  } catch {
    // 忽略
  }
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  const res = await post('/change-password', {
    old_password: oldPassword,
    new_password: newPassword,
  });
  noteLoginRequired(res);
  if (!res.ok) throw await errorOf(res, '修改密码失败');
}
