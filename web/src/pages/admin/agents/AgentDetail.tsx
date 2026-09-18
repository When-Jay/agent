// Agent 详情（web-ui-spec.md §7）：10 个 Tab 的完整配置视图，演示数据域。
import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Slider,
  Space,
  Spin,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ArrowLeftOutlined, PlusOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import dayjs from 'dayjs';
import {
  getAgent,
  listEvaluationTasks,
  listKnowledgeBases,
  saveAgent,
} from '../../../api/services';
import type { Agent, AgentToolRef, EvaluationTask, KnowledgeBase } from '../../../types';
import PageHeader from '../../../components/PageHeader';
import StatusBadge from '../../../components/StatusBadge';
import EmptyState from '../../../components/EmptyState';
import JSONViewer from '../../../components/JSONViewer';

const TIME_FMT = 'YYYY-MM-DD HH:mm:ss';

const formatTime = (value: string): string =>
  value && dayjs(value).isValid() ? dayjs(value).format(TIME_FMT) : '—';

const TYPE_LABELS: Record<Agent['type'], string> = { chat: '对话', task: '任务' };

/** 嵌套对象深拷贝：编辑草稿不直接改动 services 内存数据。 */
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

// 统一字段行：左侧标签 + 右侧控件。
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
      <div style={{ width: 150, flexShrink: 0, color: '#595959' }}>{label}</div>
      <div style={{ flex: 1 }}>{children}</div>
    </div>
  );
}

