// 本标签页的案件进度（sessionStorage）：刷新页面后可「继续上次的案件」，关掉标签页即清除。
// 只存会话编号和律师的字段修改；案件本身在后端（过期自动清除），取回时再查。
const KEY = 'complaint-case-session';

export interface SavedCaseSession {
  runId: string;
  edits: Record<string, string>;
  savedAt: number; // 毫秒时间戳
}

export function saveCaseSession(runId: string, edits: Record<string, string>): void {
  const data: SavedCaseSession = { runId, edits, savedAt: Date.now() };
  try {
    sessionStorage.setItem(KEY, JSON.stringify(data));
  } catch {
    // 隐私模式 / 存储被禁用：只是不能刷新后恢复，不影响当前流程
  }
}

export function loadCaseSession(): SavedCaseSession | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    const data = JSON.parse(raw) as Partial<SavedCaseSession>;
    if (typeof data.runId !== 'string' || typeof data.savedAt !== 'number') return null;
    const edits: Record<string, string> = {};
    for (const [k, v] of Object.entries(data.edits ?? {})) {
      if (typeof v === 'string') edits[k] = v;
    }
    return { runId: data.runId, edits, savedAt: data.savedAt };
  } catch {
    return null;
  }
}

export function clearCaseSession(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // 同上，忽略
  }
}
