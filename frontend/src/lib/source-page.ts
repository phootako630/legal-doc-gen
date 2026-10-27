// 审核页「查看原件」（#111）：判断字段能不能对照扫描件原件，能的话是哪份文件的哪一页。
// 只对 OCR 来源的字段提供：后端只暂存了扫描件的原件；文字版材料律师直接看原文件即可。
import type { ReviewField } from './types';

export interface SourcePageTarget {
  file: string;
  page: number;
}

const FILE_RE = /《([^》]+)》/;
const OCR_RE = /OCR|扫描/i;

/** 出处带《文件名》、有已验证页码、来自 OCR 的字段才可对照原件 */
export function sourcePageTarget(
  field: Pick<ReviewField, 'src' | 'page' | 'channel'>,
): SourcePageTarget | null {
  const file = FILE_RE.exec(field.src)?.[1];
  if (!file || field.page == null || field.page < 1) return null;
  if (field.channel !== 'ocr' && !OCR_RE.test(field.src)) return null;
  return { file, page: field.page };
}

/**
 * 取原件图片并建图片地址。取回时弹窗已关闭（signal 已取消）就不建，返回 null：
 * 关闭时的清理已经跑过，晚建的地址没人释放（#118 评审）。
 */
export async function loadSourcePageUrl(
  fetchBlob: () => Promise<Blob>,
  signal: AbortSignal,
  createUrl: (blob: Blob) => string = (blob) => URL.createObjectURL(blob),
): Promise<string | null> {
  const blob = await fetchBlob();
  return signal.aborted ? null : createUrl(blob);
}
