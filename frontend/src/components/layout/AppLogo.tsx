// 系统标志（法律天平图标）：页头与登录页共用
import { cn } from '@/lib/utils';

export function AppLogo({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground',
        className,
      )}
    >
      <svg viewBox="0 0 24 24" className="h-[56%] w-[56%] fill-current" aria-hidden>
        <path d="M12 3a1 1 0 0 1 .894.553l3.5 7A1 1 0 0 1 15.5 12H13v5h3a1 1 0 1 1 0 2H8a1 1 0 1 1 0-2h3v-5H8.5a1 1 0 0 1-.894-1.447l3.5-7A1 1 0 0 1 12 3Z" opacity=".3"/>
        <path d="M3 7a1 1 0 0 1 1-1h4a1 1 0 0 1 0 2H5.414l2.293 2.293a1 1 0 0 1-1.414 1.414l-3-3A1 1 0 0 1 3 8V7Zm14-1h4a1 1 0 0 1 .707 1.707l-3 3a1 1 0 0 1-1.414-1.414L19.586 8H18a1 1 0 1 1 0-2Zm-5-3a1 1 0 0 1 1 1v16a1 1 0 1 1-2 0V4a1 1 0 0 1 1-1Z"/>
      </svg>
    </div>
  );
}
