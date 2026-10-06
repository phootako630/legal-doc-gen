// 生成请求编号（Idempotency-Key）：同一批上传的材料用同一个编号，后端据此避免重复分析
//
// crypto.randomUUID 只在 HTTPS 或 localhost 下可用；律所内网用 http://192.168.x.x 访问时
// 它不存在，退回 crypto.getRandomValues（任何页面都可用）自己拼一个 UUID v4。

export function newRequestKey(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // 版本号 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 变体
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
