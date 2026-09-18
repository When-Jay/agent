// Agent 列表（web-ui-spec.md §6）：Agent 管理入口，演示数据域（发布时创建真实 Application）。
import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Dropdown,
  Input,
  Modal,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  message,
} from 'antd';
import type { MenuProps } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  CopyOutlined,
  EyeOutlined,
  HistoryOutlined,
  MoreOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RocketOutlined,
  StopOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { createRun, listAgents, publishAgent, saveAgent } from '../../../api/services';
import type { Agent } from '../../../types';
import PageHeader from '../../../components/PageHeader';
import StatusBadge from '../../../components/StatusBadge';

const TIME_FMT = 'YYYY-MM-DD HH:mm:ss';

const formatTime = (value: string): string =>
  value && dayjs(value).isValid() ? dayjs(value).format(TIME_FMT) : '—';

const TYPE_LABELS: Record<Agent['type'], string> = { chat: '对话', task: '任务' };

type TypeFilter = 'all' | Agent['type'];
type StatusFilter = 'all' | Agent['status'];

/** 嵌套对象深拷贝：避免编辑草稿时直接改动 services 内存数据。 */
function cloneAgent(agent: Agent): Agent {
  return {
    ...agent,
    model: { ...agent.model },
    prompt: { ...agent.prompt, variables: [...agent.prompt.variables] },
    knowledge: { ...agent.knowledge, baseIds: [...agent.knowledge.baseIds] },
    skills: [...agent.skills],
    tools: agent.tools.map((t) => ({ ...t })),
    memory: { ...agent.memory },
    runtime: { ...agent.runtime },
    versions: agent.versions.map((v) => ({ ...v })),
  };
}

