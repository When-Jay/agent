// JSON 查看器：输入/输出/Payload 等结构化数据展示。
export default function JSONViewer({
  value,
  maxHeight = 320,
}: {
  value: unknown;
  maxHeight?: number;
}) {
  const text = value === undefined || value === null ? '—' : JSON.stringify(value, null, 2);
  return (
    <pre
      style={{
        margin: 0,
        padding: 12,
        background: '#fafafa',
        border: '1px solid #f0f0f0',
        borderRadius: 6,
        fontSize: 12,
        lineHeight: 1.6,
        maxHeight,
        overflow: 'auto',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-all',
      }}
    >
      {text}
    </pre>
  );
}
