// 本标签页的案件进度（sessionStorage）：刷新页面后可「继续上次的案件」，关掉标签页即清除。
// 只存编号和律师的字段修改；案件本身在后端（过期自动清除），取回时再查。
// 处理中只有分析请求编号（analyzeKey）：刷新后按编号接回同一次分析，不必重新上传、不重复计费；
// 进入审核页后改存会话编号（runId）。
const KEY = 'complaint-case-session';

export interface SavedCaseSession {
  runId: string | null;
  analyzeKey: string | null;
  edits: Record<string, string>;
  savedAt: number; // 毫秒时间戳
}

export function saveCaseSession(runId: string, edits: Record<string, string>): void {
  write({ runId, analyzeKey: null, edits, savedAt: Date.now() });
}

/** 处理中：记下分析请求编号，刷新后可接回这次分析 */
export function saveAnalyzeSession(analyzeKey: string): void {
  write({ runId: null, analyzeKey, edits: {}, savedAt: Date.now() });
}

function write(data: SavedCaseSession): void {
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
    const runId = typeof data.runId === 'string' ? data.runId : null;
    const analyzeKey = typeof data.analyzeKey === 'string' ? data.analyzeKey : null;
    if ((!runId && !analyzeKey) || typeof data.savedAt !== 'number') return null;
    const edits: Record<string, string> = {};
    for (const [k, v] of Object.entries(data.edits ?? {})) {
      if (typeof v === 'string') edits[k] = v;
    }
    return { runId, analyzeKey, edits, savedAt: data.savedAt };
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
