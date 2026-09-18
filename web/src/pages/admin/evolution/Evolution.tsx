// 进化管理（/admin/evolution）：进化任务 / 候选对比 / 实验 / Decision Pipeline。
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Select,
  Space,
  Spin,
  Steps,
  Table,
  Tabs,
  Tag,
  message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import type { StepsProps } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import DemoTag from '../../../components/DemoTag';
import DetailDrawer from '../../../components/DetailDrawer';
import EmptyState from '../../../components/EmptyState';
import PageHeader from '../../../components/PageHeader';
import StatusBadge from '../../../components/StatusBadge';
import {
  getEvolutionTask,
  listCandidatesByTask,
  listEvolutionRuns,
  listEvolutionTasks,
  type EvolutionRunItem,
} from '../../../api/services';
import type { EvolutionCandidate, EvolutionTask } from '../../../types';

// ---------- 展示辅助 ----------
function fmtTime(s: string): string {
  const d = dayjs(s);
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : '—';
}

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

/** 相对基线变化：负值（下降）为好，绿色；正值红色。 */
function renderDelta(v: number): ReactNode {
  if (v === 0) return <span style={{ color: '#8c8c8c' }}>0.0%</span>;
  const down = v < 0;
  return (
    <span style={{ color: down ? '#52c41a' : '#cf1322', fontWeight: 500 }}>
      {down ? '↓' : '↑'}
      {Math.abs(v * 100).toFixed(1)}%
    </span>
  );
}

/** 实验运行状态：非终态 processing，终态按语义着色。 */
function renderRunStatus(status: string): ReactNode {
  if (status === 'COMPLETED') return <Tag color="success">{status}</Tag>;
  if (status === 'FAILED') return <Tag color="error">{status}</Tag>;
  if (status === 'CANCELLED' || status === 'CANCELED') return <Tag>{status}</Tag>;
  return <Tag color="processing">{status}</Tag>;
}

type PipelineStepStatus = EvolutionTask['pipeline'][number]['status'];

const PIPELINE_STEP_STATUS: Record<PipelineStepStatus, 'finish' | 'process' | 'wait' | 'error'> = {
  done: 'finish',
  active: 'process',
  pending: 'wait',
  failed: 'error',
};

function pipelineItems(pipeline: EvolutionTask['pipeline']): StepsProps['items'] {
  return pipeline.map((p) => ({
    key: p.stage,
    title: p.stage,
    status: PIPELINE_STEP_STATUS[p.status],
  }));
}

// ---------- 候选对比：行 = 指标，列 = 候选 ----------
interface MetricRow {
  key: string;
  label: string;
  cell: (c: EvolutionCandidate) => ReactNode;
}

const METRICS: MetricRow[] = [
  { key: 'validation', label: '验证集', cell: (c) => pct(c.scores.validation) },
  { key: 'regression', label: '回归集', cell: (c) => pct(c.scores.regression) },
  { key: 'challenge', label: '挑战集', cell: (c) => pct(c.scores.challenge) },
  { key: 'cost', label: '成本变化', cell: (c) => renderDelta(c.scores.cost) },
  { key: 'latency', label: '延迟变化', cell: (c) => renderDelta(c.scores.latency) },
  { key: 'safety', label: '安全', cell: (c) => pct(c.scores.safety) },
  { key: 'editCount', label: '编辑数', cell: (c) => String(c.editCount) },
  { key: 'editScope', label: '编辑范围', cell: (c) => (c.editScope ? c.editScope : '—') },
];

