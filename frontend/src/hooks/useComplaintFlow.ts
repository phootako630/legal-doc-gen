// 整体流程状态管理 hook：协调 upload → processing → review → preview 四步状态
import { useState } from 'react';
import type { FlowStep, UploadResponse, CaseState } from '@/lib/types';
import { newRequestKey } from '@/lib/request-key';

interface FlowState {
  step: FlowStep;
  uploadResult: UploadResponse | null;
  internetAllowed: boolean;
  // 这批材料的分析请求编号：每次上传换一个；重试、从审核页返回都沿用它，后端不会重复分析
  analyzeKey: string | null;
  caseState: CaseState | null; // agent 会话状态（含 run_id/pending，贯穿 analyze/resume）
  // 律师在审核页改过的字段（字段 key → 改后的值）：存在这里而不是审核页组件里，
  // 从预览页「返回修改」时审核页重新挂载也能恢复，不丢修改
  reviewEdits: Record<string, string>;
  complaintText: string | null;
  error: string | null;
}

interface UseComplaintFlowReturn extends FlowState {
  goToStep: (step: FlowStep) => void;
  goBack: () => void;
  setUploadResult: (r: UploadResponse, internetAllowed: boolean) => void;
  setCaseState: (s: CaseState) => void;
  setReviewEdits: (edits: Record<string, string>) => void;
  setComplaintText: (t: string) => void;
  setError: (msg: string | null) => void;
  reset: () => void;
}

const STEP_ORDER: FlowStep[] = ['upload', 'processing', 'review', 'preview'];

const initialState: FlowState = {
  step: 'upload',
  uploadResult: null,
  internetAllowed: true,
  analyzeKey: null,
  caseState: null,
  reviewEdits: {},
  complaintText: null,
  error: null,
};

export function useComplaintFlow(): UseComplaintFlowReturn {
  const [state, setState] = useState<FlowState>(initialState);

  const goToStep = (step: FlowStep) => setState((s) => ({ ...s, step, error: null }));

  const goBack = () =>
    setState((s) => {
      const idx = STEP_ORDER.indexOf(s.step);
      const prev = STEP_ORDER[Math.max(0, idx - 1)];
      return { ...s, step: prev, error: null };
    });

  // 新上传的材料 = 新的分析请求，换一个编号（联网开关也算在请求内容里）
  // 新材料：旧案件的审核修改一并清空
  const setUploadResult = (r: UploadResponse, internetAllowed: boolean) =>
    setState((s) => ({
      ...s,
      uploadResult: r,
      internetAllowed,
      analyzeKey: newRequestKey(),
      caseState: null,
      reviewEdits: {},
      complaintText: null,
    }));
  // 分析结果与断点 resume 后的新状态都存这里：返回审核页时不再弹已回答过的断点
  const setCaseState = (cs: CaseState) => setState((s) => ({ ...s, caseState: cs }));
  const setReviewEdits = (edits: Record<string, string>) =>
    setState((s) => ({ ...s, reviewEdits: edits }));
  const setComplaintText = (t: string) => setState((s) => ({ ...s, complaintText: t }));
  const setError = (msg: string | null) => setState((s) => ({ ...s, error: msg }));
  const reset = () => setState(initialState);

  return {
    ...state,
    goToStep,
    goBack,
    setUploadResult,
    setCaseState,
    setReviewEdits,
    setComplaintText,
    setError,
    reset,
  };
}
