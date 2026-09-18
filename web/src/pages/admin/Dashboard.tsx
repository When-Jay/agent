import { useEffect, useMemo, useState } from 'react';
import { Button, Card, Col, Progress, Row, Space, Spin, Table, Tag, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  ApartmentOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  DollarOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
} from 'recharts';
import DemoTag from '../../components/DemoTag';
import EmptyState from '../../components/EmptyState';
import MetricCard from '../../components/MetricCard';
import PageHeader from '../../components/PageHeader';
import StatusBadge from '../../components/StatusBadge';
import {
  getMetrics,
  listAgents,
  listKnowledgeBases,
  listRuns,
  listWorkflows,
} from '../../api/services';
import type {
  Agent,
  KnowledgeBase,
  MetricsSnapshot,
  Run,
  RunStatus,
  Workflow,
} from '../../types';

const TIME_FORMAT = 'YYYY-MM-DD HH:mm:ss';

const RUN_STATUSES: readonly RunStatus[] = [
  'queued',
  'running',
  'completed',
  'failed',
  'cancelled',
  'waiting_for_human',
];

interface TrendPoint {
  date: string;
  runs: number;
  successRate: number | null;
}

// ---------------------------------------------------------------------------
// 纯函数工具（可单测）
// ---------------------------------------------------------------------------

/** Run 展示文本：input.message ?? input.task ?? 第一个 string 字段 */
function runTaskText(input: Record<string, unknown>): string {
  const direct = input['message'] ?? input['task'];
  if (typeof direct === 'string' && direct.trim()) return direct;
  for (const value of Object.values(input)) {
    if (typeof value === 'string' && value.trim()) return value;
  }
  return '—';
}

function shortId(id: string, max = 8): string {
  if (!id) return '—';
  return id.length > max ? `${id.slice(0, max)}…` : id;
}

function formatTime(value: string): string {
  const d = dayjs(value);
  return d.isValid() ? d.format(TIME_FORMAT) : '—';
}

