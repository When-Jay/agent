// 工作流编辑器（web-ui-spec.md §9）：V1 提供只读结构视图 + 节点参数编辑，演示数据域。
import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Input,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd';
import { ArrowDownOutlined, ArrowRightOutlined } from '@ant-design/icons';
import { useParams } from 'react-router-dom';
import { getWorkflow, saveWorkflow } from '../../../api/services';
import type { Workflow, WorkflowNode } from '../../../types';
import PageHeader from '../../../components/PageHeader';
import StatusBadge from '../../../components/StatusBadge';
import DetailDrawer from '../../../components/DetailDrawer';
import EmptyState from '../../../components/EmptyState';

const NODE_TYPE_LABELS: Record<WorkflowNode['type'], string> = {
  start: '开始',
  llm: 'LLM',
  agent: 'Agent',
  knowledge: '知识检索',
  tool: '工具',
  condition: '条件',
  parallel: '并行',
  human: '人工审批',
  end: '结束',
};

const NODE_TYPE_COLORS: Record<WorkflowNode['type'], string> = {
  start: 'blue',
  llm: 'purple',
  agent: 'geekblue',
  knowledge: 'cyan',
  tool: 'orange',
  condition: 'gold',
  parallel: 'magenta',
  human: 'volcano',
  end: 'green',
};

/** 版本号末段 +1：v2.1.0 → v2.1.1。 */
function bumpVersion(version: string): string {
  const parts = version.split('.');
  const last = parts[parts.length - 1];
  const num = Number.parseInt(last, 10);
  if (Number.isNaN(num)) return version;
  parts[parts.length - 1] = String(num + 1);
  return parts.join('.');
}

/** config 摘要：一行展示键值对，超长截断。 */
function summarizeConfig(config: Record<string, unknown>): string {
  const entries = Object.entries(config);
  if (entries.length === 0) return '无配置';
  const text = entries
    .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join('；');
  return text.length > 60 ? `${text.slice(0, 60)}…` : text;
}

export default function WorkflowEditor() {
  const { id } = useParams<{ id: string }>();
  const [loading, setLoading] = useState(true);
  const [wf, setWf] = useState<Workflow | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [nameDraft, setNameDraft] = useState('');
  const [configText, setConfigText] = useState('');

  useEffect(() => {
    if (!id) return;
    void getWorkflow(id).then((w) => {
      setWf(w);
      setLoading(false);
    });
  }, [id]);

  const selectedNode = useMemo(
    () => wf?.nodes.find((n) => n.id === selectedId) ?? null,
    [wf, selectedId]
  );

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 64 }}>
        <Spin />
      </div>
    );
  }

  if (!wf) {
    return (
      <div>
        <PageHeader title="工作流编辑器" />
        <EmptyState description="未找到该工作流" />
      </div>
    );
  }

  const nodeNameOf = (nodeId: string): string =>
    wf.nodes.find((n) => n.id === nodeId)?.name ?? nodeId;

  const openNode = (node: WorkflowNode) => {
    setSelectedId(node.id);
    setNameDraft(node.name);
    setConfigText(JSON.stringify(node.config, null, 2));
  };

  const handleSaveNode = () => {
    if (!selectedNode) return;
    let config: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(configText);
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        message.error('JSON 格式错误');
        return;
      }
      config = parsed as Record<string, unknown>;
    } catch {
      message.error('JSON 格式错误');
      return;
    }
    const name = nameDraft.trim() || selectedNode.name;
    setWf({
      ...wf,
      nodes: wf.nodes.map((n) => (n.id === selectedNode.id ? { ...n, name, config } : n)),
    });
    message.success('节点已更新（需点击「保存版本」持久化）');
    setSelectedId(null);
  };

  const handleSaveVersion = () => {
    const newVersion = bumpVersion(wf.version);
    const next = { ...wf, version: newVersion };
    saveWorkflow(next);
    setWf(next);
    message.success(`已保存版本 ${newVersion}（演示数据，仅内存生效）`);
  };

  return (
    <div>
      <Alert
        type="info"
        showIcon
        message="V1 提供只读结构视图 + 节点参数编辑；复杂拖拽 Builder 为后续版本"
        style={{ marginBottom: 16 }}
      />
      <PageHeader
        title={`${wf.name}（${wf.version}）`}
        description={wf.description}
        extra={
          <Space>
            <StatusBadge status={wf.status} />
            <Button type="primary" onClick={handleSaveVersion}>
              保存版本
            </Button>
          </Space>
        }
      />
      <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>
        {/* DAG 纵向只读视图：按 nodes 数组顺序排列，节点间以箭头连接。 */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1 }}>
          {wf.nodes.map((node, idx) => (
            <div key={node.id} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <Card
                size="small"
                hoverable
                onClick={() => openNode(node)}
                style={{ width: 480 }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Typography.Text strong>{node.name}</Typography.Text>
                  <Tag color={NODE_TYPE_COLORS[node.type]}>
                    {NODE_TYPE_LABELS[node.type] ?? node.type}
                  </Tag>
                </div>
                <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 4 }}>
                  {summarizeConfig(node.config)}
                </div>
              </Card>
              {idx < wf.nodes.length - 1 ? (
                <ArrowDownOutlined style={{ margin: '6px 0', color: '#8c8c8c' }} />
              ) : null}
            </div>
          ))}
        </div>
        {/* 连线列表 */}
        <Card size="small" title="连线" style={{ width: 320, flexShrink: 0 }}>
          {wf.edges.length === 0 ? (
            <Typography.Text type="secondary">暂无连线</Typography.Text>
          ) : (
            wf.edges.map(([from, to]) => (
              <div key={`${from}->${to}`} style={{ marginBottom: 6, fontSize: 13 }}>
                {nodeNameOf(from)} <ArrowRightOutlined style={{ color: '#8c8c8c' }} />{' '}
                {nodeNameOf(to)}
              </div>
            ))
          )}
        </Card>
      </div>
      <DetailDrawer
        open={selectedId !== null}
        title={selectedNode ? `编辑节点：${selectedNode.name}` : '编辑节点'}
        onClose={() => setSelectedId(null)}
      >
        {selectedNode ? (
          <div>
            <div style={{ marginBottom: 12 }}>
              <Typography.Text type="secondary" style={{ marginRight: 8 }}>
                节点类型
              </Typography.Text>
              <Tag color={NODE_TYPE_COLORS[selectedNode.type]}>
                {NODE_TYPE_LABELS[selectedNode.type] ?? selectedNode.type}
              </Tag>
            </div>
            <div style={{ marginBottom: 16 }}>
              <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>
                节点名称
              </Typography.Text>
              <Input
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                placeholder="节点名称"
              />
            </div>
            <div style={{ marginBottom: 16 }}>
              <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>
                节点参数（JSON）
              </Typography.Text>
              <Input.TextArea
                rows={12}
                value={configText}
                onChange={(e) => setConfigText(e.target.value)}
                style={{ fontFamily: 'monospace', fontSize: 12 }}
              />
            </div>
            <Button type="primary" onClick={handleSaveNode}>
              保存节点
            </Button>
          </div>
        ) : null}
      </DetailDrawer>
    </div>
  );
}
