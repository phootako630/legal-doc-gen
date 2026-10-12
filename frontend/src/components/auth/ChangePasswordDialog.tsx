// 修改密码弹窗：输入当前密码与两遍新密码；成功后本机保持登录，其他电脑上的登录失效
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { CheckCircle2, Loader2, TriangleAlert } from 'lucide-react';
import { changePassword } from '@/lib/auth-api';

const MIN_LENGTH = 10;

interface ChangePasswordDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ChangePasswordDialog({ open, onOpenChange }: ChangePasswordDialogProps) {
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 关闭即清空，密码不在内存里多留
  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setOldPassword('');
      setNewPassword('');
      setConfirm('');
      setDone(false);
      setError(null);
    }
    onOpenChange(next);
  };

  // 本地先挡住明显的问题，提示更快；后端仍会再校验一遍
  const localError =
    newPassword && newPassword.length < MIN_LENGTH
      ? `新密码至少 ${MIN_LENGTH} 位`
      : confirm && confirm !== newPassword
        ? '两次输入的新密码不一致'
        : null;
  const canSubmit =
    !saving && oldPassword && newPassword.length >= MIN_LENGTH && confirm === newPassword;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSaving(true);
    setError(null);
    try {
      await changePassword(oldPassword, newPassword);
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : '修改密码失败，请重试');
    } finally {
      setSaving(false);
    }
  };

  const shownError = error ?? localError;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>修改密码</DialogTitle>
          <DialogDescription>
            新密码至少 {MIN_LENGTH} 位。修改后，其他电脑上的登录会失效，需要用新密码重新登录。
          </DialogDescription>
        </DialogHeader>

        {done ? (
          <>
            <p className="flex items-center gap-2 py-2 text-sm text-emerald-700">
              <CheckCircle2 className="h-4 w-4" />
              密码已修改
            </p>
            <DialogFooter>
              <Button onClick={() => handleOpenChange(false)}>完成</Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={submit} className="space-y-3">
            <PasswordField
              label="当前密码"
              value={oldPassword}
              onChange={setOldPassword}
              autoComplete="current-password"
            />
            <PasswordField
              label="新密码"
              value={newPassword}
              onChange={setNewPassword}
              autoComplete="new-password"
            />
            <PasswordField
              label="再输入一次新密码"
              value={confirm}
              onChange={setConfirm}
              autoComplete="new-password"
            />
            {shownError && (
              <p role="alert" className="flex items-center gap-1.5 text-sm text-destructive">
                <TriangleAlert className="h-4 w-4 shrink-0" />
                {shownError}
              </p>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => handleOpenChange(false)}
                disabled={saving}
              >
                取消
              </Button>
              <Button type="submit" disabled={!canSubmit} className="gap-2">
                {saving && <Loader2 className="h-4 w-4 animate-spin" />}
                确认修改
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function PasswordField({
  label,
  value,
  onChange,
  autoComplete,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete: string;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-medium text-foreground">{label}</span>
      <Input
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={autoComplete}
        className="h-9"
      />
    </label>
  );
}
