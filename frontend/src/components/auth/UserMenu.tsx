// 页头账号区：显示当前登录账号，提供「修改密码」与「退出登录」
import { useState } from 'react';
import { KeyRound, LogOut, UserRound } from 'lucide-react';
import { ChangePasswordDialog } from './ChangePasswordDialog';

interface UserMenuProps {
  username: string;
  onLogout: () => Promise<void>;
}

export function UserMenu({ username, onLogout }: UserMenuProps) {
  const [pwOpen, setPwOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);

  const logout = async () => {
    setLeaving(true);
    try {
      await onLogout();
    } finally {
      setLeaving(false);
    }
  };

  const itemClass =
    'flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50';

  return (
    <div className="flex items-center gap-1 border-l border-border pl-3">
      <span className="flex items-center gap-1.5 px-1 text-sm font-medium text-foreground">
        <UserRound className="h-4 w-4 text-muted-foreground" />
        {username}
      </span>
      <button type="button" onClick={() => setPwOpen(true)} className={itemClass}>
        <KeyRound className="h-4 w-4" />
        修改密码
      </button>
      <button type="button" onClick={logout} disabled={leaving} className={itemClass}>
        <LogOut className="h-4 w-4" />
        退出登录
      </button>
      <ChangePasswordDialog open={pwOpen} onOpenChange={setPwOpen} />
    </div>
  );
}
