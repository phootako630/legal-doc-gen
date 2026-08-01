// 第二步：AI 处理中——自动调用 /api/extract，模拟三阶段进度，成功后自动推进
import { useEffect, useRef, useCallback, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { StepProgress, type SubStep, type SubStepStatus } from './StepProgress';
import { extractFields } from '@/lib/api';
import type { UploadResponse, ExtractResponse } from '@/lib/types';

interface ProcessingStepProps {
  uploadResult: UploadResponse;
  internetAllowed: boolean;
  onDone: (result: ExtractResponse) => void;
}

type Phase = 'parse' | 'extract' | 'validate' | 'done' | 'error';

function statusOf(phase: Phase, errorAt: Phase | null, stepPhase: Phase): SubStepStatus {
  const order: Phase[] = ['parse', 'extract', 'validate', 'done'];
  const phaseIdx = order.indexOf(phase === 'error' ? (errorAt ?? 'extract') : phase);
  const stepIdx = order.indexOf(stepPhase);

  if (phase === 'error' && stepPhase === errorAt) return 'error';
  if (stepIdx < phaseIdx) return 'done';
  if (stepIdx === phaseIdx && phase !== 'error' && phase !== 'done') return 'active';
  if (phase === 'done') return 'done';
  return 'pending';
}

function buildSteps(phase: Phase, errorAt: Phase | null, errorMsg: string): SubStep[] {
  return [
    {
      label: '解析文件',
      status: statusOf(phase, errorAt, 'parse'),
      summary: '文件内容已提取',
    },
    {
      label: '抽取字段',
      status: statusOf(phase, errorAt, 'extract'),
      summary: '字段抽取完成',
      error: errorAt === 'extract' ? errorMsg : undefined,
    },
    {
      label: '交叉校验',
      status: statusOf(phase, errorAt, 'validate'),
      summary: '校验完成，可进入审核',
      error: errorAt === 'validate' ? errorMsg : undefined,
    },
  ];
}

export function ProcessingStep({ uploadResult, internetAllowed, onDone }: ProcessingStepProps) {
  const [phase, setPhase] = useState<Phase>('parse');
  const [errorAt, setErrorAt] = useState<Phase | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  const extractResultRef = useRef<ExtractResponse | null>(null);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;
  const calledRef = useRef(false);

  const runExtract = useCallback(async () => {
    calledRef.current = false;
    setPhase('parse');
    setErrorAt(null);
    setErrorMsg('');
    extractResultRef.current = null;

    // 短暂显示"解析文件"
    await delay(900);
    setPhase('extract');

    let result: ExtractResponse;
    try {
      result = await extractFields(uploadResult.files, internetAllowed);
    } catch (e) {
      setErrorAt('extract');
      setErrorMsg(e instanceof Error ? e.message : '字段抽取失败，请重试');
      setPhase('error');
      return;
    }

    extractResultRef.current = result;
    setPhase('validate');
  }, [uploadResult, internetAllowed]);

  // 启动
  useEffect(() => { runExtract(); }, [runExtract]);

  // validate 阶段：短暂后设为 done
  useEffect(() => {
    if (phase !== 'validate') return;
    const t = setTimeout(() => setPhase('done'), 700);
    return () => clearTimeout(t);
  }, [phase]);

  // done 阶段：稍停后推进
  useEffect(() => {
    if (phase !== 'done' || calledRef.current) return;
    calledRef.current = true;
    const t = setTimeout(() => {
      if (extractResultRef.current) onDoneRef.current(extractResultRef.current);
    }, 500);
    return () => clearTimeout(t);
  }, [phase]);

  const steps = buildSteps(phase, errorAt, errorMsg);

  return (
    <Card className="shadow-sm">
      <CardHeader className="border-b border-border pb-5">
        <CardTitle className="text-base">AI 正在处理</CardTitle>
        <CardDescription>请稍候，正在自动分析上传的案件材料…</CardDescription>
      </CardHeader>
      <CardContent className="px-10 py-10">
        <StepProgress
          steps={steps}
          onRetry={phase === 'error' ? runExtract : undefined}
        />
      </CardContent>
    </Card>
  );
}

function delay(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}
