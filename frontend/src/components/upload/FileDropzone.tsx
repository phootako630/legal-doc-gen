// 拖拽/点击上传区域，支持 PDF 和 DOCX，单文件限 20MB
import { useRef, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { UploadCloud } from 'lucide-react';

const ACCEPTED_TYPES = new Set([
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
]);
const ACCEPTED_EXT = ['.pdf', '.docx'];
const MAX_SIZE_MB = 20;
const MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024;

interface FileDropzoneProps {
  onFiles: (files: File[]) => void;
}

export function FileDropzone({ onFiles }: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [dragError, setDragError] = useState<string | null>(null);

  const validate = useCallback((rawFiles: File[]): { valid: File[]; errors: string[] } => {
    const valid: File[] = [];
    const errors: string[] = [];
    for (const f of rawFiles) {
      if (!ACCEPTED_TYPES.has(f.type)) {
        errors.push(`"${f.name}" 格式不支持，仅接受 PDF 和 Word 文件`);
      } else if (f.size === 0) {
        errors.push(`"${f.name}" 是空文件，请检查后重新上传`);
      } else if (f.size > MAX_SIZE_BYTES) {
        errors.push(`"${f.name}" 超过 ${MAX_SIZE_MB}MB 限制`);
      } else {
        valid.push(f);
      }
    }
    return { valid, errors };
  }, []);

  const handleFiles = useCallback(
    (rawFiles: File[]) => {
      const { valid, errors } = validate(rawFiles);
      setDragError(errors.length > 0 ? errors.join('；') : null);
      if (valid.length > 0) onFiles(valid);
    },
    [validate, onFiles],
  );

  const onDragOver = (e: React.DragEvent) => { e.preventDefault(); setIsDragging(true); };
  const onDragLeave = () => setIsDragging(false);
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    handleFiles(Array.from(e.dataTransfer.files));
  };
  const onInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) handleFiles(Array.from(e.target.files));
    e.target.value = '';
  };

  return (
    <div className="flex flex-col gap-2">
      <div
        role="button"
        tabIndex={0}
        aria-label="点击或拖拽上传文件"
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
        className={cn(
          'relative flex cursor-pointer flex-col items-center justify-center gap-4 rounded-xl border-2 border-dashed px-10 py-14 text-center transition-all duration-200 select-none outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2',
          isDragging
            ? 'border-primary bg-primary/5 shadow-inner'
            : 'border-border bg-white hover:border-primary/40 hover:bg-accent/30',
        )}
      >
        {/* 上传图标 */}
        <div
          className={cn(
            'flex h-14 w-14 items-center justify-center rounded-full transition-colors',
            isDragging ? 'bg-primary/10' : 'bg-muted',
          )}
        >
          <UploadCloud
            className={cn(
              'h-7 w-7 transition-colors',
              isDragging ? 'text-primary' : 'text-muted-foreground/50',
            )}
          />
        </div>

        <div>
          <p
            className={cn(
              'text-sm font-semibold transition-colors',
              isDragging ? 'text-primary' : 'text-foreground',
            )}
          >
            {isDragging ? '松开即可上传' : '拖拽文件至此，或点击选择'}
          </p>
          <p className="mt-1.5 text-xs text-muted-foreground">
            支持 PDF、DOCX 格式 &nbsp;·&nbsp; 单文件最大 {MAX_SIZE_MB} MB &nbsp;·&nbsp; 可多文件同时选择
          </p>
        </div>

        {/* 拖拽时的高亮遮罩边框 */}
        {isDragging && (
          <div className="pointer-events-none absolute inset-0 rounded-xl ring-2 ring-primary/30" />
        )}
      </div>

      {dragError && (
        <p className="flex items-start gap-1.5 text-xs text-destructive">
          <svg viewBox="0 0 16 16" className="mt-0.5 h-3.5 w-3.5 shrink-0 fill-current" aria-hidden>
            <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm-.75 3.75a.75.75 0 0 1 1.5 0v3.5a.75.75 0 0 1-1.5 0v-3.5Zm.75 7a.875.875 0 1 1 0-1.75.875.875 0 0 1 0 1.75Z"/>
          </svg>
          {dragError}
        </p>
      )}

      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPTED_EXT.join(',')}
        aria-label="选择案件文件"
        className="hidden"
        onChange={onInputChange}
      />
    </div>
  );
}