function outputNumber(run: Run, key: string): number | null {
  const value = run.output?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function durationText(run: Run): string {
  if (!run.startedAt || !run.completedAt) return '—';
  const start = dayjs(run.startedAt);
  const end = dayjs(run.completedAt);
  if (!start.isValid() || !end.isValid()) return '—';
  return `${end.diff(start, 'second')}s`;
}

/** counters 中 key 含指定关键词的项求和 */
function sumCounters(counters: Record<string, number>, keyword: string): number {
  return Object.entries(counters)
    .filter(([key]) => key.toLowerCase().includes(keyword))
    .reduce((acc, [, value]) => acc + (Number.isFinite(value) ? value : 0), 0);
}

function formatSuccessRate(metrics: MetricsSnapshot): string {
  const total = metrics.runs.completed + metrics.runs.failed;
  if (total === 0) return '—';
  return `${((metrics.runs.completed / total) * 100).toFixed(1)}%`;
}

/** 平均 Latency：取 counters 中含 latency 的键求均值，保留 2 位（ms） */
function formatAvgLatency(counters: Record<string, number>): string {
  const values = Object.entries(counters)
    .filter(([key]) => key.toLowerCase().includes('latency'))
    .map(([, value]) => value)
    .filter((value) => Number.isFinite(value));
  if (values.length === 0) return '—';
  const avg = values.reduce((a, b) => a + b, 0) / values.length;
  return `${avg.toFixed(2)} ms`;
}

function formatTokenUsage(counters: Record<string, number>): string {
  const total = sumCounters(counters, 'token');
  return total > 0 ? Math.round(total).toLocaleString() : '—';
}

function formatCostTotal(counters: Record<string, number>): string {
  const total = sumCounters(counters, 'cost');
  return total > 0 ? `¥${total.toFixed(2)}` : '—';
}

/** 近 7 天按天聚合：每日 Run 数 + 每日成功率（当天无运行时成功率为 null，曲线断开） */
function buildTrend(runs: Run[]): TrendPoint[] {
  const points: TrendPoint[] = [];
  for (let i = 6; i >= 0; i--) {
    points.push({
      date: dayjs().subtract(i, 'day').format('MM-DD'),
      runs: 0,
      successRate: null,
    });
  }
  const buckets = new Map<string, { total: number; completed: number }>();
  for (const run of runs) {
    const d = dayjs(run.createdAt);
    if (!d.isValid()) continue;
    const key = d.format('MM-DD');
    const bucket = buckets.get(key) ?? { total: 0, completed: 0 };
    bucket.total += 1;
    if (run.status === 'completed') bucket.completed += 1;
    buckets.set(key, bucket);
  }
  return points.map((point) => {
    const bucket = buckets.get(point.date);
    if (!bucket) return point;
    return {
      ...point,
      runs: bucket.total,
      successRate: Number(((bucket.completed / bucket.total) * 100).toFixed(1)),
    };
  });
}

// ---------------------------------------------------------------------------
// 页面
// ---------------------------------------------------------------------------

export default function Dashboard() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [metrics, setMetrics] = useState<MetricsSnapshot | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      const [agentList, workflowList, kbList, runList, metricsSnapshot] = await Promise.all([
        listAgents(),
        listWorkflows(),
        listKnowledgeBases(),
        listRuns(),
        getMetrics(),
      ]);
      if (!alive) return;
      setAgents(agentList);
      setWorkflows(workflowList);
      setKnowledgeBases(kbList);
      setRuns(runList);
      setMetrics(metricsSnapshot);
      setLoading(false);
    })();
    return () => {
      alive = false;
    };
  }, []);

  const todayRunCount = useMemo(
    () => runs.filter((run) => dayjs(run.createdAt).isSame(dayjs(), 'day')).length,
    [runs]
  );

  const trend = useMemo(() => buildTrend(runs), [runs]);

  const statusCounts = useMemo(() => {
    const counts = new Map<RunStatus, number>(RUN_STATUSES.map((s): [RunStatus, number] => [s, 0]));
    for (const run of runs) counts.set(run.status, (counts.get(run.status) ?? 0) + 1);
    return counts;
  }, [runs]);

  /** Failure Distribution：六类失败的数值映射（取不到为 0） */
  const failureItems = useMemo(() => {
    const counters = metrics?.counters ?? {};
    return [
      { label: 'Generation', value: metrics?.nodes.failed ?? 0 },
      { label: 'Retrieval', value: sumCounters(counters, 'retrieval') },
      { label: 'Tool', value: metrics?.tools.failed ?? 0 },
      { label: 'Agent', value: metrics?.llm.failed ?? 0 },
      { label: 'System', value: sumCounters(counters, 'system') },
      { label: 'Safety', value: sumCounters(counters, 'safety') },
    ];
  }, [metrics]);
  const failureMax = Math.max(...failureItems.map((item) => item.value), 0);

  const recentRuns = useMemo(() => runs.slice(0, 10), [runs]);

  const rateValue = metrics ? formatSuccessRate(metrics) : '—';
  const latencyValue = metrics ? formatAvgLatency(metrics.counters) : '—';
  const tokenValue = metrics ? formatTokenUsage(metrics.counters) : '—';
  const costValue = metrics ? formatCostTotal(metrics.counters) : '—';

  const renderPending = () => (
    <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
      <Spin />
    </div>
  );

  const columns: ColumnsType<Run> = [
    {
      title: 'Run ID',
      dataIndex: 'id',
      key: 'id',
      width: 180,
      render: (id: string, run) => (
        <Space size={4}>
          <Tooltip title={id}>
            <code style={{ fontSize: 12 }}>{shortId(id, 14)}</code>
          </Tooltip>
          {run.source === 'demo' ? <DemoTag /> : null}
        </Space>
      ),
    },
    {
      title: 'Task',
      key: 'task',
      ellipsis: true,
      render: (_, run) => runTaskText(run.input),
    },
    {
      title: 'Agent / Workflow',
      key: 'application',
      width: 190,
      render: (_, run) => (
        <Space size={4}>
          <Tag style={{ marginRight: 0 }}>{run.runtimeType === 'agent' ? 'Agent' : 'Workflow'}</Tag>
          <span style={{ color: '#595959' }}>
            {run.applicationName ?? shortId(run.applicationId)}
          </span>
        </Space>
      ),
    },
    {
      title: '状态',
      key: 'status',
      width: 96,
      render: (_, run) => <StatusBadge status={run.status} />,
    },
    {
      title: '耗时',
      key: 'duration',
      width: 80,
      render: (_, run) => durationText(run),
    },
    {
      title: 'Token',
      key: 'tokens',
      width: 90,
      align: 'right',
      render: (_, run) => {
        const tokens = outputNumber(run, 'tokens');
        return tokens === null ? '—' : tokens.toLocaleString();
      },
    },
    {
      title: 'Cost',
      key: 'cost',
      width: 90,
      align: 'right',
      render: (_, run) => {
        const cost = outputNumber(run, 'cost');
        return cost === null ? '—' : `¥${cost.toFixed(2)}`;
      },
    },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      key: 'createdAt',
      width: 160,
      render: (value: string) => formatTime(value),
    },
    {
      title: '操作',
      key: 'action',
      width: 64,
      render: (_, run) => (
        <Button
          type="link"
          size="small"
          style={{ padding: 0 }}
          onClick={() => navigate(`/admin/runs/${run.id}`)}
        >
          详情
        </Button>
      ),
    },
  ];

  return (
    <div>
      <PageHeader title="工作台" description="平台总体运行情况概览" />

      <Row gutter={[12, 12]}>
        <Col xs={12} xl={6}>
          <MetricCard
            title="Agent 数量"
            value={loading ? '—' : agents.length}
            icon={<RobotOutlined />}
          />
        </Col>
        <Col xs={12} xl={6}>
          <MetricCard
            title="Workflow 数量"
            value={loading ? '—' : workflows.length}
            icon={<ApartmentOutlined />}
          />
        </Col>
        <Col xs={12} xl={6}>
          <MetricCard
            title="知识库数量"
            value={loading ? '—' : knowledgeBases.length}
            icon={<DatabaseOutlined />}
          />
        </Col>
        <Col xs={12} xl={6}>
          <MetricCard
            title="今日 Run"
            value={loading ? '—' : todayRunCount}
            icon={<PlayCircleOutlined />}
          />
        </Col>
      </Row>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={12} xl={6}>
          <MetricCard
            title="Task Success Rate"
            value={rateValue}
            sub={
              rateValue === '—'
                ? '暂无指标数据'
                : `${metrics?.runs.completed ?? 0} 成功 / ${metrics?.runs.failed ?? 0} 失败`
            }
            icon={<CheckCircleOutlined />}
          />
        </Col>
        <Col xs={12} xl={6}>
          <MetricCard
            title="平均 Latency"
            value={latencyValue}
            sub={latencyValue === '—' ? '暂无指标数据' : undefined}
            icon={<ClockCircleOutlined />}
          />
        </Col>
        <Col xs={12} xl={6}>
          <MetricCard
            title="Token Usage"
            value={tokenValue}
            sub={tokenValue === '—' ? '暂无指标数据' : undefined}
            icon={<ThunderboltOutlined />}
          />
        </Col>
        <Col xs={12} xl={6}>
          <MetricCard
            title="Cost"
            value={costValue}
            sub={costValue === '—' ? '暂无指标数据' : undefined}
            icon={<DollarOutlined />}
          />
        </Col>
      </Row>

      <Card size="small" title="运行趋势（近 7 天）" style={{ marginTop: 12 }}>
        {loading ? (
          renderPending()
        ) : runs.length === 0 ? (
          <EmptyState description="暂无运行数据" />
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={trend} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
              <XAxis dataKey="date" tickLine={false} axisLine={{ stroke: '#d9d9d9' }} tick={{ fontSize: 12 }} />
              <YAxis
                yAxisId="runs"
                allowDecimals={false}
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 12 }}
                width={40}
              />
              <YAxis
                yAxisId="rate"
                orientation="right"
                domain={[0, 100]}
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 12 }}
                width={40}
              />
              <RTooltip />
              <Legend />
              <Line
                yAxisId="runs"
                type="monotone"
                dataKey="runs"
                name="每日 Run 数"
                stroke="#2f54eb"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 3 }}
              />
              <Line
                yAxisId="rate"
                type="monotone"
                dataKey="successRate"
                name="成功率 %"
                stroke="#52c41a"
                strokeWidth={2}
                dot={false}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </Card>

      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} lg={12}>
          <Card size="small" title="Run 状态分布">
            {loading ? (
              renderPending()
            ) : runs.length === 0 ? (
              <EmptyState />
            ) : (
              RUN_STATUSES.map((status) => (
                <div
                  key={status}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '6px 0',
                  }}
                >
                  <StatusBadge status={status} />
                  <span style={{ color: '#595959', fontVariantNumeric: 'tabular-nums' }}>
                    {statusCounts.get(status) ?? 0}
                  </span>
                </div>
              ))
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="Failure Distribution">
            {loading ? (
              renderPending()
            ) : (
              failureItems.map((item) => (
                <div
                  key={item.label}
                  style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '4px 0' }}
                >
                  <span style={{ width: 88, flexShrink: 0, color: '#595959' }}>{item.label}</span>
                  <Progress
                    percent={failureMax > 0 ? Math.round((item.value / failureMax) * 100) : 0}
                    showInfo={false}
                    size="small"
                    strokeColor="#ff4d4f"
                    style={{ flex: 1, marginBottom: 0 }}
                  />
                  <span
                    style={{
                      width: 48,
                      textAlign: 'right',
                      color: '#595959',
                      fontVariantNumeric: 'tabular-nums',
                    }}
                  >
                    {item.value}
                  </span>
                </div>
              ))
            )}
          </Card>
        </Col>
      </Row>

      <Card size="small" title="最近运行" style={{ marginTop: 12 }}>
        <Table<Run>
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={recentRuns}
          loading={loading}
          pagination={false}
          scroll={{ x: 1000 }}
        />
      </Card>
    </div>
  );
}
