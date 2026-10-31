// 上传大小限制：单个文件与一次上传的总大小（与服务器 nginx 的 client_max_body_size 100m 对应）
export const MAX_FILE_MB = 20;
// nginx 限制整个请求 100MB（deploy/nginx/app.conf），留出表单编码的余量
export const MAX_TOTAL_MB = 95;

const MB = 1024 * 1024;

export function formatMB(bytes: number): string {
  return `${(bytes / MB).toFixed(1)} MB`;
}

/** 所选文件总大小超过一次上传的上限时返回中文提示，否则返回 null */
export function totalSizeError(files: File[]): string | null {
  const total = files.reduce((sum, f) => sum + f.size, 0);
  if (total <= MAX_TOTAL_MB * MB) return null;
  return (
    `所选文件共 ${formatMB(total)}，超过一次上传 ${MAX_TOTAL_MB} MB 的上限。` +
    '请删除不需要的文件，或把过大的扫描件压缩后再上传。'
  );
}

/** 同一份文件的标识：文件名 + 大小 + 修改时间。只比名字和大小会把另一份同名同大小的材料当重复（#149 评审） */
function fileKey(f: File): string {
  return `${f.name}|${f.size}|${f.lastModified}`;
}

/** 合并新选的文件：重复选中同一份文件只留一份；同名但不是同一份的材料照样加入，上传后由后端自动编号（#121） */
export function mergeSelectedFiles(prev: File[], incoming: File[]): File[] {
  const seen = new Set(prev.map(fileKey));
  const added: File[] = [];
  for (const f of incoming) {
    const key = fileKey(f);
    if (seen.has(key)) continue;
    seen.add(key);
    added.push(f);
  }
  return [...prev, ...added];
}
