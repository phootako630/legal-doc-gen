// 第四步：预览与下载——渲染起诉状全文、高亮标记、XXX 警告，以及 Word 下载
import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Loader2, Download, TriangleAlert, ArrowLeft, RotateCcw } from 'lucide-react';
import { ComplaintPreview } from './ComplaintPreview';
import { buildComplaintHtml, downloadAsWord } from '@/lib/html-doc-generator';

interface PreviewStepProps {
  complaintText: string;
  onBack: () => void;
  onReset: () => void;
}

export function PreviewStep({ complaintText, onBack, onReset }: PreviewStepProps) {
  const [downloading, setDownloading] = useState(false);

  const xxxCount = (complaintText.match(/XXX/g) ?? []).length;

  const handleDownload = () => {
    setDownloading(true);
    try {
      // TODO: headerText 目前用固定文案占位；若后续把当事人姓名/案号一路传到本组件，
      // 可以在这里换成真实案件名（如"XX诉XX安装合同纠纷"）
      const html = buildComplaintHtml(complaintText, { headerText: '民事起诉状' });
      downloadAsWord(html, '起诉状.doc');
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="flex flex-col gap-5">
      {/* XXX 警告 */}
      {xxxCount > 0 && (
        <Alert className="border-orange-200 bg-orange-50">
          <TriangleAlert className="h-4 w-4 text-orange-500" />
          <AlertDescription className="text-orange-800">
            起诉状中仍有{' '}
            <strong className="font-semibold text-orange-900">{xxxCount}</strong>{' '}
            处未填写内容（标记为 XXX），请返回修改或在下载后手动补充。
          </AlertDescription>
        </Alert>
      )}

      {/* 预览卡片 */}
      <Card className="shadow-sm">
        <CardHeader className="border-b border-border pb-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardTitle className="text-base">预览起诉状</CardTitle>
              <CardDescription className="mt-1">
                请仔细核对内容后再下载 &nbsp;—&nbsp;
                <span className="text-red-600 font-medium">红色</span>为缺失字段，
                <span className="text-orange-600 font-medium">橙色</span>为冲突字段，
                <span className="text-amber-600 font-medium">黄色</span>为 OCR 待核实内容
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        {/* 灰色背景托盘，衬托出 A4 纸张效果 */}
        <CardContent className="bg-muted/40 p-6">
          <ComplaintPreview text={complaintText} />
        </CardContent>
      </Card>

      {/* 底部操作栏 */}
      <div className="flex items-center justify-between rounded-xl border border-border bg-white px-6 py-4 shadow-sm">
        <div className="flex gap-2">
          <Button variant="outline" onClick={onBack} disabled={downloading} className="gap-2">
            <ArrowLeft className="h-4 w-4" />
            返回修改
          </Button>
          <Button variant="ghost" onClick={onReset} disabled={downloading} className="gap-2 text-muted-foreground hover:text-foreground">
            <RotateCcw className="h-3.5 w-3.5" />
            重新开始
          </Button>
        </div>

        <Button
          onClick={handleDownload}
          disabled={downloading}
          size="lg"
          className="min-w-40 gap-2 shadow-sm"
        >
          {downloading ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              生成中…
            </>
          ) : (
            <>
              <Download className="h-4 w-4" />
              下载 Word 文档
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
