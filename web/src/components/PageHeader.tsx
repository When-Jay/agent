import type { ReactNode } from 'react';

// 页头：标题 + 说明 + 右侧操作区。
export default function PageHeader({
  title,
  description,
  extra,
}: {
  title: string;
  description?: string;
  extra?: ReactNode;
}) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'flex-start',
        marginBottom: 16,
      }}
    >
      <div>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>{title}</h2>
        {description ? <div style={{ color: '#8c8c8c', marginTop: 4, fontSize: 13 }}>{description}</div> : null}
      </div>
      {extra ? <div>{extra}</div> : null}
    </div>
  );
}
