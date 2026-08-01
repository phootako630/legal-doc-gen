// 高亮列表：解析后端 highlight_list 文本，按关键词分色显示
import { cn } from '@/lib/utils';

function classifyLine(line: string): string {
  const t = line.toLowerCase();
  if (t.includes('缺失') || t.includes('null') || t.includes('待补充') || t.includes('未找到')) {
    return 'text-red-700 before:bg-red-500';
  }
  if (t.includes('冲突') || t.includes('不一致') || t.includes('差异')) {
    return 'text-orange-700 before:bg-orange-500';
  }
  if (t.includes('ocr') || t.includes('扫描') || t.includes('识别')) {
    return 'text-yellow-700 before:bg-yellow-500';
  }
  if (t.includes('联网') || t.includes('查询')) {
    return 'text-muted-foreground before:bg-muted-foreground/50';
  }
  return 'text-foreground/80 before:bg-foreground/30';
}

interface HighlightListProps {
  highlightList: string;
}

export function HighlightList({ highlightList }: HighlightListProps) {
  const lines = highlightList
    .split('\n')
    .map((l) => l.replace(/^[-*]\s*/, '').trim())
    .filter(Boolean);

  if (lines.length === 0) {
    return <p className="text-sm text-muted-foreground italic">无高亮项</p>;
  }

  return (
    <ul className="flex flex-col gap-2">
      {lines.map((line, idx) => {
        const colorClass = classifyLine(line);
        return (
          <li
            key={idx}
            className={cn(
              'relative pl-4 text-sm leading-relaxed',
              "before:absolute before:left-0 before:top-[7px] before:h-1.5 before:w-1.5 before:rounded-full before:content-['']",
              colorClass,
            )}
          >
            {line}
          </li>
        );
      })}
    </ul>
  );
}
