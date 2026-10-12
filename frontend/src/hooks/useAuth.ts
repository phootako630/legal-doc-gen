// 登录状态：启动时向后端查询是否已登录；任一接口报「需要登录」时切回登录页。
//
// 后端未开启登录（本地开发）时直接进入系统，不显示登录页和账号菜单。
import { useCallback, useEffect, useState } from 'react';
import {
  LOGIN_REQUIRED_EVENT,
  fetchAuthStatus,
  login as apiLogin,
  logout as apiLogout,
} from '@/lib/auth-api';

export type AuthPhase = 'loading' | 'login' | 'ready';

export interface AuthState {
  phase: AuthPhase;
  /** 后端是否开启了登录；false 时不显示账号菜单 */
  authEnabled: boolean;
  username: string | null;
  /** 登录页顶部的提示（如「登录已过期」） */
  notice: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export function useAuth(): AuthState {
  const [phase, setPhase] = useState<AuthPhase>('loading');
  const [authEnabled, setAuthEnabled] = useState(false);
  const [username, setUsername] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAuthStatus()
      .then((s) => {
        if (cancelled) return;
        setAuthEnabled(s.auth_enabled);
        setUsername(s.username);
        setPhase(s.auth_enabled && !s.username ? 'login' : 'ready');
      })
      .catch(() => {
        // 查不到状态（后端暂时连不上）：先进入系统，真正调接口时若需要登录会再切回登录页
        if (!cancelled) setPhase('ready');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onRequired = () => {
      setAuthEnabled(true);
      setUsername(null);
      setNotice('登录已失效，请重新登录');
      setPhase('login');
    };
    window.addEventListener(LOGIN_REQUIRED_EVENT, onRequired);
    return () => window.removeEventListener(LOGIN_REQUIRED_EVENT, onRequired);
  }, []);

  const login = useCallback(async (name: string, password: string) => {
    const s = await apiLogin(name, password);
    setAuthEnabled(s.auth_enabled);
    setUsername(s.username);
    setNotice(null);
    setPhase('ready');
  }, []);

  const logout = useCallback(async () => {
    await apiLogout();
    setUsername(null);
    setNotice(null);
    setPhase('login');
  }, []);

  return { phase, authEnabled, username, notice, login, logout };
}
