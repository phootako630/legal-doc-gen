// 管理员口令：核对通过后存在 sessionStorage（关闭标签页即失效），用于更新原告信息表
import { useCallback, useState } from 'react';
import { checkAdminToken } from '@/lib/branch-api';

const KEY = 'legal-doc-gen.admin-token';

function readStored(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null; // 隐私模式等场景 storage 不可用：每次重新输入口令
  }
}

export function useAdminToken() {
  const [token, setToken] = useState<string | null>(readStored);

  /** 先让后端核对口令，通过才记住；失败抛出中文错误 */
  const login = useCallback(async (input: string) => {
    await checkAdminToken(input);
    try {
      sessionStorage.setItem(KEY, input);
    } catch {
      // 存不下就只在本次页面内有效
    }
    setToken(input);
  }, []);

  const logout = useCallback(() => {
    try {
      sessionStorage.removeItem(KEY);
    } catch {
      // 忽略
    }
    setToken(null);
  }, []);

  return { token, login, logout };
}