export default function AgentDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [agent, setAgent] = useState<Agent | null>(null);
  const [draft, setDraft] = useState<Agent | null>(null);
  const [kbOptions, setKbOptions] = useState<KnowledgeBase[]>([]);
  const [evals, setEvals] = useState<EvaluationTask[]>([]);
  const [variableInput, setVariableInput] = useState('');
  const [skillInput, setSkillInput] = useState('');

  useEffect(() => {
    if (!id) return;
    void getAgent(id).then((a) => {
      setAgent(a ? cloneAgent(a) : null);
      setDraft(a ? cloneAgent(a) : null);
      setLoading(false);
    });
  }, [id]);

  useEffect(() => {
    void listKnowledgeBases().then(setKbOptions);
  }, []);

  useEffect(() => {
    if (!agent) return;
    let cancelled = false;
    void listEvaluationTasks().then((all) => {
      if (!cancelled) setEvals(all.filter((t) => t.target.includes(agent.name)));
    });
    return () => {
      cancelled = true;
    };
  }, [agent]);

  const update = useCallback((patch: Partial<Agent>) => {
    setDraft((d) => (d ? { ...d, ...patch } : d));
  }, []);

  const handleSave = () => {
    if (!draft) return;
    const next = cloneAgent(draft);
    next.updatedAt = new Date().toISOString();
    saveAgent(next);
    setAgent(cloneAgent(next));
    message.success('已保存（演示数据，仅内存生效）');
  };

  const addVariable = () => {
    const name = variableInput.trim();
    if (!draft || !name || draft.prompt.variables.includes(name)) {
      setVariableInput('');
      return;
    }
    update({ prompt: { ...draft.prompt, variables: [...draft.prompt.variables, name] } });
    setVariableInput('');
  };

  const addSkill = () => {
    const name = skillInput.trim();
    if (!draft || !name || draft.skills.includes(name)) {
      setSkillInput('');
      return;
    }
    update({ skills: [...draft.skills, name] });
    setSkillInput('');
  };

  const updateToolPermission = (name: string, permission: AgentToolRef['permission']) => {
    if (!draft) return;
    update({ tools: draft.tools.map((t) => (t.name === name ? { ...t, permission } : t)) });
  };

  const handleRollback = (version: string) => {
    if (!draft) return;
    const next = cloneAgent(draft);
    next.version = version;
    saveAgent(next);
    setAgent(cloneAgent(next));
    setDraft(cloneAgent(next));
    message.success(`已回滚至版本 ${version}（演示数据，仅内存生效）`);
  };

  const handlePublishVersion = (version: string) => {
    if (!draft) return;
    const next = cloneAgent(draft);
    next.versions = next.versions.map((v) =>
      v.version === version ? { ...v, status: 'published' as const } : v
    );
    next.version = version;
    saveAgent(next);
    setAgent(cloneAgent(next));
    setDraft(cloneAgent(next));
    message.success(`已发布版本 ${version}（演示数据，仅内存生效）`);
  };

  const toolColumns: ColumnsType<AgentToolRef> = [
    { title: 'Name', dataIndex: 'name', width: 180 },
    {
      title: 'Type',
      dataIndex: 'type',
      width: 100,
      render: (type: AgentToolRef['type']) => (
        <Tag>{type === 'builtin' ? '内置' : 'MCP'}</Tag>
      ),
    },
    {
      title: 'Permission',
      dataIndex: 'permission',
      width: 160,
      render: (permission: AgentToolRef['permission'], record) => (
        <Select<AgentToolRef['permission']>
          value={permission}
          onChange={(v) => updateToolPermission(record.name, v)}
          style={{ width: 120 }}
          options={[
            { label: '允许', value: 'allow' },
            { label: '需确认', value: 'ask' },
            { label: '禁止', value: 'deny' },
          ]}
        />
      ),
    },
    {
      title: 'Risk',
      dataIndex: 'riskLevel',
      width: 100,
      render: (risk: AgentToolRef['riskLevel']) => <StatusBadge status={risk} />,
    },
  ];

  const evalColumns: ColumnsType<EvaluationTask> = [
    { title: 'Name', dataIndex: 'name', ellipsis: true },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 110,
      render: (status: EvaluationTask['status']) => <StatusBadge status={status} />,
    },
    {
      title: 'PassRate',
      dataIndex: 'passRate',
      width: 110,
      align: 'right',
      render: (rate: number | null) => (rate === null ? '—' : `${(rate * 100).toFixed(1)}%`),
    },
    { title: 'CreatedAt', dataIndex: 'createdAt', width: 170, render: formatTime },
  ];

  const versionColumns: ColumnsType<Agent['versions'][number]> = [
    { title: 'Version', dataIndex: 'version', width: 100 },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 100,
      render: (status: Agent['versions'][number]['status']) => <StatusBadge status={status} />,
    },
    { title: 'Created By', dataIndex: 'createdBy', width: 110 },
    { title: 'Created At', dataIndex: 'createdAt', width: 170, render: formatTime },
    {
      title: 'Evaluation',
      dataIndex: 'evaluation',
      width: 140,
      render: (evaluation?: string) => evaluation ?? '—',
    },
    {
      title: 'Released',
      dataIndex: 'released',
      width: 100,
      render: (released: boolean) =>
        released ? <Tag color="green">已发布</Tag> : <Tag>未发布</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_, record) => (
        <Space size={0}>
          <Button
            type="link"
            size="small"
            onClick={() =>
              Modal.info({
                title: `版本 ${record.version}`,
                width: 640,
                content: <JSONViewer value={record} />,
              })
            }
          >
            查看
          </Button>
          <Popconfirm
            title={`回滚至版本 ${record.version}？`}
            onConfirm={() => handleRollback(record.version)}
          >
            <Button type="link" size="small">
              回滚
            </Button>
          </Popconfirm>
          <Popconfirm
            title={`发布版本 ${record.version}？`}
            onConfirm={() => handlePublishVersion(record.version)}
          >
            <Button type="link" size="small" disabled={record.status === 'published'}>
              发布
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 64 }}>
        <Spin />
      </div>
    );
  }

  if (!agent || !draft) {
    return (
      <div>
        <PageHeader
          title="Agent 详情"
          extra={
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/admin/agents')}>
              返回列表
            </Button>
          }
        />
        <EmptyState description="未找到该 Agent" />
      </div>
    );
  }

  // 可编辑 Tab 顶部的元信息条。
  const tabMeta = (
    <div style={{ marginBottom: 16, fontSize: 12, color: '#8c8c8c' }}>
      当前版本 {agent.version} · 更新于 {formatTime(agent.updatedAt)}
    </div>
  );

  const saveButton = (
    <Button type="primary" onClick={handleSave}>
      保存
    </Button>
  );

  const items = [
    {
      key: 'info',
      label: '基本信息',
      children: (
        <Descriptions bordered size="small" column={2}>
          <Descriptions.Item label="Name">{agent.name}</Descriptions.Item>
          <Descriptions.Item label="Type">{TYPE_LABELS[agent.type] ?? agent.type}</Descriptions.Item>
          <Descriptions.Item label="Owner">{agent.owner}</Descriptions.Item>
          <Descriptions.Item label="Status">
            <StatusBadge status={agent.status} />
          </Descriptions.Item>
          <Descriptions.Item label="当前版本">{agent.version}</Descriptions.Item>
          <Descriptions.Item label="applicationId">
            {agent.applicationId ?? '—'}
          </Descriptions.Item>
          <Descriptions.Item label="Created At">{formatTime(agent.createdAt)}</Descriptions.Item>
          <Descriptions.Item label="Updated At">{formatTime(agent.updatedAt)}</Descriptions.Item>
          <Descriptions.Item label="Description" span={2}>
            {agent.description}
          </Descriptions.Item>
        </Descriptions>
      ),
    },
    {
      key: 'prompt',
      label: 'Prompt',
      children: (
        <div>
          {tabMeta}
          <Field label="System Prompt">
            <Input.TextArea
              rows={6}
              value={draft.prompt.system}
              onChange={(e) => update({ prompt: { ...draft.prompt, system: e.target.value } })}
            />
          </Field>
          <Field label="User Prompt Template">
            <Input.TextArea
              rows={4}
              value={draft.prompt.userTemplate}
              onChange={(e) =>
                update({ prompt: { ...draft.prompt, userTemplate: e.target.value } })
              }
            />
          </Field>
          <Field label="Variables">
            <div>
              <div style={{ marginBottom: 8 }}>
                {draft.prompt.variables.map((name) => (
                  <Tag
                    key={name}
                    closable
                    onClose={() =>
                      update({
                        prompt: {
                          ...draft.prompt,
                          variables: draft.prompt.variables.filter((v) => v !== name),
                        },
                      })
                    }
                  >
                    {name}
                  </Tag>
                ))}
                {draft.prompt.variables.length === 0 ? (
                  <Typography.Text type="secondary">暂无变量</Typography.Text>
                ) : null}
              </div>
              <Space>
                <Input
                  style={{ width: 220 }}
                  placeholder="变量名，如 contract_id"
                  value={variableInput}
                  onChange={(e) => setVariableInput(e.target.value)}
                  onPressEnter={addVariable}
                />
                <Button icon={<PlusOutlined />} onClick={addVariable}>
                  添加
                </Button>
              </Space>
            </div>
          </Field>
          {saveButton}
        </div>
      ),
    },
    {
      key: 'model',
      label: 'Model',
      children: (
        <div>
          {tabMeta}
          <Field label="Provider">
            <Select
              value={draft.model.provider}
              onChange={(v) => update({ model: { ...draft.model, provider: v } })}
              style={{ width: 220 }}
              options={[
                { label: 'anthropic', value: 'anthropic' },
                { label: 'openai', value: 'openai' },
                { label: 'google', value: 'google' },
              ]}
            />
          </Field>
          <Field label="Model">
            <Input
              style={{ width: 220 }}
              value={draft.model.name}
              onChange={(e) => update({ model: { ...draft.model, name: e.target.value } })}
            />
          </Field>
          <Field label={`Temperature（${draft.model.temperature}）`}>
            <Slider
              min={0}
              max={1}
              step={0.1}
              value={draft.model.temperature}
              onChange={(v) => update({ model: { ...draft.model, temperature: v } })}
              style={{ maxWidth: 320 }}
            />
          </Field>
          <Field label="Max Tokens">
            <InputNumber
              min={1}
              value={draft.model.maxTokens}
              onChange={(v) => update({ model: { ...draft.model, maxTokens: v ?? 1 } })}
            />
          </Field>
          {saveButton}
        </div>
      ),
    },
    {
      key: 'knowledge',
      label: 'Knowledge',
      children: (
        <div>
          {tabMeta}
          <Field label="知识库">
            <Checkbox.Group
              value={draft.knowledge.baseIds}
              options={kbOptions.map((kb) => ({ label: kb.name, value: kb.id }))}
              onChange={(vals) =>
                update({
                  knowledge: {
                    ...draft.knowledge,
                    baseIds: vals.filter((v): v is string => typeof v === 'string'),
                  },
                })
              }
            />
          </Field>
          <Field label="检索策略">
            <Select
              value={draft.knowledge.strategy}
              onChange={(v) => update({ knowledge: { ...draft.knowledge, strategy: v } })}
              style={{ width: 220 }}
              options={[
                { label: '向量（vector）', value: 'vector' },
                { label: '关键词（bm25）', value: 'bm25' },
                { label: '混合（hybrid）', value: 'hybrid' },
              ]}
            />
          </Field>
          <Field label="Top K">
            <InputNumber
              min={1}
              value={draft.knowledge.topK}
              onChange={(v) => update({ knowledge: { ...draft.knowledge, topK: v ?? 1 } })}
            />
          </Field>
          <Field label="Reranker">
            <Input
              style={{ width: 220 }}
              placeholder="如 bge-reranker-large，留空表示不启用"
              value={draft.knowledge.reranker}
              onChange={(e) => update({ knowledge: { ...draft.knowledge, reranker: e.target.value } })}
            />
          </Field>
          <Field label="Query Rewrite">
            <Switch
              checked={draft.knowledge.queryRewrite}
              onChange={(v) => update({ knowledge: { ...draft.knowledge, queryRewrite: v } })}
            />
          </Field>
          <Field label="Citation（引用标注）">
            <Switch
              checked={draft.knowledge.citation}
              onChange={(v) => update({ knowledge: { ...draft.knowledge, citation: v } })}
            />
          </Field>
          {saveButton}
        </div>
      ),
    },
    {
      key: 'skills',
      label: 'Skills',
      children: (
        <div>
          {tabMeta}
          <Field label="已启用 Skills">
            <div>
              <div style={{ marginBottom: 8 }}>
                {draft.skills.map((skill) => (
                  <Tag
                    key={skill}
                    closable
                    onClose={() => update({ skills: draft.skills.filter((s) => s !== skill) })}
                  >
                    {skill}
                  </Tag>
                ))}
                {draft.skills.length === 0 ? (
                  <Typography.Text type="secondary">暂无 Skill</Typography.Text>
                ) : null}
              </div>
              <Space>
                <Input
                  style={{ width: 220 }}
                  placeholder="Skill 名称"
                  value={skillInput}
                  onChange={(e) => setSkillInput(e.target.value)}
                  onPressEnter={addSkill}
                />
                <Button icon={<PlusOutlined />} onClick={addSkill}>
                  添加
                </Button>
              </Space>
            </div>
          </Field>
          {saveButton}
        </div>
      ),
    },
    {
      key: 'tools',
      label: 'Tools',
      children: (
        <div>
          {tabMeta}
          <Table<AgentToolRef>
            rowKey="name"
            size="small"
            columns={toolColumns}
            dataSource={draft.tools}
            pagination={false}
          />
          <div style={{ marginTop: 16 }}>{saveButton}</div>
        </div>
      ),
    },
    {
      key: 'memory',
      label: 'Memory',
      children: (
        <div>
          {tabMeta}
          <Field label="启用记忆">
            <Switch
              checked={draft.memory.enabled}
              onChange={(v) => update({ memory: { ...draft.memory, enabled: v } })}
            />
          </Field>
          <Field label="Scope">
            <Select
              value={draft.memory.scope}
              onChange={(v) => update({ memory: { ...draft.memory, scope: v } })}
              style={{ width: 220 }}
              options={[
                { label: '用户级（user）', value: 'user' },
                { label: '会话级（session）', value: 'session' },
              ]}
            />
          </Field>
          <Field label="Write Policy">
            <Input
              style={{ width: 320 }}
              value={draft.memory.writePolicy}
              onChange={(e) => update({ memory: { ...draft.memory, writePolicy: e.target.value } })}
            />
          </Field>
          <Field label="Read Policy">
            <Input
              style={{ width: 320 }}
              value={draft.memory.readPolicy}
              onChange={(e) => update({ memory: { ...draft.memory, readPolicy: e.target.value } })}
            />
          </Field>
          {saveButton}
        </div>
      ),
    },
    {
      key: 'runtime',
      label: 'Runtime',
      children: (
        <div>
          {tabMeta}
          <Field label="Max Turns">
            <InputNumber
              min={1}
              value={draft.runtime.maxTurns}
              onChange={(v) => update({ runtime: { ...draft.runtime, maxTurns: v ?? 1 } })}
            />
          </Field>
          <Field label="Max Tokens">
            <InputNumber
              min={1}
              value={draft.runtime.maxTokens}
              onChange={(v) => update({ runtime: { ...draft.runtime, maxTokens: v ?? 1 } })}
            />
          </Field>
          <Field label="Max Cost">
            <InputNumber
              min={0}
              step={0.5}
              value={draft.runtime.maxCost}
              onChange={(v) => update({ runtime: { ...draft.runtime, maxCost: v ?? 0 } })}
            />
          </Field>
          <Field label="Max Tool Calls">
            <InputNumber
              min={0}
              value={draft.runtime.maxToolCalls}
              onChange={(v) => update({ runtime: { ...draft.runtime, maxToolCalls: v ?? 0 } })}
            />
          </Field>
          <Field label="Max Execution Time（秒）">
            <InputNumber
              min={1}
              value={draft.runtime.maxExecutionTime}
              onChange={(v) => update({ runtime: { ...draft.runtime, maxExecutionTime: v ?? 1 } })}
            />
          </Field>
          <Field label="Checkpoint">
            <Switch
              checked={draft.runtime.checkpoint}
              onChange={(v) => update({ runtime: { ...draft.runtime, checkpoint: v } })}
            />
          </Field>
          <Field label="HITL（人工介入）">
            <Switch
              checked={draft.runtime.hitl}
              onChange={(v) => update({ runtime: { ...draft.runtime, hitl: v } })}
            />
          </Field>
          <Field label="Sandbox">
            <Switch
              checked={draft.runtime.sandbox}
              onChange={(v) => update({ runtime: { ...draft.runtime, sandbox: v } })}
            />
          </Field>
          {saveButton}
        </div>
      ),
    },
    {
      key: 'evaluation',
      label: 'Evaluation',
      children: (
        <div>
          <Alert
            type="info"
            showIcon
            message={`按 target 关联：展示 target 包含「${agent.name}」的评测任务`}
            style={{ marginBottom: 16 }}
          />
          {evals.length === 0 ? (
            <EmptyState description="暂无与该 Agent 关联的评测任务" />
          ) : (
            <Table<EvaluationTask>
              rowKey="id"
              size="small"
              columns={evalColumns}
              dataSource={evals}
              pagination={false}
            />
          )}
        </div>
      ),
    },
    {
      key: 'versions',
      label: 'Versions',
      children: (
        <Table<Agent['versions'][number]>
          rowKey="version"
          size="small"
          columns={versionColumns}
          dataSource={draft.versions}
          pagination={false}
        />
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={`${agent.name}（${agent.version}）`}
        description={agent.description}
        extra={
          <Space>
            <StatusBadge status={agent.status} />
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/admin/agents')}>
              返回列表
            </Button>
          </Space>
        }
      />
      <Tabs defaultActiveKey="info" items={items} />
    </div>
  );
}
