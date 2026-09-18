import { Empty } from 'antd';

// 空状态：统一文案与内边距。
export default function EmptyState({ description = '暂无数据' }: { description?: string }) {
  return <Empty description={description} style={{ padding: '32px 0' }} />;
}