// ---------- Decision Pipeline 静态链路 ----------
const DECISION_PIPELINE: Array<{ title: string; description: string }> = [
  { title: '线上信号（Bad Case）', description: '从线上运行、用户反馈与巡检中发现 Bad Case 信号' },
  { title: 'Case 沉淀', description: 'Bad Case 结构化沉淀为数据集 Case，并附复现 Trace' },
  { title: '诊断归因', description: '定位失败原因：Prompt / 工具 / 知识 / 流程归因' },
  { title: '进化任务', description: '基于诊断结论创建进化任务，明确优化目标与策略' },
  { title: '候选生成', description: '优化器生成候选改进（Prompt / 配置 / 流程变更）' },
  { title: '实验评测', description: '候选在验证集 / 回归集 / 挑战集上进行离线评测' },
  { title: '质量门禁', description: 'Validation / Regression / Challenge / Safety 四类阈值' },
  { title: '决策（自动 / 人工审批）', description: '达标自动通过，边界情况进入人工审批' },
  { title: '发布新版本 / 回滚', description: '发布候选为新版本，异常时回滚至上一稳定版本' },
];

export default function Evolution() {
  const [tasks, setTasks] = useState<EvolutionTask[]>([]);
  const [runs, setRuns] = useState<EvolutionRunItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedTaskId, setSelectedTaskId] = useState<string | undefined>(undefined);
  const [candidates, setCandidates] = useState<EvolutionCandidate[]>([]);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [detail, setDetail] = useState<EvolutionTask | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listEvolutionTasks(), listEvolutionRuns()])
      .then(([t, r]) => {
        if (cancelled) return;
        setTasks(t);
        setRuns(r);
        if (t.length > 0) setSelectedTaskId(t[0].id);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedTaskId) {
      setCandidates([]);
      return;
    }
    let cancelled = false;
    setCandidatesLoading(true);
    listCandidatesByTask(selectedTaskId)
      .then((list) => {
        if (!cancelled) setCandidates(list);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setCandidatesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedTaskId]);

  const openDetail = (id: string) => {
    setDetailId(id);
    setDetail(null);
    setDetailLoading(true);
    getEvolutionTask(id)
      .then((t) => setDetail(t))
      .catch(() => setDetail(null))
      .finally(() => setDetailLoading(false));
  };

  const taskColumns: ColumnsType<EvolutionTask> = [
    {
      title: 'Name',
      dataIndex: 'name',
      key: 'name',
      render: (_, record) => (
        <Space size={4}>
          {record.name}
          {record.source === 'demo' ? <DemoTag /> : null}
        </Space>
      ),
    },
    { title: 'Target', dataIndex: 'target', key: 'target', width: 150, ellipsis: true },
    { title: 'Diagnosis', dataIndex: 'diagnosis', key: 'diagnosis', width: 150, ellipsis: true },
    { title: 'Optimizer', dataIndex: 'optimizer', key: 'optimizer', width: 150, ellipsis: true },
    {
      title: 'Status',
      key: 'status',
      width: 110,
      render: (_, record) => <StatusBadge status={record.status} />,
    },
    { title: 'Created At', key: 'createdAt', width: 170, render: (_, record) => fmtTime(record.createdAt) },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_, record) => (
        <Button type="link" size="small" style={{ padding: 0 }} onClick={() => openDetail(record.id)}>
          详情
        </Button>
      ),
    },
  ];

  const compareColumns: ColumnsType<MetricRow> = [
    { title: '指标', dataIndex: 'label', key: 'label', width: 120, fixed: 'left' },
    ...candidates.map((c) => ({
      title: c.name,
      key: c.id,
      render: (_: unknown, row: MetricRow) => row.cell(c),
    })),
  ];

  const runColumns: ColumnsType<EvolutionRunItem> = [
    { title: 'ID', key: 'id', width: 130, render: (_, record) => record.id.slice(0, 8) },
    {
      title: 'TaskID',
      key: 'taskId',
      dataIndex: 'taskId',
      width: 160,
      ellipsis: true,
      render: (_, record) => record.taskId || '—',
    },
    {
      title: 'Status',
      key: 'status',
      width: 130,
      render: (_, record) => renderRunStatus(record.status),
    },
    { title: 'Created At', key: 'createdAt', width: 170, render: (_, record) => fmtTime(record.createdAt) },
  ];

  const tabItems = [
    {
      key: 'tasks',
      label: '进化任务',
      children: (
        <>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => message.info('由诊断/Case 晋升触发，暂不开放手动创建')}
            >
              新建进化任务
            </Button>
          </div>
          <Table<EvolutionTask>
            rowKey="id"
            columns={taskColumns}
            dataSource={tasks}
            loading={loading}
            scroll={{ x: 1000 }}
            expandable={{
              expandedRowRender: (record) =>
                record.pipeline.length > 0 ? (
                  <Steps size="small" direction="horizontal" items={pipelineItems(record.pipeline)} />
                ) : (
                  <span style={{ color: '#8c8c8c' }}>暂无执行流水线信息</span>
                ),
            }}
          />
        </>
      ),
    },
    {
      key: 'candidates',
      label: '候选对比',
      children: (
        <>
          <div style={{ marginBottom: 12 }}>
            <Select
              style={{ width: 360 }}
              placeholder="请选择进化任务"
              value={selectedTaskId}
              onChange={(v) => setSelectedTaskId(v)}
              options={tasks.map((t) => ({ value: t.id, label: t.name }))}
              showSearch
              optionFilterProp="label"
            />
          </div>
          {candidates.length === 0 && !candidatesLoading ? (
            <EmptyState description="该任务暂无候选" />
          ) : (
            <Table<MetricRow>
              rowKey="key"
              columns={compareColumns}
              dataSource={METRICS}
              loading={candidatesLoading}
              pagination={false}
              scroll={{ x: 'max-content' }}
            />
          )}
        </>
      ),
    },
    {
      key: 'runs',
      label: '实验',
      children:
        runs.length === 0 && !loading ? (
          <EmptyState description="尚无实验运行，创建进化任务后自动产生" />
        ) : (
          <Table<EvolutionRunItem>
            rowKey="id"
            columns={runColumns}
            dataSource={runs}
            loading={loading}
            pagination={false}
          />
        ),
    },
    {
      key: 'pipeline',
      label: 'Decision Pipeline',
      children: (
        <>
          <Card size="small">
            <Steps
              direction="vertical"
              size="small"
              items={DECISION_PIPELINE.map((s) => ({ title: s.title, description: s.description }))}
            />
          </Card>
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 16 }}
            message="决策与版本发布接入后端 Evolution API 后自动更新"
          />
        </>
      ),
    },
  ];

  return (
    <>
      <PageHeader title="进化管理" description="诊断驱动的自进化：候选生成、实验评测与决策发布" />
      <Tabs defaultActiveKey="tasks" items={tabItems} />

      <DetailDrawer
        open={detailId !== null}
        title={detail ? `进化任务详情 · ${detail.name}` : '进化任务详情'}
        width={720}
        onClose={() => {
          setDetailId(null);
          setDetail(null);
        }}
      >
        {detailLoading ? (
          <div style={{ textAlign: 'center', padding: 48 }}>
            <Spin />
          </div>
        ) : detail ? (
          <>
            <Descriptions
              column={1}
              size="small"
              bordered
              items={[
                { key: 'id', label: 'ID', children: detail.id },
                { key: 'target', label: '目标', children: detail.target },
                { key: 'diagnosis', label: '诊断', children: detail.diagnosis },
                { key: 'optimizer', label: '优化器', children: detail.optimizer },
                { key: 'status', label: '状态', children: <StatusBadge status={detail.status} /> },
                { key: 'best', label: '最优候选', children: detail.bestCandidate ?? '—' },
                { key: 'created', label: '创建时间', children: fmtTime(detail.createdAt) },
              ]}
            />
            {detail.pipeline.length > 0 ? (
              <>
                <div style={{ margin: '16px 0 8px', fontWeight: 500 }}>执行流水线</div>
                <Steps size="small" direction="horizontal" items={pipelineItems(detail.pipeline)} />
              </>
            ) : null}
          </>
        ) : (
          <EmptyState description="未找到该进化任务" />
        )}
      </DetailDrawer>
    </>
  );
}
