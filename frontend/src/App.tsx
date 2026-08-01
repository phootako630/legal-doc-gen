// 根组件：单页面 Stepper 容器，管理 upload → processing → review → preview 四步流程
import { useComplaintFlow } from '@/hooks/useComplaintFlow';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Stepper } from '@/components/layout/Stepper';
import { UploadStep } from '@/components/upload/UploadStep';
import { ProcessingStep } from '@/components/processing/ProcessingStep';
import { ReviewStep } from '@/components/review/ReviewStep';
import { PreviewStep } from '@/components/preview/PreviewStep';

export default function App() {
  const flow = useComplaintFlow();

  return (
    <TooltipProvider delay={300}>
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="sticky top-0 z-10 border-b border-border bg-white shadow-sm">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-8 py-3.5">
          <div className="flex items-center gap-3">
            {/* 法律天平图标 */}
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <svg viewBox="0 0 24 24" className="h-4.5 w-4.5 fill-current" aria-hidden>
                <path d="M12 3a1 1 0 0 1 .894.553l3.5 7A1 1 0 0 1 15.5 12H13v5h3a1 1 0 1 1 0 2H8a1 1 0 1 1 0-2h3v-5H8.5a1 1 0 0 1-.894-1.447l3.5-7A1 1 0 0 1 12 3Z" opacity=".3"/>
                <path d="M3 7a1 1 0 0 1 1-1h4a1 1 0 0 1 0 2H5.414l2.293 2.293a1 1 0 0 1-1.414 1.414l-3-3A1 1 0 0 1 3 8V7Zm14-1h4a1 1 0 0 1 .707 1.707l-3 3a1 1 0 0 1-1.414-1.414L19.586 8H18a1 1 0 1 1 0-2Zm-5-3a1 1 0 0 1 1 1v16a1 1 0 1 1-2 0V4a1 1 0 0 1 1-1Z"/>
              </svg>
            </div>
            <div>
              <h1 className="text-[15px] font-semibold leading-tight tracking-tight text-foreground">起诉状助手</h1>
              <p className="text-[11px] leading-tight text-muted-foreground">安装合同纠纷 · 自动化生成系统</p>
            </div>
          </div>
          <span className="rounded-full border border-border bg-muted px-3 py-1 text-[11px] font-medium text-muted-foreground">
            v1.0 内部版
          </span>
        </div>
      </header>

      {/* 步骤条 */}
      <div className="border-b border-border bg-white px-8 py-4">
        <div className="mx-auto max-w-5xl">
          <Stepper currentStep={flow.step} />
        </div>
      </div>

      {/* 主内容区 */}
      <main className="mx-auto max-w-5xl px-8 py-8">
        {/* 错误提示 */}
        {flow.error && (
          <div className="mb-6 flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            <svg viewBox="0 0 20 20" className="mt-0.5 h-4 w-4 shrink-0 fill-current" aria-hidden><path fillRule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm.75-11.25a.75.75 0 0 0-1.5 0v4.5a.75.75 0 0 0 1.5 0v-4.5Zm-.75 8a1 1 0 1 1 0-2 1 1 0 0 1 0 2Z" clipRule="evenodd"/></svg>
            <span>{flow.error}</span>
          </div>
        )}

        {flow.step === 'upload' && (
          <UploadStep
            onDone={(result, internetAllowed) => {
              flow.setUploadResult(result, internetAllowed);
              flow.goToStep('processing');
            }}
          />
        )}

        {flow.step === 'processing' && flow.uploadResult && (
          <ProcessingStep
            uploadResult={flow.uploadResult}
            internetAllowed={flow.internetAllowed}
            onDone={(result) => {
              flow.setExtractResult(result);
              flow.goToStep('review');
            }}
          />
        )}

        {flow.step === 'review' && flow.extractResult && (
          <ReviewStep
            extractResult={flow.extractResult}
            onBack={flow.goBack}
            onDone={(complaintText) => {
              flow.setComplaintText(complaintText);
              flow.goToStep('preview');
            }}
          />
        )}

        {flow.step === 'preview' && flow.complaintText && (
          <PreviewStep
            complaintText={flow.complaintText}
            onBack={flow.goBack}
            onReset={flow.reset}
          />
        )}
      </main>

      {/* Footer */}
      <footer className="mt-12 border-t border-border bg-white px-8 py-4">
        <p className="mx-auto max-w-5xl text-center text-[11px] text-muted-foreground">
          仅供内部律师团队使用 · 生成内容须经人工复核后方可使用 · 不构成法律意见
        </p>
      </footer>
    </div>
    </TooltipProvider>
  );
}
