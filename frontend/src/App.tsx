// 根组件：单页面 Stepper 容器，管理 upload → processing → review → preview 四步流程；
// 页头「原告信息表」切换到表格维护页（不打断正在进行的案件，返回后流程状态仍在）。
// 后端开启登录时先显示登录页；登录失效后回到登录页，重新登录后流程状态仍在。
import { useState } from 'react';
import { useComplaintFlow } from '@/hooks/useComplaintFlow';
import { useLeaveWarning } from '@/hooks/useLeaveWarning';
import { clearCaseSession, loadCaseSession } from '@/lib/case-session';
import { ResumeCaseBanner } from '@/components/upload/ResumeCaseBanner';
import { useAuth } from '@/hooks/useAuth';
import { LoginPage } from '@/components/auth/LoginPage';
import { UserMenu } from '@/components/auth/UserMenu';
import { AppLogo } from '@/components/layout/AppLogo';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Stepper } from '@/components/layout/Stepper';
import { UploadStep } from '@/components/upload/UploadStep';
import { ProcessingStep } from '@/components/processing/ProcessingStep';
import { ReviewStep } from '@/components/review/ReviewStep';
import { PreviewStep } from '@/components/preview/PreviewStep';
import { BranchTablePage } from '@/components/branches/BranchTablePage';
import { Building2 } from 'lucide-react';

export default function App() {
  const flow = useComplaintFlow();
  const auth = useAuth();
  const [view, setView] = useState<'flow' | 'branches'>('flow');
  // 审核页字段多、列多，放宽到 1280px；其余步骤保持窄版便于阅读。页头 / 步骤条 / 页脚一起切，左右对齐
  const container = view === 'flow' && flow.step === 'review' ? 'max-w-[1280px]' : 'max-w-5xl';
  // 页面打开时读一次：本标签页刷新前有未完成的案件，就在上传页问律师是否继续
  const [savedCase, setSavedCase] = useState(loadCaseSession);
  useLeaveWarning(flow.step !== 'upload');

  if (auth.phase === 'loading') return <div className="min-h-screen bg-background" />;
  if (auth.phase === 'login') return <LoginPage notice={auth.notice} onLogin={auth.login} />;

  return (
    <TooltipProvider delay={300}>
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="sticky top-0 z-10 border-b border-border bg-white shadow-sm">
        <div className={`mx-auto flex ${container} items-center justify-between px-8 py-3.5`}>
          <div className="flex items-center gap-3">
            <AppLogo />
            <div>
              <h1 className="text-[15px] font-semibold leading-tight tracking-tight text-foreground">起诉状助手</h1>
              <p className="text-xs leading-tight text-muted-foreground">安装合同纠纷 · 自动化生成系统</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setView(view === 'branches' ? 'flow' : 'branches')}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                view === 'branches'
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              }`}
            >
              <Building2 className="h-4 w-4" />
              原告信息表
            </button>
            <span className="rounded-full border border-border bg-muted px-3 py-1 text-xs font-medium text-muted-foreground">
              v1.0 内部版
            </span>
            {auth.authEnabled && auth.username && (
              <UserMenu username={auth.username} onLogout={auth.logout} />
            )}
          </div>
        </div>
      </header>

      {view === 'branches' ? (
        <main className="mx-auto max-w-5xl px-8 py-8">
          <BranchTablePage onBack={() => setView('flow')} />
        </main>
      ) : (
      <>
      {/* 步骤条 */}
      <div className="border-b border-border bg-white px-8 py-4">
        <div className={`mx-auto ${container}`}>
          <Stepper currentStep={flow.step} />
        </div>
      </div>

      {/* 主内容区 */}
      <main className={`mx-auto ${container} px-8 py-8`}>
        {/* 错误提示 */}
        {flow.error && (
          <div className="mb-6 flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            <svg viewBox="0 0 20 20" className="mt-0.5 h-4 w-4 shrink-0 fill-current" aria-hidden><path fillRule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm.75-11.25a.75.75 0 0 0-1.5 0v4.5a.75.75 0 0 0 1.5 0v-4.5Zm-.75 8a1 1 0 1 1 0-2 1 1 0 0 1 0 2Z" clipRule="evenodd"/></svg>
            <span>{flow.error}</span>
          </div>
        )}

        {flow.step === 'upload' && savedCase && (
          <ResumeCaseBanner
            saved={savedCase}
            onResume={(caseState, edits) => {
              setSavedCase(null);
              flow.restoreCase(caseState, edits);
            }}
            onDiscard={() => {
              clearCaseSession();
              setSavedCase(null);
            }}
          />
        )}

        {flow.step === 'upload' && (
          <UploadStep
            onDone={(result, internetAllowed) => {
              setSavedCase(null);
              flow.setUploadResult(result, internetAllowed);
              flow.goToStep('processing');
            }}
            onOpenBranches={() => setView('branches')}
          />
        )}

        {flow.step === 'processing' && flow.uploadResult && flow.analyzeKey && (
          <ProcessingStep
            uploadResult={flow.uploadResult}
            internetAllowed={flow.internetAllowed}
            analyzeKey={flow.analyzeKey}
            onDone={(result) => {
              flow.setCaseState(result);
              flow.goToStep('review');
            }}
          />
        )}

        {flow.step === 'review' && flow.caseState && (
          <ReviewStep
            caseState={flow.caseState}
            edits={flow.reviewEdits}
            onEditsChange={flow.setReviewEdits}
            onCaseStateChange={flow.setCaseState}
            // 按钮写的是「返回上传」：直接回上传页。若用 goBack 会退到处理页，
            // 同一请求编号的分析会立刻原样返回，又跳回审核页，看起来像按钮失灵
            onBack={() => flow.goToStep('upload')}
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
      </>
      )}

      {/* Footer */}
      <footer className="mt-12 border-t border-border bg-white px-8 py-4">
        <p className={`mx-auto ${container} text-center text-xs text-muted-foreground`}>
          仅供内部律师团队使用 · 生成内容须经人工复核后方可使用 · 不构成法律意见
        </p>
      </footer>
    </div>
    </TooltipProvider>
  );
}
