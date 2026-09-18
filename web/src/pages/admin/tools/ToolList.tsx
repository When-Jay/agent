import { useEffect, useMemo, useState } from 'react';
import { Button, Card, Descriptions, Input, Segmented, Space, Table, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { SearchOutlined } from '@ant-design/icons';
import DetailDrawer from '../../../components/DetailDrawer';
import EmptyState from '../../../components/EmptyState';
import JSONViewer from '../../../components/JSONViewer';
import PageHeader from '../../../components/PageHeader';
import StatusBadge from '../../../components/StatusBadge';
import { listTools } from '../../../api/services';
import type { Tool } from '../../../types';

type ToolTypeFilter = 'all' | 'builtin' | 'mcp';

/** services 合并真实 GET /tools 时会附带 parameters 字段；UI 按可选字段读取 */
type ToolWithParameters = Tool & { parameters?: Record<string, unknown> };

function typeLabel(type: Tool['type']): string {
  return type === 'mcp' ? 'MCP' : 'Built-in';
}

export default function ToolList() {
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<ToolTypeFilter>('all');
  const [detail, setDetail] = useState<Tool | null>(null);

  useEffect(() => {
    let alive = true;
    listTools().then((list) => {
      if (!alive) return;
      setTools(list);
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, []);

  const filtered = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return tools.filter((tool) => {
      const matchKeyword =
        !keyword ||
        tool.name.toLowerCase().includes(keyword) ||
        tool.description.toLowerCase().includes(keyword);
      const matchType = typeFilter === 'all' || tool.type === typeFilter;
      return matchKeyword && matchType;
    });
  }, [tools, search, typeFilter]);

  const columns: ColumnsType<Tool> = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 180,
      render: (name: string) => <code style={{ fontSize: 12 }}>{name}</code>,
    },
    {
      title: '类型',
      key: 'type',
      width: 92,
      render: (_, tool) => (
        <Tag style={{ marginRight: 0 }} color={tool.type === 'mcp' ? 'geekblue' : 'default'}>
          {typeLabel(tool.type)}
        </Tag>
      ),
    },
    {
      title: '权限',
      key: 'permission',
      width: 92,
      render: (_, tool) => <StatusBadge status={tool.permission} />,
    },
    {
      title: '风险等级',
      key: 'riskLevel',
      width: 92,
      render: (_, tool) => <StatusBadge status={tool.riskLevel} />,
    },
    {
      title: '状态',
      key: 'status',
      width: 92,
      render: (_, tool) => <StatusBadge status={tool.status} />,
    },
    {
      title: '调用次数',
      dataIndex: 'usage',
      key: 'usage',
      width: 96,
      align: 'right',
      render: (usage: number) => usage.toLocaleString(),
    },
    {
      title: 'Provider',
      dataIndex: 'provider',
      key: 'provider',
      width: 110,
      render: (provider: string) => provider || '—',
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (description: string) => description || '—',
    },
    {
      title: '操作',
      key: 'action',
      width: 64,
      render: (_, tool) => (
        <Button type="link" size="small" style={{ padding: 0 }} onClick={() => setDetail(tool)}>
          详情
        </Button>
      ),
    },
  ];

  const detailParams: Record<string, unknown> | undefined = detail
    ? (detail as ToolWithParameters).parameters
    : undefined;

  return (
    <div>
      <PageHeader
        title="MCP / Tools"
        description="内置工具与 MCP 工具的注册、权限与风险治理"
      />

      <Card size="small">
        <Space wrap style={{ marginBottom: 16 }}>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索名称 / 描述"
            style={{ width: 280 }}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <Segmented
            value={typeFilter}
            onChange={(value) => setTypeFilter(value as ToolTypeFilter)}
            options={[
              { label: '全部', value: 'all' },
              { label: 'Built-in', value: 'builtin' },
              { label: 'MCP', value: 'mcp' },
            ]}
          />
        </Space>

        <Table<Tool>
          rowKey="name"
          size="small"
          columns={columns}
          dataSource={filtered}
          loading={loading}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          scroll={{ x: 960 }}
        />
      </Card>

      <DetailDrawer
        open={detail !== null}
        title={detail ? `工具详情：${detail.name}` : '工具详情'}
        onClose={() => setDetail(null)}
      >
        {detail ? (
          <>
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="名称">{detail.name}</Descriptions.Item>
              <Descriptions.Item label="类型">
                <Tag style={{ marginRight: 0 }} color={detail.type === 'mcp' ? 'geekblue' : 'default'}>
                  {typeLabel(detail.type)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="权限">
                <StatusBadge status={detail.permission} />
              </Descriptions.Item>
              <Descriptions.Item label="风险等级">
                <StatusBadge status={detail.riskLevel} />
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <StatusBadge status={detail.status} />
              </Descriptions.Item>
              <Descriptions.Item label="调用次数">{detail.usage.toLocaleString()}</Descriptions.Item>
              <Descriptions.Item label="Provider">{detail.provider || '—'}</Descriptions.Item>
              <Descriptions.Item label="MCP Server">{detail.server ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="限流">{detail.rateLimit ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="描述">{detail.description || '—'}</Descriptions.Item>
            </Descriptions>
            <div style={{ marginTop: 16 }}>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>参数定义</div>
              {detailParams ? (
                <JSONViewer value={detailParams} maxHeight={360} />
              ) : (
                <EmptyState description="该工具未声明参数" />
              )}
            </div>
          </>
        ) : null}
      </DetailDrawer>
    </div>
  );
}
