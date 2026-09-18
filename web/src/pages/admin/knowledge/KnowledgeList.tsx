// 知识库列表（web-ui-spec.md §10）：知识库管理入口，演示数据域。
import { useEffect, useState } from 'react';
import { Alert, Button, Space, Table, Tag, Tooltip, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { CloudUploadOutlined, FileSearchOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { listKnowledgeBases } from '../../../api/services';
import type { KnowledgeBase } from '../../../types';
import PageHeader from '../../../components/PageHeader';
import StatusBadge from '../../../components/StatusBadge';

const TIME_FMT = 'YYYY-MM-DD HH:mm:ss';

const formatTime = (value: string): string =>
  value && dayjs(value).isValid() ? dayjs(value).format(TIME_FMT) : '—';

export default function KnowledgeList() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [bases, setBases] = useState<KnowledgeBase[]>([]);

  const load = async () => {
    setLoading(true);
    try {
      setBases(await listKnowledgeBases());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const columns: ColumnsType<KnowledgeBase> = [
    {
      title: 'Name',
      dataIndex: 'name',
      width: 180,
      render: (name: string, record) => (
        <Button type="link" size="small" style={{ padding: 0 }} onClick={() => navigate(`/admin/knowledge/${record.id}`)}>
          {name}
        </Button>
      ),
    },
    { title: 'Description', dataIndex: 'description', ellipsis: true },
    { title: '文档数', dataIndex: 'documentCount', width: 80, align: 'right' },
    { title: 'Chunk 数', dataIndex: 'chunkCount', width: 90, align: 'right' },
    { title: 'Status', dataIndex: 'status', width: 100, render: (status: KnowledgeBase['status']) => <StatusBadge status={status} /> },
    {
      title: 'Permission',
      dataIndex: 'permission',
      width: 200,
      render: (permission: string[]) => {
        const shown = permission.slice(0, 2);
        const rest = permission.length - shown.length;
        return (
          <Space size={4} wrap>
            {shown.map((p) => (
              <Tag key={p}>{p}</Tag>
            ))}
            {rest > 0 ? (
              <Tooltip title={permission.slice(2).join('、')}>
                <Tag>+{rest}</Tag>
              </Tooltip>
            ) : null}
          </Space>
        );
      },
    },
    { title: 'Updated At', dataIndex: 'updatedAt', width: 170, render: formatTime },
    {
      title: '操作',
      key: 'actions',
      width: 210,
      render: (_, record) => (
        <Space size={0}>
          <Button type="link" size="small" onClick={() => navigate(`/admin/knowledge/${record.id}`)}>
            查看
          </Button>
          <Button type="link" size="small" icon={<FileSearchOutlined />} onClick={() => navigate(`/admin/knowledge/${record.id}/retrieval`)}>
            检索测试
          </Button>
          <Button type="link" size="small" danger onClick={() => message.info('演示环境不支持删除')}>
            删除
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Alert type="info" showIcon message="当前为演示数据" style={{ marginBottom: 16 }} />
      <PageHeader
        title="知识库管理"
        description="企业知识库的文档、切分与检索配置入口"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()}>
              刷新
            </Button>
            <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => message.info('演示环境暂不支持创建')}>
              新建知识库
            </Button>
          </Space>
        }
      />
      <Table<KnowledgeBase>
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={bases}
        pagination={{ showSizeChanger: false, showTotal: (total) => `共 ${total} 个知识库` }}
      />
    </div>
  );
}
