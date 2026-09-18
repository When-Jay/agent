// 工作流列表（web-ui-spec.md §8）：Workflow 管理入口，演示数据域。
import { useEffect, useState } from 'react';
import { Alert, Button, Progress, Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ReloadOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { listWorkflows } from '../../../api/services';
import type { Workflow } from '../../../types';
import PageHeader from '../../../components/PageHeader';
import StatusBadge from '../../../components/StatusBadge';

const TIME_FMT = 'YYYY-MM-DD HH:mm:ss';

const formatTime = (value: string): string =>
  value && dayjs(value).isValid() ? dayjs(value).format(TIME_FMT) : '—';

export default function WorkflowList() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);

  const load = async () => {
    setLoading(true);
    try {
      setWorkflows(await listWorkflows());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const columns: ColumnsType<Workflow> = [
    {
      title: 'Name',
      dataIndex: 'name',
      width: 200,
      render: (name: string, record) => (
        <Button
          type="link"
          size="small"
          style={{ padding: 0 }}
          onClick={() => navigate(`/admin/workflows/${record.id}`)}
        >
          {name}
        </Button>
      ),
    },
    { title: 'Version', dataIndex: 'version', width: 100 },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 100,
      render: (status: Workflow['status']) => <StatusBadge status={status} />,
    },
    {
      title: 'Run Count',
      dataIndex: 'runCount',
      width: 110,
      align: 'right',
    },
    {
      title: 'Success Rate',
      dataIndex: 'successRate',
      width: 180,
      render: (rate: number) => (
        <Progress percent={Math.round(rate * 100)} size="small" style={{ maxWidth: 140 }} />
      ),
    },
    { title: 'Updated At', dataIndex: 'updatedAt', width: 170, render: formatTime },
    {
      title: '操作',
      key: 'actions',
      width: 90,
      render: (_, record) => (
        <Button type="link" size="small" onClick={() => navigate(`/admin/workflows/${record.id}`)}>
          编辑
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Alert type="info" showIcon message="当前为演示数据" style={{ marginBottom: 16 }} />
      <PageHeader
        title="工作流管理"
        description="基于图的流程编排：节点、连线、状态与版本"
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
        }
      />
      <Table<Workflow>
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={workflows}
        pagination={{ showSizeChanger: false, showTotal: (total) => `共 ${total} 个工作流` }}
      />
    </div>
  );
}
