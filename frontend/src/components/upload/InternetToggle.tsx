// 联网查询开关：允许 AI 通过企业名称在线补全统一社会信用代码等信息
import { Globe } from 'lucide-react';
import { Switch } from '@/components/ui/switch';

interface InternetToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
}

export function InternetToggle({ checked, onChange }: InternetToggleProps) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-border bg-muted/30 px-4 py-3.5">
      <div className="flex items-center gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Globe className="h-4 w-4" />
        </div>
        <div>
          <p className="text-sm font-medium leading-tight">联网查询原告信息</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            允许 AI 通过企业名称在线补全统一社会信用代码等信息
          </p>
        </div>
      </div>
      <Switch
        checked={checked}
        onCheckedChange={onChange}
        aria-label="联网查询开关"
      />
    </div>
  );
}
