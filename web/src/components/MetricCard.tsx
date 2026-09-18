import { Card } from 'antd';
import type { ReactNode } from 'react';

// 指标卡片：Dashboard / 详情页顶部指标复用。
export default function MetricCard({
  title,
  value,
  sub,
  icon,
}: {
  title: string;
  value: ReactNode;
  sub?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <Card size="small">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ color: '#8c8c8c', fontSize: 12 }}>{title}</div>
          <div style={{ fontSize: 22, fontWeight: 600, lineHeight: '32px', marginTop: 4 }}>{value}</div>
          {sub ? <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 2 }}>{sub}</div> : null}
        </div>
        {icon ? <div style={{ fontSize: 22, color: '#2f54eb', opacity: 0.7 }}>{icon}</div> : null}
      </div>
    </Card>
  );
}
