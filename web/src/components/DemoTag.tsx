import { Tag, Tooltip } from 'antd';

// 演示数据标记：标注该条目来自 Mock，非真实后端数据。
export default function DemoTag({ tooltip = '演示数据（Mock），非真实后端数据' }: { tooltip?: string }) {
  return (
    <Tooltip title={tooltip}>
      <Tag style={{ fontSize: 11, lineHeight: '16px', marginRight: 0 }}>演示</Tag>
    </Tooltip>
  );
}
