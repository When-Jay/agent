// 知识库详情（web-ui-spec.md §11）：概览 / 文档 / 数据源 / 检索 / 权限 / 版本 / 统计。
import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Descriptions,
  Divider,
  Popconfirm,
  Row,
  Col,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ArrowLeftOutlined, FileSearchOutlined, UploadOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import dayjs from 'dayjs';
import { deleteDocument, getKnowledgeBase, listDocuments } from '../../../api/services';
import type { DocumentChunk, KnowledgeBase, KnowledgeDocument } from '../../../types';
import { STATUS_LABELS } from '../../../theme';
import PageHeader from '../../../components/PageHeader';
import StatusBadge from '../../../components/StatusBadge';
import DetailDrawer from '../../../components/DetailDrawer';
import JSONViewer from '../../../components/JSONViewer';
import EmptyState from '../../../components/EmptyState';

const TIME_FMT = 'YYYY-MM-DD HH:mm:ss';

const formatTime = (value: string): string =>
  value && dayjs(value).isValid() ? dayjs(value).format(TIME_FMT) : '—';

// >1MB 显示 x.x MB，否则显示 KB
const formatSize = (size: number): string =>
  size > 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(size / 1024))} KB`;

// StatusBadge 全局语义中没有 'done'，映射到 completed 以复用统一文案
const embedBadgeStatus = (status: DocumentChunk['embeddingStatus']): string =>
  status === 'done' ? 'completed' : status;

// 权限矩阵示例（静态演示数据）
interface PermissionRow {
  key: string;
  tenant: boolean;
  dept: boolean;
  user: boolean;
  role: boolean;
  scope: string;
}
const PERMISSION_MATRIX: PermissionRow[] = [
  { key: 'p1', tenant: true, dept: true, user: false, role: true, scope: '全部文档（管理）' },
  { key: 'p2', tenant: true, dept: true, user: true, role: false, scope: '全部文档（只读）' },
  { key: 'p3', tenant: true, dept: false, user: true, role: false, scope: '指定目录（/contracts）' },
  { key: 'p4', tenant: true, dept: false, user: false, role: false, scope: '仅元数据（脱敏）' },
  { key: 'p5', tenant: false, dept: false, user: false, role: false, scope: '兜底拒绝' },
];

const grant = (ok: boolean) =>
  ok ? <Tag color="success">✓</Tag> : <Tag color="default">✕</Tag>;

export default function KnowledgeDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [kb, setKb] = useState<KnowledgeBase | null>(null);
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [drawerDoc, setDrawerDoc] = useState<KnowledgeDocument | null>(null);

  const kbId = id ?? '';

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [base, documentList] = await Promise.all([getKnowledgeBase(kbId), listDocuments(kbId)]);
      setKb(base);
      setDocs(documentList);
    } finally {
      setLoading(false);
    }
  }, [kbId]);

  useEffect(() => {
    void load();
  }, [load]);

  const reloadDocs = async () => {
    setDocs(await listDocuments(kbId));
  };

  const handleDeleteDoc = async (docId: string) => {
    deleteDocument(docId);
    message.success('已删除');
    await reloadDocs();
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin />
      </div>
    );
  }

  if (!kb) {
    return (
      <div>
        <PageHeader
          title="知识库详情"
          extra={
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/admin/knowledge')}>
              返回列表
            </Button>
          }
        />
        <EmptyState description="知识库不存在或已删除" />
      </div>
    );
  }

  // ------------------------------------------------------------------ 文档 Tab
  const docColumns: ColumnsType<KnowledgeDocument> = [
    { title: 'Name', dataIndex: 'name', ellipsis: true },
    { title: 'Size', dataIndex: 'size', width: 100, align: 'right', render: (size: number) => formatSize(size) },
    { title: 'Type', dataIndex: 'type', width: 80 },
    { title: 'Version', dataIndex: 'version', width: 90 },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 100,
      render: (status: KnowledgeDocument['status']) => <StatusBadge status={status} />,
    },
    { title: 'Chunk Count', dataIndex: 'chunkCount', width: 110, align: 'right' },
    { title: 'Updated At', dataIndex: 'updatedAt', width: 170, render: formatTime },
    {
      title: '操作',
      key: 'actions',
      width: 250,
      render: (_, record) => (
        <Space size={0}>
          <Button type="link" size="small" onClick={() => setDrawerDoc(record)}>
            详情
          </Button>
          <Popconfirm title="确认删除该文档？" onConfirm={() => void handleDeleteDoc(record.id)}>
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
          <Button type="link" size="small" onClick={() => message.success('已触发（演示）')}>
            重新解析
          </Button>
          <Button type="link" size="small" onClick={() => message.success('已触发（演示）')}>
            重新索引
          </Button>
        </Space>
      ),
    },
  ];

  const chunkColumns: ColumnsType<DocumentChunk> = [
    { title: 'Chunk ID', dataIndex: 'id', width: 120 },
    {
      title: 'Content',
      dataIndex: 'content',
      ellipsis: true,
    },
    { title: 'Parent', dataIndex: 'parent', width: 100 },
    { title: 'Anchor', dataIndex: 'anchor', width: 130 },
    {
      title: 'Metadata',
      dataIndex: 'metadata',
      width: 220,
      render: (metadata: Record<string, unknown>) => <JSONViewer value={metadata} maxHeight={140} />,
    },
    {
      title: 'Embedding Status',
      dataIndex: 'embeddingStatus',
      width: 140,
      render: (status: DocumentChunk['embeddingStatus']) => <StatusBadge status={embedBadgeStatus(status)} />,
    },
  ];

  // ------------------------------------------------------------------ 权限 Tab
  const permissionColumns: ColumnsType<PermissionRow> = [
    { title: '租户', dataIndex: 'tenant', width: 80, align: 'center', render: grant },
    { title: '部门', dataIndex: 'dept', width: 80, align: 'center', render: grant },
    { title: '用户', dataIndex: 'user', width: 80, align: 'center', render: grant },
    { title: '角色', dataIndex: 'role', width: 80, align: 'center', render: grant },
    { title: '知识范围', dataIndex: 'scope' },
  ];

  const tabItems = [
    {
      key: 'overview',
      label: '概览',
      children: (
        <Descriptions bordered size="small" column={2}>
          <Descriptions.Item label="ID">{kb.id}</Descriptions.Item>
          <Descriptions.Item label="名称">{kb.name}</Descriptions.Item>
          <Descriptions.Item label="描述" span={2}>
            {kb.description || '—'}
          </Descriptions.Item>
          <Descriptions.Item label="文档数">{kb.documentCount}</Descriptions.Item>
          <Descriptions.Item label="Chunk 数">{kb.chunkCount}</Descriptions.Item>
          <Descriptions.Item label="Status">
            <StatusBadge status={kb.status} />
          </Descriptions.Item>
          <Descriptions.Item label="Updated At">{formatTime(kb.updatedAt)}</Descriptions.Item>
          <Descriptions.Item label="Embedding Model">{kb.embeddingModel}</Descriptions.Item>
          <Descriptions.Item label="Chunk Strategy">{kb.chunkStrategy}</Descriptions.Item>
          <Descriptions.Item label="Permission" span={2}>
            <Space size={4} wrap>
              {kb.permission.map((p) => (
                <Tag key={p}>{p}</Tag>
              ))}
            </Space>
          </Descriptions.Item>
        </Descriptions>
      ),
    },
    {
      key: 'documents',
      label: '文档',
      children: (
        <div>
          <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'flex-end' }}>
            <Button type="primary" icon={<UploadOutlined />} onClick={() => message.info('演示环境暂不支持上传')}>
              上传文档
            </Button>
          </div>
          <Table<KnowledgeDocument>
            rowKey="id"
            columns={docColumns}
            dataSource={docs}
            pagination={{ showSizeChanger: false, showTotal: (total) => `共 ${total} 个文档` }}
          />
        </div>
      ),
    },
    {
      key: 'datasource',
      label: '数据源',
      children: <EmptyState description="数据源连接为保留能力，暂未开放" />,
    },
    {
      key: 'retrieval',
      label: '检索',
      children: (
        <div>
          <Descriptions bordered size="small" column={1} style={{ maxWidth: 560 }}>
            <Descriptions.Item label="Chunk Strategy">{kb.chunkStrategy}</Descriptions.Item>
            <Descriptions.Item label="Embedding Model">{kb.embeddingModel}</Descriptions.Item>
          </Descriptions>
          <Button
            type="primary"
            icon={<FileSearchOutlined />}
            style={{ marginTop: 16 }}
            onClick={() => navigate(`/admin/knowledge/${kb.id}/retrieval`)}
          >
            进入检索测试
          </Button>
        </div>
      ),
    },
    {
      key: 'permission',
      label: '权限',
      children: (
        <div>
          <Typography.Text type="secondary">示例数据（演示）</Typography.Text>
          <Table<PermissionRow>
            rowKey="key"
            size="small"
            columns={permissionColumns}
            dataSource={PERMISSION_MATRIX}
            pagination={false}
            style={{ marginTop: 8 }}
          />
        </div>
      ),
    },
    {
      key: 'version',
      label: '版本',
      children: <EmptyState description="知识库版本管理为保留能力" />,
    },
    {
      key: 'stats',
      label: '统计',
      children: (
        <Row gutter={16}>
          <Col span={6}>
            <Statistic title="文档数" value={kb.documentCount} />
          </Col>
          <Col span={6}>
            <Statistic title="Chunk 数" value={kb.chunkCount} />
          </Col>
          <Col span={6}>
            <Statistic title="状态" value={STATUS_LABELS[kb.status] ?? kb.status} />
          </Col>
          <Col span={6}>
            <Statistic title="最近更新" value={formatTime(kb.updatedAt)} />
          </Col>
        </Row>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={kb.name}
        description={kb.description}
        extra={
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/admin/knowledge')}>
            返回列表
          </Button>
        }
      />
      <Tabs items={tabItems} />

      <DetailDrawer open={drawerDoc !== null} title={drawerDoc?.name ?? '文档详情'} width={720} onClose={() => setDrawerDoc(null)}>
        {drawerDoc ? (
          <div>
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="ID">{drawerDoc.id}</Descriptions.Item>
              <Descriptions.Item label="名称">{drawerDoc.name}</Descriptions.Item>
              <Descriptions.Item label="类型">{drawerDoc.type}</Descriptions.Item>
              <Descriptions.Item label="大小">{formatSize(drawerDoc.size)}</Descriptions.Item>
              <Descriptions.Item label="Version">{drawerDoc.version}</Descriptions.Item>
              <Descriptions.Item label="Status">
                <StatusBadge status={drawerDoc.status} />
              </Descriptions.Item>
              <Descriptions.Item label="Chunk Count">{drawerDoc.chunkCount}</Descriptions.Item>
              <Descriptions.Item label="Updated At">{formatTime(drawerDoc.updatedAt)}</Descriptions.Item>
            </Descriptions>
            <Divider orientation="left" plain>
              Chunks（{drawerDoc.chunks.length}）
            </Divider>
            <Table<DocumentChunk>
              rowKey="id"
              size="small"
              columns={chunkColumns}
              dataSource={drawerDoc.chunks}
              expandable={{
                expandedRowRender: (chunk) => (
                  <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>{chunk.content}</div>
                ),
              }}
              pagination={false}
            />
            <Divider orientation="left" plain>
              处理日志
            </Divider>
            <EmptyState description="日志为保留能力" />
          </div>
        ) : null}
      </DetailDrawer>
    </div>
  );
}
