// 管理员口令输入：核对通过后才显示上传 / 回退操作
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Loader2, Lock } from 'lucide-react';

interface AdminLoginProps {
  onLogin: (token: string) => Promise<void>;
}

export function AdminLogin({ onLogin }: AdminLoginProps) {
  const [value, setValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!value.trim()) return;
    setLoading(true);
    setError(null);
    try {
      await onLogin(value.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : '口令核对失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-2">
      <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
        <Lock className="h-3.5 w-3.5" />
        更新或回退此表需要管理员口令
      </p>
      <div className="flex gap-2">
        <Input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="输入管理员口令"
          aria-label="管理员口令"
          className="max-w-64"
        />
        <Button
          type="submit"
          variant="outline"
          disabled={loading || !value.trim()}
          className="gap-2"
        >
          {loading && <Loader2 className="h-4 w-4 animate-spin" />}
          管理员登录
        </Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </form>
  );
}
