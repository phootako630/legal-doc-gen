// 整体流程状态管理 hook：协调 upload → processing → review → preview 四步状态
import { useState } from 'react';
import type { FlowStep, UploadResponse, ExtractResponse } from '@/lib/types';

interface FlowState {
  step: FlowStep;
  uploadResult: UploadResponse | null;
  internetAllowed: boolean;
  extractResult: ExtractResponse | null;
  complaintText: string | null;
  error: string | null;
}

interface UseComplaintFlowReturn extends FlowState {
  goToStep: (step: FlowStep) => void;
  goBack: () => void;
  setUploadResult: (r: UploadResponse, internetAllowed: boolean) => void;
  setExtractResult: (r: ExtractResponse) => void;
  setComplaintText: (t: string) => void;
  setError: (msg: string | null) => void;
  reset: () => void;
}

const STEP_ORDER: FlowStep[] = ['upload', 'processing', 'review', 'preview'];

const initialState: FlowState = {
  step: 'upload',
  uploadResult: null,
  internetAllowed: true,
  extractResult: null,
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

  const setUploadResult = (r: UploadResponse, internetAllowed: boolean) =>
    setState((s) => ({ ...s, uploadResult: r, internetAllowed }));
  const setExtractResult = (r: ExtractResponse) => setState((s) => ({ ...s, extractResult: r }));
  const setComplaintText = (t: string) => setState((s) => ({ ...s, complaintText: t }));
  const setError = (msg: string | null) => setState((s) => ({ ...s, error: msg }));
  const reset = () => setState(initialState);

  return { ...state, goToStep, goBack, setUploadResult, setExtractResult, setComplaintText, setError, reset };
}
