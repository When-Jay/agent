// 用户端任务页：最近任务列表（前 20 条），点击查看输入/输出详情。
import { useCallback, useEffect, useState } from 'react';
import { Card, List, Space, Typography } from 'antd';
import dayjs from 'dayjs';
import type { Run } from '../../types';
import { listRuns } from '../../api/services';
import StatusBadge from '../../components/StatusBadge';
import DetailDrawer from '../../components/DetailDrawer';
import EmptyState from '../../components/EmptyState';
import JSONViewer from '../../components/JSONViewer';
import PageHeader from '../../components/PageHeader';

const fmtTime = (v: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '—');

const MONO = { fontFamily: 'SFMono-Regular, Consolas, Menlo, monospace', fontSize: 12 };

// 输入摘要：优先 input.message，否则 JSON 截短 40 字
function inputSummary(run: Run): string {
  const msg = run.input['message'];
  if (typeof msg === 'string' && msg) return msg;
  const json = JSON.stringify(run.input ?? {});
  return json.length > 40 ? `${json.slice(0, 40)}…` : json;
}

export default function UserTasks() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Run | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const list = await listRuns();
      setRuns(list.slice(0, 20));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <PageHeader title="我的任务" description="展示与你相关的最近任务（演示环境未做用户隔离）" />
      <Card style={{ borderRadius: 10 }}>
        <List
          loading={loading}
          dataSource={runs}
          locale={{ emptyText: <EmptyState description="暂无任务" /> }}
          renderItem={(run) => (
            <List.Item
              style={{ cursor: 'pointer' }}
              actions={[
                <Space key="meta" size={12}>
                  <span style={{ color: '#8c8c8c', fontSize: 12 }}>{fmtTime(run.createdAt)}</span>
                  <StatusBadge status={run.status} />
                </Space>,
              ]}
              onClick={() => setSelected(run)}
            >
              <List.Item.Meta
                title={run.applicationName ?? run.applicationId ?? '未命名应用'}
                description={inputSummary(run)}
              />
            </List.Item>
          )}
        />
      </Card>
      <DetailDrawer
        open={selected !== null}
        title={selected ? `任务 ${selected.id.slice(0, 8)}` : ''}
        onClose={() => setSelected(null)}
      >
        {selected ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Space size={12} wrap>
              <StatusBadge status={selected.status} />
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
          </Space>
        ) : null}
      </DetailDrawer>
    </div>
  );
}
