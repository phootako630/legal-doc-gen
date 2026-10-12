// 登录页：账号 + 密码；账号由管理员在服务器上开通，没有注册入口
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { AppLogo } from '@/components/layout/AppLogo';
import { Loader2, TriangleAlert } from 'lucide-react';

interface LoginPageProps {
  notice: string | null;
  onLogin: (username: string, password: string) => Promise<void>;
}

export function LoginPage({ notice, onLogin }: LoginPageProps) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setLoading(true);
    setError(null);
    try {
      await onLogin(username.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败，请重试');
      setPassword('');
    } finally {
      setLoading(false);
    }
  };

  const message = error ?? notice;

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm rounded-xl border border-border bg-white p-8 shadow-sm">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <AppLogo className="h-10 w-10" />
          <div>
            <h1 className="text-lg font-semibold text-foreground">起诉状助手</h1>
            <p className="text-xs text-muted-foreground">安装合同纠纷 · 自动化生成系统</p>
          </div>
        </div>

        <form onSubmit={submit} className="space-y-4">
          <label className="block space-y-1.5">
            <span className="text-sm font-medium text-foreground">账号</span>
            <Input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              className="h-10"
            />
          </label>
          <label className="block space-y-1.5">
            <span className="text-sm font-medium text-foreground">密码</span>
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className="h-10"
            />
          </label>

          {message && (
            <p
              role="alert"
              className={`flex items-center gap-1.5 text-sm ${
                error ? 'text-destructive' : 'text-amber-700'
              }`}
            >
              <TriangleAlert className="h-4 w-4 shrink-0" />
              {message}
            </p>
          )}

          <Button
            type="submit"
            disabled={loading || !username.trim() || !password}
            className="h-10 w-full gap-2"
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            登录
          </Button>
        </form>

        <p className="mt-6 text-center text-xs text-muted-foreground">
          没有账号或忘记密码，请联系系统管理员
        </p>
      </div>
    </div>
  );
}
