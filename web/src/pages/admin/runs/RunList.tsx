// 运行记录列表（web-ui-spec.md §14）：过滤器 + Run 表格。
// 数据来自统一服务层：真实 Run + 演示 Run 合并，createdAt 倒序。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Badge, Button, Card, Segmented, Select, Space, Switch, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ReloadOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import type { Application, Run, RunStatus } from '../../../types';
import { listApplications, listRuns } from '../../../api/services';
import { RUN_STATUS_LABELS } from '../../../theme';
import StatusBadge from '../../../components/StatusBadge';
import DemoTag from '../../../components/DemoTag';
import EmptyState from '../../../components/EmptyState';
import PageHeader from '../../../components/PageHeader';

type SourceFilter = 'all' | 'real' | 'demo';

function fmtTime(value: string | null | undefined): string {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm:ss') : '—';
}

// 耗时：startedAt → (completedAt ?? 现在)，单位秒；未开始显示 —
function fmtDuration(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt) return '—';
  const end = completedAt ? dayjs(completedAt) : dayjs();
  const seconds = Math.max(0, end.diff(dayjs(startedAt), 'second', true));
  return seconds < 10 ? `${seconds.toFixed(1)} 秒` : `${Math.round(seconds)} 秒`;
}

// 服务层仅给演示 Run 标记 source: 'demo'，未标记的一律视为真实数据
const sourceOf = (run: Run): 'real' | 'demo' => run.source ?? 'real';

export default function RunList() {
  const navigate = useNavigate();
  const [runs, setRuns] = useState<Run[]>([]);
  const [apps, setApps] = useState<Application[]>([]);
  const [loading, setLoading] = useState(true);
  const [appFilter, setAppFilter] = useState<string | undefined>(undefined);
  const [statusFilter, setStatusFilter] = useState<RunStatus | undefined>(undefined);
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all');
  const [showDemo, setShowDemo] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [runList, appList] = await Promise.all([listRuns(), listApplications()]);
      setRuns(runList);
      setApps(appList);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = useMemo(() => {
    // 固定选项 value='demo'（label 演示数据）对应演示 Run 的 applicationId='app-demo'
    const filterAppId = appFilter === 'demo' ? 'app-demo' : appFilter;
    return runs.filter((r) => {
      if (filterAppId && r.applicationId !== filterAppId) return false;
      if (statusFilter && r.status !== statusFilter) return false;
      if (sourceFilter !== 'all' && sourceOf(r) !== sourceFilter) return false;
      if (!showDemo && r.source === 'demo') return false;
      return true;
    });
  }, [runs, appFilter, statusFilter, sourceFilter, showDemo]);

  const columns: ColumnsType<Run> = [
    {
      title: 'Run ID',
      dataIndex: 'id',
      width: 190,
      render: (_, r) => (
        <Space size={4}>
          <Tooltip title={r.id}>
            <span style={{ fontFamily: 'SFMono-Regular, Consolas, Menlo, monospace', fontSize: 12 }}>
              {r.id.slice(0, 13)}
            </span>
          </Tooltip>
          {r.source === 'demo' ? <DemoTag /> : null}
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'runtimeType',
      width: 90,
      render: (_, r) => <Tag>{r.runtimeType === 'workflow' ? 'Workflow' : 'Agent'}</Tag>,
    },
    {
      title: '应用',
      dataIndex: 'applicationId',
      width: 160,
      ellipsis: true,
      render: (_, r) => r.applicationName ?? (r.applicationId || '—'),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 120,
      render: (_, r) =>
        r.status === 'running' ? (
          <Badge dot color="blue">
            <StatusBadge status={r.status} />
          </Badge>
        ) : (
          <StatusBadge status={r.status} />
        ),
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 170,
      render: (_, r) => fmtTime(r.createdAt),
    },
    {
      title: '耗时',
      dataIndex: 'startedAt',
      width: 100,
      render: (_, r) => fmtDuration(r.startedAt, r.completedAt),
    },
    {
      title: '操作',
      key: 'actions',
      width: 80,
      render: (_, r) => (
        <Button
          type="link"
          size="small"
          style={{ padding: 0 }}
          onClick={() => navigate(`/admin/runs/${r.id}`)}
        >
          详情
        </Button>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="运行记录"
        description="Agent / Workflow 全部运行记录，支持按应用、状态与来源筛选"
      />
      <Card>
        <Space wrap size={12} style={{ marginBottom: 16 }}>
          <Select
            style={{ width: 200 }}
            placeholder="全部应用"
            allowClear
            value={appFilter}
            onChange={(v) => setAppFilter(v ?? undefined)}
            options={[
              ...apps.map((a) => ({ value: a.id, label: a.name })),
              { value: 'demo', label: '演示数据' },
            ]}
          />
          <Select
            style={{ width: 140 }}
            placeholder="全部状态"
            allowClear
            value={statusFilter}
            onChange={(v) => setStatusFilter(v ?? undefined)}
            options={(Object.keys(RUN_STATUS_LABELS) as RunStatus[]).map((s) => ({
              value: s,
              label: RUN_STATUS_LABELS[s],
            }))}
          />
          <Segmented
            value={sourceFilter}
            onChange={(v) => setSourceFilter(v as SourceFilter)}
            options={[
              { label: '全部', value: 'all' },
              { label: '真实', value: 'real' },
              { label: '演示', value: 'demo' },
            ]}
          />
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>
            刷新
          </Button>
          <Space size={6}>
            <span style={{ color: '#595959' }}>显示演示数据</span>
            <Switch size="small" checked={showDemo} onChange={setShowDemo} />
          </Space>
        </Space>
        <Table<Run>
          rowKey="id"
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={filtered}
          pagination={{
            pageSize: 10,
            showSizeChanger: false,
            showTotal: (total) => `共 ${total} 条`,
          }}
          locale={{ emptyText: <EmptyState description="暂无运行记录" /> }}
        />
      </Card>
    </div>
  );
}
