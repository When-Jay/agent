import { Tag } from 'antd';
import { STATUS_COLORS, STATUS_LABELS } from '../theme';

// 统一状态徽标：颜色/文案与 theme.ts 全局语义保持一致。
export default function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? 'default';
  const label = STATUS_LABELS[status] ?? status;
  return <Tag color={color}>{label}</Tag>;
}
