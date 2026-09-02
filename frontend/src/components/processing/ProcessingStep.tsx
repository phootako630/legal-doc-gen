// 第二步：AI 处理中——启动 agent（/api/analyze），轮询后端真实阶段驱动进度展示
import { useEffect, useRef, useCallback, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { StepProgress, type SubStep, type SubStepStatus } from './StepProgress';
import { analyzeCase, fetchAnalyzeProgress } from '@/lib/api';
import type { UploadResponse, CaseState } from '@/lib/types';

interface ProcessingStepProps {
  uploadResult: UploadResponse;
  internetAllowed: boolean;
  onDone: (result: CaseState) => void;
}

/** 后端三个 LLM 阶段（与 /api/extract/progress 返回的 stage 名一一对应） */
type StageKey = 'checklist' | 'extract' | 'validate';

const STAGE_ORDER: StageKey[] = ['checklist', 'extract', 'validate'];

const STAGE_BY_NAME: Record<string, StageKey> = {
  材料清点: 'checklist',
  字段抽取: 'extract',
  校验高亮: 'validate',
};

const STAGE_LABELS: Record<StageKey, string> = {
  checklist: '材料清点',
  extract: '抽取字段',
  validate: '交叉校验',
};

const STAGE_SUMMARIES: Record<StageKey, string> = {
  checklist: '材料清点完成',
  extract: '字段抽取完成',
  validate: '校验完成，可进入审核',
};

/** 各阶段的预计耗时提示（基于 DeepSeek 实际观测，帮律师建立等待预期） */
const STAGE_HINTS: Record<StageKey, string> = {
  checklist: '正在核对材料是否齐全，通常需要 10–30 秒',
  extract: '正在逐字段抽取并标注出处，本步最耗时，通常需要 1–2 分钟',
  validate: '正在交叉校验各文件间的数据一致性，通常需要 30–60 秒',
};

type Phase = 'running' | 'done' | 'error';

function buildSteps(
  phase: Phase,
  activeStage: StageKey,
  elapsed: number,
  errorMsg: string,
): SubStep[] {
  const activeIdx = STAGE_ORDER.indexOf(activeStage);

  const llmSteps = STAGE_ORDER.map((stage, idx): SubStep => {
    let status: SubStepStatus;
    if (phase === 'done' || idx < activeIdx) status = 'done';
    else if (idx > activeIdx) status = 'pending';
    else status = phase === 'error' ? 'error' : 'active';

    return {
      label: STAGE_LABELS[stage],
      status,
      summary: STAGE_SUMMARIES[stage],
      error: status === 'error' ? errorMsg : undefined,
      activeHint:
        status === 'active'
          ? `${STAGE_HINTS[stage]}（已用时 ${elapsed} 秒）`
          : undefined,
    };
  });

  // 文件解析在上传步骤已完成，这里作为已完成步骤展示，让律师看到完整链路
  return [
    { label: '解析文件', status: 'done', summary: '上传时已完成' },
    ...llmSteps,
  ];
}

export function ProcessingStep({ uploadResult, internetAllowed, onDone }: ProcessingStepProps) {
  const [phase, setPhase] = useState<Phase>('running');
  const [activeStage, setActiveStage] = useState<StageKey>('checklist');
  const [elapsed, setElapsed] = useState(0);
  const [errorMsg, setErrorMsg] = useState('');
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  const runAnalyze = useCallback(async () => {
    setPhase('running');
    setActiveStage('checklist');
    setElapsed(0);
    setErrorMsg('');

    let result: CaseState;
    try {
      result = await analyzeCase(uploadResult.files, internetAllowed);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : '处理失败，请重试');
      setPhase('error');
      return;
    }

    setPhase('done');
    // 稍停片刻让律师看到全部完成的绿勾，再推进到审核页（命中断点则由审核页处理 pending）
    setTimeout(() => onDoneRef.current(result), 600);
  }, [uploadResult, internetAllowed]);

  // 启动。React StrictMode 开发模式下 effect 会双重执行，若不拦截会同时发出
  // 两个 /api/analyze 请求（各含多次 LLM 调用）：双倍耗时费用，且两次结果
  // 可能不一致（曾出现一次 422 一次成功的竞态）——用 ref 保证只启动一次
  const startedRef = useRef(false);
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    runAnalyze();
  }, [runAnalyze]);

  // 处理期间每秒轮询后端真实阶段；elapsed 由后端计算，避免前后端时钟不一致
  useEffect(() => {
    if (phase !== 'running') return;
    const timer = setInterval(async () => {
      const p = await fetchAnalyzeProgress();
      if (p?.active && p.stage in STAGE_BY_NAME) {
        setActiveStage(STAGE_BY_NAME[p.stage]);
        setElapsed(p.stage_elapsed_s);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [phase]);

  const steps = buildSteps(phase, activeStage, elapsed, errorMsg);

  return (
    <Card className="shadow-sm">
      <CardHeader className="border-b border-border pb-5">
        <CardTitle className="text-base">AI 正在处理</CardTitle>
        <CardDescription>请稍候，正在自动分析上传的案件材料…</CardDescription>
      </CardHeader>
      <CardContent className="px-10 py-10">
        <StepProgress
          steps={steps}
          onRetry={phase === 'error' ? runAnalyze : undefined}
        />
      </CardContent>
    </Card>
  );
}
