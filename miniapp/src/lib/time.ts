export function formatTime(ts: number | null | undefined): string {
  if (!ts) {
    return '-';
  }
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    hour12: false,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}

export function formatRelative(ts: number): string {
  const now = Math.floor(Date.now() / 1000);
  const diff = Math.max(0, now - ts);
  if (diff < 60) {
    return '刚刚';
  }
  if (diff < 3600) {
    return `${Math.floor(diff / 60)} 分钟前`;
  }
  if (diff < 86400) {
    return `${Math.floor(diff / 3600)} 小时前`;
  }
  return `${Math.floor(diff / 86400)} 天前`;
}
