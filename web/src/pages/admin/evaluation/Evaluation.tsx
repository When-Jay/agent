// 评测管理（/admin/evaluation）：评测任务 / 数据集 / Rubric / Cases / Online AB。
import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Row,
  Segmented,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import dayjs from 'dayjs';
import { useNavigate } from 'react-router-dom';
import DemoTag from '../../../components/DemoTag';
import DetailDrawer from '../../../components/DetailDrawer';
import EmptyState from '../../../components/EmptyState';
import JSONViewer from '../../../components/JSONViewer';
import PageHeader from '../../../components/PageHeader';
import StatusBadge from '../../../components/StatusBadge';
import {
  listAbTests,
  listCases,
  listEvaluationTasks,
  type ABTestItem,
} from '../../../api/services';
import type { DatasetCase, EvaluationTask } from '../../../types';

// ---------- 展示辅助 ----------
function fmtTime(s: string): string {
  const d = dayjs(s);
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : '—';
}

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

function truncate(s: string, max = 60): string {
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

/** 输入是 JSON 字符串时先 parse 便于结构化展示；解析失败则原样返回。 */
function tryParseJson(s: string): unknown {
  try {
    return JSON.parse(s) as unknown;
  } catch {
    return s;
  }
}

// ---------- Rubric 静态示例 ----------
const RUBRIC_EXAMPLES: Array<{ name: string; dimensions: Array<{ name: string; criteria: number }> }> = [
  {
    name: '合同审查 Rubric',
    dimensions: [
      { name: '任务成功', criteria: 4 },
      { name: '引用准确', criteria: 3 },
    ],
  },
  {
    name: '客服应答 Rubric',
    dimensions: [
      { name: '应答正确', criteria: 3 },
      { name: '安全', criteria: 2 },
    ],
  },
];

type CaseFilter = 'all' | DatasetCase['type'];

const CASE_FILTERS: Array<{ label: string; value: CaseFilter }> = [
  { label: '全部', value: 'all' },
  { label: 'GOLDEN', value: 'golden' },
  { label: 'GOOD', value: 'good' },
  { label: 'BAD', value: 'bad' },
  { label: '挑战', value: 'challenge' },
  { label: '校准', value: 'calibration' },
];

const CASE_TYPE_STATS: Array<{ key: DatasetCase['type']; label: string }> = [
  { key: 'good', label: 'Good' },
  { key: 'bad', label: 'Bad' },
  { key: 'golden', label: 'Golden' },
  { key: 'challenge', label: 'Challenge' },
  { key: 'calibration', label: '校准' },
  { key: 'inspection', label: '巡检' },
];

export default function Evaluation() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<EvaluationTask[]>([]);
  const [cases, setCases] = useState<DatasetCase[]>([]);
  const [abTests, setAbTests] = useState<ABTestItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [caseFilter, setCaseFilter] = useState<CaseFilter>('all');
  const [caseDetail, setCaseDetail] = useState<DatasetCase | null>(null);
  const [abReport, setAbReport] = useState<ABTestItem | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listEvaluationTasks(), listCases(), listAbTests()])
      .then(([t, c, a]) => {
        if (cancelled) return;
        setTasks(t);
        setCases(c);
        setAbTests(a);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filteredCases = useMemo(
    () => (caseFilter === 'all' ? cases : cases.filter((c) => c.type === caseFilter)),
    [cases, caseFilter]
  );

  const caseCounts = useMemo(() => {
    const m: Record<DatasetCase['type'], number> = {
      good: 0,
      bad: 0,
      golden: 0,
      challenge: 0,
      calibration: 0,
      inspection: 0,
    };
    for (const c of cases) m[c.type] += 1;
    return m;
  }, [cases]);

  const taskColumns: ColumnsType<EvaluationTask> = [
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
    { title: 'Dataset', dataIndex: 'dataset', key: 'dataset', width: 150, ellipsis: true },
    { title: 'Rubric', dataIndex: 'rubric', key: 'rubric', width: 150, ellipsis: true },
    {
      title: 'Status',
      key: 'status',
      width: 110,
      render: (_, record) => <StatusBadge status={record.status} />,
    },
    {
      title: 'Pass Rate',
      key: 'passRate',
      width: 110,
      render: (_, record) => (record.passRate === null ? '—' : pct(record.passRate)),
    },
    { title: 'Created At', key: 'createdAt', width: 170, render: (_, record) => fmtTime(record.createdAt) },
  ];

  const caseColumns: ColumnsType<DatasetCase> = [
    {
      title: 'Type',
      key: 'type',
      width: 110,
      render: (_, record) => <StatusBadge status={record.type} />,
    },
    {
      title: 'Input',
      key: 'input',
      render: (_, record) => (
        <Tooltip title={record.input} placement="topLeft">
          <span>{truncate(record.input)}</span>
        </Tooltip>
      ),
    },
    {
      title: 'Expected',
      key: 'expected',
      render: (_, record) => (
        <Tooltip title={record.expected} placement="topLeft">
          <span>{truncate(record.expected)}</span>
        </Tooltip>
      ),
    },
    {
      title: 'Trace ID',
      key: 'traceId',
      width: 190,
      ellipsis: true,
      render: (_, record) => {
        const tid = record.traceId;
        if (tid && tid.startsWith('demo-run-')) {
          return (
            <Typography.Link onClick={() => navigate(`/admin/runs/${tid}`)}>{tid}</Typography.Link>
          );
        }
        return tid ?? '—';
      },
    },
    {
      title: 'Failure Type',
      key: 'failureType',
      width: 130,
      ellipsis: true,
      render: (_, record) => record.failureType ?? '—',
    },
    {
      title: 'Tags',
      key: 'tags',
      width: 180,
      render: (_, record) =>
        record.tags.length > 0 ? (
          <Space size={4} wrap>
            {record.tags.map((t) => (
              <Tag key={t}>{t}</Tag>
            ))}
          </Space>
        ) : (
          '—'
        ),
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_, record) => (
        <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setCaseDetail(record)}>
          详情
        </Button>
      ),
    },
  ];

  const abColumns: ColumnsType<ABTestItem> = [
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
    {
      title: 'Application',
      dataIndex: 'applicationId',
      key: 'applicationId',
      width: 160,
      ellipsis: true,
    },
    {
      title: 'Status',
      key: 'status',
      width: 110,
      render: (_, record) => (
        <Tag color={record.status === 'RUNNING' ? 'processing' : 'default'}>{record.status}</Tag>
      ),
    },
    {
      title: 'Variants',
      key: 'variants',
      render: (_, record) =>
        record.variants.length > 0
          ? record.variants.map((v) => `${v.key} ${pct(v.weight)}`).join(', ')
          : '—',
    },
    {
      title: 'Sampling Rate',
      key: 'samplingRate',
      width: 120,
      render: (_, record) => pct(record.samplingRate),
    },
    { title: 'Created At', key: 'createdAt', width: 170, render: (_, record) => fmtTime(record.createdAt) },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_, record) => (
        <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setAbReport(record)}>
          实验报告
        </Button>
      ),
    },
  ];

  const tabItems = [
    {
      key: 'tasks',
      label: '评测任务',
      children: (
        <>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => message.info('需先创建 Suite/Application 绑定，演示环境暂未开放')}
            >
              新建评测
            </Button>
          </div>
          <Table<EvaluationTask>
            rowKey="id"
            columns={taskColumns}
            dataSource={tasks}
            loading={loading}
            scroll={{ x: 1000 }}
            expandable={{
              expandedRowRender: (record) => (
                <div>
                  <div style={{ marginBottom: 8, fontWeight: 500 }}>评测结果</div>
                  <JSONViewer value={record.results} />
                </div>
              ),
            }}
          />
        </>
      ),
    },
    {
      key: 'dataset',
      label: '数据集',
      children: (
        <>
          <Row gutter={16}>
            {CASE_TYPE_STATS.map((it) => (
              <Col span={4} key={it.key}>
                <Card size="small">
                  <Statistic title={it.label} value={caseCounts[it.key]} />
                </Card>
              </Col>
            ))}
          </Row>
          <div style={{ marginTop: 24 }}>
            <EmptyState description="Suite/Asset 数据集管理为保留能力，暂未开放" />
          </div>
        </>
      ),
    },
    {
      key: 'rubric',
      label: 'Rubric',
      children: (
        <Row gutter={16}>
          {RUBRIC_EXAMPLES.map((r) => (
            <Col span={12} key={r.name}>
              <Card size="small" title={r.name} extra={<Tag>示例（演示）</Tag>}>
                {r.dimensions.map((d) => (
                  <div
                    key={d.name}
                    style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}
                  >
                    <span>{d.name}</span>
                    <span style={{ color: '#8c8c8c' }}>{d.criteria} 条 criteria</span>
                  </div>
                ))}
              </Card>
            </Col>
          ))}
        </Row>
      ),
    },
    {
      key: 'cases',
      label: 'Cases',
      children: (
        <>
          <div style={{ marginBottom: 12 }}>
            <Segmented
              options={CASE_FILTERS}
              value={caseFilter}
              onChange={(v) => setCaseFilter(v as CaseFilter)}
            />
          </div>
          <Table<DatasetCase>
            rowKey="id"
            columns={caseColumns}
            dataSource={filteredCases}
            loading={loading}
            scroll={{ x: 1100 }}
          />
        </>
      ),
    },
    {
      key: 'ab',
      label: 'Online AB',
      children: (
        <Table<ABTestItem>
          rowKey="id"
          columns={abColumns}
          dataSource={abTests}
          loading={loading}
          scroll={{ x: 1000 }}
        />
      ),
    },
  ];

  return (
    <>
      <PageHeader title="评测管理" description="离线评测、数据集与在线实验的质量管理入口" />
      <Tabs defaultActiveKey="tasks" items={tabItems} />

      <DetailDrawer
        open={caseDetail !== null}
        title={caseDetail ? `Case 详情 · ${caseDetail.id}` : 'Case 详情'}
        onClose={() => setCaseDetail(null)}
      >
        {caseDetail ? (
          <>
            <Descriptions
              column={1}
              size="small"
              bordered
              items={[
                { key: 'type', label: 'Type', children: <StatusBadge status={caseDetail.type} /> },
                { key: 'failure', label: 'Failure Type', children: caseDetail.failureType ?? '—' },
                {
                  key: 'tags',
                  label: 'Tags',
                  children:
                    caseDetail.tags.length > 0 ? (
                      <Space size={4} wrap>
                        {caseDetail.tags.map((t) => (
                          <Tag key={t}>{t}</Tag>
                        ))}
                      </Space>
                    ) : (
                      '—'
                    ),
                },
                { key: 'trace', label: 'Trace ID', children: caseDetail.traceId ?? '—' },
              ]}
            />
            <div style={{ margin: '16px 0 8px', fontWeight: 500 }}>Input</div>
            <JSONViewer value={tryParseJson(caseDetail.input)} />
            <div style={{ margin: '16px 0 8px', fontWeight: 500 }}>Expected</div>
            <JSONViewer value={tryParseJson(caseDetail.expected)} />
          </>
        ) : null}
      </DetailDrawer>

      <DetailDrawer
        open={abReport !== null}
        title={abReport ? `实验报告 · ${abReport.name}` : '实验报告'}
        onClose={() => setAbReport(null)}
      >
        {abReport ? (
          <>
            <Alert
              type="info"
              showIcon
              message="真实实验报告 API 未接入，以下为实验配置预览。"
              style={{ marginBottom: 16 }}
            />
            <Descriptions
              column={1}
              size="small"
              bordered
              items={[
                { key: 'app', label: 'Application', children: abReport.applicationId },
                { key: 'status', label: 'Status', children: abReport.status },
                { key: 'sampling', label: 'Sampling Rate', children: pct(abReport.samplingRate) },
                { key: 'created', label: 'Created At', children: fmtTime(abReport.createdAt) },
              ]}
            />
            <div style={{ margin: '16px 0 8px', fontWeight: 500 }}>变体列表</div>
            {abReport.variants.length > 0 ? (
              abReport.variants.map((v) => (
                <div
                  key={v.key}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    padding: '6px 0',
                    borderBottom: '1px solid #f0f0f0',
                  }}
                >
                  <span>
                    {v.key}
                    {v.agentVersion ? `（${v.agentVersion}）` : ''}
                  </span>
                  <span style={{ color: '#8c8c8c' }}>流量 {pct(v.weight)}</span>
                </div>
              ))
            ) : (
              <EmptyState description="该实验暂无变体" />
            )}
            <div style={{ marginTop: 16, color: '#8c8c8c', fontSize: 12 }}>
              提示：演示实验仅用于展示分流配置结构，不代表线上真实流量数据。
            </div>
          </>
        ) : null}
      </DetailDrawer>
    </>
  );
}
