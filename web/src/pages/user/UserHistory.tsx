// 用户端运行历史：轻量表格 + 来源过滤（全部/真实/演示）+ 行点击详情。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Card, Segmented, Space, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import type { Run } from '../../types';
import { listRuns } from '../../api/services';
import StatusBadge from '../../components/StatusBadge';
import DemoTag from '../../components/DemoTag';
import DetailDrawer from '../../components/DetailDrawer';
import EmptyState from '../../components/EmptyState';
import JSONViewer from '../../components/JSONViewer';
import PageHeader from '../../components/PageHeader';

type SourceFilter = 'all' | 'real' | 'demo';

const MONO = { fontFamily: 'SFMono-Regular, Consolas, Menlo, monospace', fontSize: 12 };

const fmtTime = (v: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '—');

// 耗时：startedAt → (completedAt ?? 现在)，单位秒；未开始显示 —
function fmtDuration(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt) return '—';
  const end = completedAt ? dayjs(completedAt) : dayjs();
  const seconds = Math.max(0, end.diff(dayjs(startedAt), 'second', true));
  return seconds < 10 ? `${seconds.toFixed(1)} 秒` : `${Math.round(seconds)} 秒`;
}

// 服务层仅给演示 Run 标记 source: 'demo'，未标记的一律视为真实数据
const sourceOf = (run: Run): 'real' | 'demo' => run.source ?? 'real';

// 输入摘要：优先 input.message，否则 JSON 截短 40 字
function inputSummary(run: Run): string {
  const msg = run.input['message'];
  if (typeof msg === 'string' && msg) return msg;
  const json = JSON.stringify(run.input ?? {});
  return json.length > 40 ? `${json.slice(0, 40)}…` : json;
}

export default function UserHistory() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [source, setSource] = useState<SourceFilter>('all');
  const [selected, setSelected] = useState<Run | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRuns(await listRuns());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(
    () => runs.filter((r) => source === 'all' || sourceOf(r) === source),
    [runs, source]
  );

  const columns: ColumnsType<Run> = [
    { title: '时间', dataIndex: 'createdAt', width: 170, render: (_, r) => fmtTime(r.createdAt) },
    {
      title: '应用',
      dataIndex: 'applicationName',
      width: 160,
      ellipsis: true,
      render: (_, r) => r.applicationName ?? r.applicationId ?? '—',
    },
    {
      title: '类型',
      dataIndex: 'runtimeType',
      width: 90,
      render: (_, r) => <Tag>{r.runtimeType === 'workflow' ? 'Workflow' : 'Agent'}</Tag>,
    },
    { title: '状态', dataIndex: 'status', width: 110, render: (_, r) => <StatusBadge status={r.status} /> },
    { title: '耗时', key: 'duration', width: 90, render: (_, r) => fmtDuration(r.startedAt, r.completedAt) },
    { title: '输入摘要', key: 'input', ellipsis: true, render: (_, r) => inputSummary(r) },
  ];

  return (
    <div>
      <PageHeader title="运行历史" description="全部运行记录（含演示数据）" />
      <Card style={{ borderRadius: 10 }}>
        <Segmented
          style={{ marginBottom: 16 }}
          value={source}
          onChange={(v) => setSource(v as SourceFilter)}
          options={[
            { label: '全部', value: 'all' },
            { label: '真实', value: 'real' },
            { label: '演示', value: 'demo' },
          ]}
        />
        <Table<Run>
          rowKey="id"
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={filtered}
          pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (total) => `共 ${total} 条` }}
          locale={{ emptyText: <EmptyState description="暂无运行记录" /> }}
          onRow={(r) => ({ onClick: () => setSelected(r), style: { cursor: 'pointer' } })}
        />
      </Card>
      <DetailDrawer
        open={selected !== null}
        title={selected ? `运行 ${selected.id.slice(0, 8)}` : ''}
        onClose={() => setSelected(null)}
      >
        {selected ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Space size={12} wrap>
              <StatusBadge status={selected.status} />
              {sourceOf(selected) === 'demo' ? <DemoTag /> : null}
              <Typography.Text copyable style={MONO}>
                {selected.id}
              </Typography.Text>
            </Space>
            <div>
              <div style={{ marginBottom: 6 }}>输入</div>
              <JSONViewer value={selected.input} />
            </div>
            <div>
              <div style={{ marginBottom: 6 }}>输出</div>
              <JSONViewer value={selected.output} />
            </div>
            <div>
              <div style={{ marginBottom: 6 }}>错误</div>
              <JSONViewer value={selected.error} />
            </div>
          </Space>
        ) : null}
      </DetailDrawer>
    </div>
  );
}
