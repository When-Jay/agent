// 运行详情（web-ui-spec.md §15）：概览 / 执行时间线 / 事件 JSON / 产物。
// 支持取消 / 重试 / HITL 人工响应，活跃 Run 实时跟踪事件流（SSE 或演示注入）。
import { useCallback, useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import {
  Alert,
  Button,
  Card,
  Divider,
  Input,
  message,
  Modal,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd';
import type { TabsProps } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ReloadOutlined, SyncOutlined } from '@ant-design/icons';
import { useParams } from 'react-router-dom';
import dayjs from 'dayjs';
import type { Run, RuntimeEvent } from '../../../types';
import {
  cancelRun,
  getRun,
  listRunArtifacts,
  listRunEvents,
  openRunEventStream,
  respondRun,
  retryRun,
} from '../../../api/services';
import StatusBadge from '../../../components/StatusBadge';
import DemoTag from '../../../components/DemoTag';
import EmptyState from '../../../components/EmptyState';
import EventTimeline from '../../../components/EventTimeline';
import JSONViewer from '../../../components/JSONViewer';
import PageHeader from '../../../components/PageHeader';

interface RunArtifact {
  id: string;
  name: string;
  uri: string;
  metadata: Record<string, unknown>;
  createdAt: string;
}

type HumanDecision = { type: 'approve' } | { type: 'reject'; message: string };

const ACTIVE_STATUSES: Run['status'][] = ['queued', 'running', 'waiting_for_human'];

const labelStyle: CSSProperties = { marginBottom: 8, fontWeight: 500 };

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

function errMsg(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失败，请稍后重试';
}

// 合并历史事件与流式事件：按 id 去重，按 createdAt 升序
function mergeEvents(prev: RuntimeEvent[], incoming: RuntimeEvent[]): RuntimeEvent[] {
  const map = new Map<string, RuntimeEvent>();
  for (const e of prev) map.set(e.id, e);
  for (const e of incoming) map.set(e.id, e);
  return Array.from(map.values()).sort((a, b) =>
    a.createdAt < b.createdAt ? -1 : a.createdAt > b.createdAt ? 1 : 0
  );
}

export default function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const [run, setRun] = useState<Run | null>(null);
  const [loading, setLoading] = useState(true);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(true);
  const [artifacts, setArtifacts] = useState<RunArtifact[]>([]);
  const [artifactsLoading, setArtifactsLoading] = useState(true);
  const [tracking, setTracking] = useState(false);
  const [acting, setActing] = useState<'approve' | 'reject' | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState('');

  const loadRun = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      setRun(await getRun(id));
    } finally {
      setLoading(false);
    }
  }, [id]);

  const loadEvents = useCallback(async () => {
    if (!id) return;
    setEventsLoading(true);
    try {
      // 演示 Run 此处返回空数组，事件全部由事件流注入，处理逻辑保持一致
      const history = await listRunEvents(id);
      setEvents((prev) => mergeEvents(prev, history));
    } finally {
      setEventsLoading(false);
    }
  }, [id]);

  const loadArtifacts = useCallback(async () => {
    if (!id) return;
    setArtifactsLoading(true);
    try {
      setArtifacts(await listRunArtifacts(id));
    } finally {
      setArtifactsLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void loadRun();
    void loadEvents();
    void loadArtifacts();
  }, [loadRun, loadEvents, loadArtifacts]);

  // 实时跟踪：活跃状态下订阅事件流；cleanup 中取消订阅（兼容 StrictMode 双挂载）
  useEffect(() => {
    if (!id || !run || !ACTIVE_STATUSES.includes(run.status)) {
      setTracking(false);
      return;
    }
    setTracking(true);
    const cancel = openRunEventStream(
      id,
      (e) =>
        setEvents((prev) =>
          prev.some((p) => p.id === e.id) ? prev : mergeEvents(prev, [e])
        ),
      () => {
        // 流结束：刷新 Run 状态
        void loadRun();
      }
    );
    return () => {
      cancel();
      setTracking(false);
    };
  }, [id, run?.id, run?.status, loadRun]);

  const showCancelConfirm = () => {
    if (!id) return;
    Modal.confirm({
      title: '确认取消该 Run？',
      content: '取消后 Run 将停止执行。',
      okText: '确认取消',
      okButtonProps: { danger: true },
      cancelText: '返回',
      onOk: async () => {
        try {
          await cancelRun(id);
          message.success('已发起取消');
          await loadRun();
        } catch (err) {
          message.error(errMsg(err));
        }
      },
    });
  };

  const showRetryConfirm = () => {
    if (!id) return;
    Modal.confirm({
      title: '确认重试该 Run？',
      content: '将基于该 Run 重新发起执行。',
      okText: '确认重试',
      cancelText: '返回',
      onOk: async () => {
        try {
          await retryRun(id);
          message.success('已发起重试');
          await loadRun();
        } catch (err) {
          message.error(errMsg(err));
        }
      },
    });
  };

  const handleApprove = async () => {
    if (!id) return;
    setActing('approve');
    try {
      const payload: { decisions: HumanDecision[] } = { decisions: [{ type: 'approve' }] };
      await respondRun(id, payload);
      message.success('已提交同意响应');
      await loadRun();
    } catch (err) {
      message.error(errMsg(err));
    } finally {
      setActing(null);
    }
  };

  const handleReject = async () => {
    if (!id) return;
    setActing('reject');
    try {
      const payload: { decisions: HumanDecision[] } = {
        decisions: [{ type: 'reject', message: rejectReason.trim() }],
      };
      await respondRun(id, payload);
      message.success('已提交拒绝响应');
      setRejectOpen(false);
      setRejectReason('');
      await loadRun();
    } catch (err) {
      message.error(errMsg(err));
    } finally {
      setActing(null);
    }
  };

  if (loading && !run) {
    return (
      <Card>
        <div style={{ padding: 48, textAlign: 'center' }}>
          <Spin />
        </div>
      </Card>
    );
  }

  if (!run) {
    return (
      <Card>
        <EmptyState description="未找到该 Run（可能不存在或已被删除）" />
      </Card>
    );
  }

  const artifactColumns: ColumnsType<RunArtifact> = [
    { title: 'Name', dataIndex: 'name', width: 180, ellipsis: true },
    {
      title: 'URI',
      dataIndex: 'uri',
      render: (v: string) => (
        <Typography.Text copyable={{ text: v }} style={{ maxWidth: 360 }} ellipsis>
          {v}
        </Typography.Text>
      ),
    },
    {
      title: 'Metadata',
      dataIndex: 'metadata',
      width: 300,
      render: (v: Record<string, unknown>) => <JSONViewer value={v} maxHeight={160} />,
    },
    {
      title: 'Created At',
      dataIndex: 'createdAt',
      width: 170,
      render: (v: string) => fmtTime(v),
    },
  ];

  const items: TabsProps['items'] = [
    {
      key: 'overview',
      label: '概览',
      children: (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {run.error ? (
            <Alert type="error" showIcon message="执行失败" description={run.error} />
          ) : null}
          <div>
            <div style={labelStyle}>输入</div>
            <JSONViewer value={run.input} />
          </div>
          <div>
            <div style={labelStyle}>输出</div>
            <JSONViewer value={run.output} />
          </div>
        </div>
      ),
    },
    {
      key: 'timeline',
      label: '执行时间线',
      children: (
        <div>
          <Space wrap size={12} style={{ marginBottom: 16 }}>
            <span style={{ color: '#595959' }}>已加载 {events.length} 个事件</span>
            {tracking ? (
              <Tag color="processing" icon={<SyncOutlined spin />}>
                实时跟踪中
              </Tag>
            ) : null}
            {eventsLoading ? <Spin size="small" /> : null}
          </Space>
          <EventTimeline events={events} />
        </div>
      ),
    },
    {
      key: 'events-json',
      label: '事件 JSON',
      children: <JSONViewer value={events} maxHeight={480} />,
    },
    {
      key: 'artifacts',
      label: '产物',
      children: (
        <Table<RunArtifact>
          rowKey="id"
          size="small"
          loading={artifactsLoading}
          columns={artifactColumns}
          dataSource={artifacts}
          pagination={false}
          locale={{ emptyText: <EmptyState description="暂无产物" /> }}
        />
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={run.id}
        extra={
          <Space wrap>
            <Button
              icon={<ReloadOutlined />}
              loading={loading}
              onClick={() => {
                void loadRun();
                void loadEvents();
              }}
            >
              刷新
            </Button>
            {run.status === 'queued' ? (
              <Button danger onClick={showCancelConfirm}>
                取消
              </Button>
            ) : null}
            {run.status === 'failed' || run.status === 'cancelled' ? (
              <Button onClick={showRetryConfirm}>重试</Button>
            ) : null}
          </Space>
        }
      />

      {/* 标题副行：Run ID 复制 / 状态 / 类型 / 应用 */}
      <Space wrap size={8} align="center" style={{ marginBottom: 8 }}>
        <Typography.Text copyable={{ text: run.id, tooltips: '复制 Run ID' }} style={{ fontSize: 14 }} />
        <StatusBadge status={run.status} />
        <Tag>{run.runtimeType === 'workflow' ? 'Workflow' : 'Agent'}</Tag>
        <span style={{ fontWeight: 500 }}>{run.applicationName ?? run.applicationId ?? '—'}</span>
        {run.source === 'demo' ? <DemoTag /> : null}
      </Space>

      {/* 元信息行 */}
      <div style={{ color: '#8c8c8c', fontSize: 12, marginBottom: 16 }}>
        <Space wrap size={4} split={<Divider type="vertical" />}>
          <span>Session：{run.sessionId ?? '—'}</span>
          <span>创建：{fmtTime(run.createdAt)}</span>
          <span>开始：{fmtTime(run.startedAt)}</span>
          <span>结束：{fmtTime(run.completedAt)}</span>
          <span>耗时：{fmtDuration(run.startedAt, run.completedAt)}</span>
        </Space>
      </div>

      {/* HITL：等待人工响应 */}
      {run.status === 'waiting_for_human' ? (
        <>
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="该 Run 暂停等待人工响应（Agent 审批或 Workflow 人工节点）"
          />
          <Card size="small" style={{ marginBottom: 16 }} title="人工响应">
            <Space wrap>
              <Button
                type="primary"
                loading={acting === 'approve'}
                disabled={acting === 'reject'}
                onClick={() => void handleApprove()}
              >
                同意
              </Button>
              <Button
                danger
                disabled={acting === 'approve'}
                onClick={() => setRejectOpen((v) => !v)}
              >
                {rejectOpen ? '收起' : '拒绝'}
              </Button>
            </Space>
            {rejectOpen ? (
              <div style={{ marginTop: 12 }}>
                <Input.TextArea
                  rows={2}
                  maxLength={500}
                  showCount
                  value={rejectReason}
                  placeholder="请填写拒绝原因"
                  onChange={(e) => setRejectReason(e.target.value)}
                />
                <Space style={{ marginTop: 8 }}>
                  <Button
                    danger
                    type="primary"
                    loading={acting === 'reject'}
                    disabled={acting === 'approve'}
                    onClick={() => void handleReject()}
                  >
                    确认拒绝
                  </Button>
                </Space>
              </div>
            ) : null}
          </Card>
        </>
      ) : null}

      <Card>
        <Tabs items={items} />
      </Card>
    </div>
  );
}
