// 案件进行中（处理 / 审核 / 预览）刷新或关闭页面时，浏览器先弹「确定离开？」，避免误操作丢失进度
import { useEffect } from 'react';

export function useLeaveWarning(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      // 旧版浏览器需要设置 returnValue 才会弹窗；提示文字由浏览器决定，自定义文字不会显示
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [active]);
}