export default function AgentList() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [keyword, setKeyword] = useState('');
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  const load = async () => {
    setLoading(true);
    try {
      setAgents(await listAgents());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    return agents.filter((a) => {
      if (kw && !a.name.toLowerCase().includes(kw) && !a.description.toLowerCase().includes(kw)) {
        return false;
      }
      if (typeFilter !== 'all' && a.type !== typeFilter) return false;
      if (statusFilter !== 'all' && a.status !== statusFilter) return false;
      return true;
    });
  }, [agents, keyword, typeFilter, statusFilter]);

  const handleCopy = (record: Agent) => {
    Modal.confirm({
      title: `复制 Agent「${record.name}」？`,
      content: '将创建一个草稿副本，不携带发布状态与真实 Application 关联。',
      okText: '复制',
      onOk: () => {
        const suffix = Date.now().toString(36);
        const copy = cloneAgent(record);
        copy.id = `ag-copy-${suffix}`;
        copy.name = `${record.name} 副本`;
        copy.status = 'draft';
        delete copy.applicationId;
        saveAgent(copy);
        void load();
        message.success('已创建副本');
      },
    });
  };

  const handlePublish = async (record: Agent) => {
    try {
      await publishAgent(cloneAgent(record));
      message.success('已发布：后端已创建真实 Application');
      await load();
    } catch (err) {
      message.error(err instanceof Error ? err.message : '发布失败');
    }
  };

  const handleToggleStatus = (record: Agent) => {
    const nextStatus: Agent['status'] =
      record.status === 'disabled' ? (record.applicationId ? 'published' : 'draft') : 'disabled';
    saveAgent({ ...record, status: nextStatus });
    message.success(nextStatus === 'disabled' ? '已停用' : '已启用');
    void load();
  };

  const handleRun = (record: Agent) => {
    let inputText = '';
    Modal.confirm({
      title: `运行 Agent「${record.name}」`,
      content: (
        <div style={{ marginTop: 12 }}>
          <div style={{ marginBottom: 8, color: '#8c8c8c' }}>
            {record.status !== 'published' && !record.applicationId
              ? '该 Agent 尚未发布，运行前将自动发布（在后端创建真实 Application）。'
              : '输入本次运行的消息。'}
          </div>
          <Input.TextArea
            rows={4}
            placeholder="请输入消息，例如：请审查这份合同的违约条款"
            onChange={(e) => {
              inputText = e.target.value;
            }}
          />
        </div>
      ),
      okText: '运行',
      onOk: async () => {
        const text = inputText.trim();
        if (!text) {
          message.warning('请输入运行消息');
          return;
        }
        try {
          let applicationId = record.applicationId;
          if (!applicationId) {
            const published = await publishAgent(cloneAgent(record));
            applicationId = published.applicationId;
          }
          if (!applicationId) {
            message.error('发布失败：未获取到 Application ID');
            return;
          }
          const run = await createRun({
            applicationId,
            runtimeType: 'agent',
            input: { message: text },
          });
          message.success('Run 已创建');
          navigate(`/admin/runs/${run.id}`);
        } catch (err) {
          message.error(err instanceof Error ? err.message : '运行失败');
        }
      },
    });
  };

  const buildMenu = (record: Agent): MenuProps => ({
    items: [
      { key: 'view', icon: <EyeOutlined />, label: '查看' },
      { key: 'run', icon: <PlayCircleOutlined />, label: '运行' },
      {
        key: 'publish',
        icon: <RocketOutlined />,
        label: '发布',
        disabled: record.status === 'published' || Boolean(record.applicationId),
      },
      {
        key: 'toggle',
        icon: <StopOutlined />,
        label: record.status === 'disabled' ? '启用' : '停用',
      },
      { key: 'copy', icon: <CopyOutlined />, label: '复制' },
      { key: 'runs', icon: <HistoryOutlined />, label: '运行记录' },
    ],
    onClick: ({ key }) => {
      switch (key) {
        case 'view':
          navigate(`/admin/agents/${record.id}`);
          break;
        case 'run':
          handleRun(record);
          break;
        case 'publish':
          void handlePublish(record);
          break;
        case 'toggle':
          handleToggleStatus(record);
          break;
        case 'copy':
          handleCopy(record);
          break;
        case 'runs':
          navigate('/admin/runs');
          break;
      }
    },
  });

  const columns: ColumnsType<Agent> = [
    {
      title: 'Name',
      dataIndex: 'name',
      width: 200,
      render: (name: string, record) => (
        <Button
          type="link"
          size="small"
          style={{ padding: 0 }}
          onClick={() => navigate(`/admin/agents/${record.id}`)}
        >
          {name}
        </Button>
      ),
    },
    {
      title: 'Type',
      dataIndex: 'type',
      width: 80,
      render: (type: Agent['type']) => <Tag>{TYPE_LABELS[type] ?? type}</Tag>,
    },
    { title: 'Version', dataIndex: 'version', width: 90 },
    {
      title: 'Model',
      key: 'model',
      width: 170,
      render: (_, record) => `${record.model.provider}:${record.model.name}`,
    },
    {
      title: '知识库',
      key: 'knowledge',
      width: 80,
      align: 'right',
      render: (_, record) => record.knowledge.baseIds.length,
    },
    {
      title: '工具',
      key: 'tools',
      width: 70,
      align: 'right',
      render: (_, record) => record.tools.length,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 100,
      render: (status: Agent['status']) => <StatusBadge status={status} />,
    },
    { title: 'Updated At', dataIndex: 'updatedAt', width: 170, render: formatTime },
    {
      title: '操作',
      key: 'actions',
      width: 80,
      render: (_, record) => (
        <Dropdown menu={buildMenu(record)} trigger={['click']}>
          <Button size="small" icon={<MoreOutlined />} />
        </Dropdown>
      ),
    },
  ];

  return (
    <div>
      <Alert
        type="info"
        showIcon
        message="当前为演示数据；点击发布后会在后端创建真实 Application"
        style={{ marginBottom: 16 }}
      />
      <PageHeader
        title="Agent 管理"
        description="Agent 应用的提示词、模型、知识库、工具与运行时配置"
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>
            刷新
          </Button>
        }
      />
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          placeholder="搜索名称或描述"
          allowClear
          style={{ width: 260 }}
          onSearch={setKeyword}
          onChange={(e) => {
            if (!e.target.value) setKeyword('');
          }}
        />
        <Segmented<TypeFilter>
          value={typeFilter}
          onChange={(v) => setTypeFilter(v)}
          options={[
            { label: '全部', value: 'all' },
            { label: '对话', value: 'chat' },
            { label: '任务', value: 'task' },
          ]}
        />
        <Select<StatusFilter>
          value={statusFilter}
          onChange={setStatusFilter}
          style={{ width: 140 }}
          options={[
            { label: '全部状态', value: 'all' },
            { label: '草稿', value: 'draft' },
            { label: '已发布', value: 'published' },
            { label: '已停用', value: 'disabled' },
          ]}
        />
      </Space>
      <Table<Agent>
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={filtered}
        pagination={{ showSizeChanger: false, showTotal: (total) => `共 ${total} 个 Agent` }}
      />
    </div>
  );
}
