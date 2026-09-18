// 用户端对话页（web-ui-spec.md §4.2）：左侧已发布 Agent 列表 + 右侧对话区。
// 发送流程在事件处理器中触发（不放 useEffect，避免 StrictMode 下重复执行）。
// 已发布且配置了 applicationId 的 Agent 走真实 Run + SSE 事件流，其余走模拟 Trace。
import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Collapse, Input, Space, Timeline, Tooltip, message } from 'antd';
import { SendOutlined } from '@ant-design/icons';
import type { Agent, Run, RuntimeEvent, TraceStep } from '../../types';
import {
  createRun,
  getRun,
  listAgents,
  openRunEventStream,
  openSimulatedTrace,
  respondRun,
} from '../../api/services';
import StatusBadge from '../../components/StatusBadge';
import EventTimeline from '../../components/EventTimeline';
import EmptyState from '../../components/EmptyState';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  steps?: TraceStep[];
  events?: RuntimeEvent[];
  runId?: string;
  status?: Run['status'];
}

// assistant 气泡：状态徽标 + Run 短号 + 可折叠执行过程 + 回复文本 + 人工审批按钮
function AssistantBubble({
  m,
  onRespond,
}: {
  m: ChatMessage;
  onRespond: (runId: string, approve: boolean) => void;
}) {
  const hasTrace = (m.steps?.length ?? 0) > 0 || (m.events?.length ?? 0) > 0;
  const runId = m.runId;
  const body =
    m.content || (m.status === 'waiting_for_human' ? '运行已暂停，请处理下方人工审批。' : '正在执行…');
  return (
    <div style={{ maxWidth: 720 }}>
      {m.status || runId ? (
        <Space size={6} style={{ marginBottom: 4 }}>
          {m.status ? <StatusBadge status={m.status} /> : null}
          {runId ? (
            <Tooltip title={runId}>
              <span
                style={{
                  fontFamily: 'SFMono-Regular, Consolas, Menlo, monospace',
                  fontSize: 12,
                  color: '#8c8c8c',
                }}
              >
                Run {runId.slice(0, 8)}
              </span>
            </Tooltip>
          ) : null}
        </Space>
      ) : null}
      {hasTrace ? (
        <Collapse
          ghost
          size="small"
          style={{ marginBottom: 4 }}
          items={[
            {
              key: 'trace',
              label: '查看执行过程',
              children: (
                <>
                  {(m.steps?.length ?? 0) > 0 ? (
                    <Timeline
                      items={(m.steps ?? []).map((s) => ({
                        children: (
                          <div>
                            <div>{s.title}</div>
                            {s.detail ? (
                              <div style={{ color: '#8c8c8c', fontSize: 12 }}>{s.detail}</div>
                            ) : null}
                          </div>
                        ),
                      }))}
                    />
                  ) : null}
                  {(m.events?.length ?? 0) > 0 ? <EventTimeline events={m.events ?? []} /> : null}
                </>
              ),
            },
          ]}
        />
      ) : null}
      <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.7 }}>{body}</div>
      {m.status === 'waiting_for_human' && runId ? (
        <Space style={{ marginTop: 8 }}>
          <Button size="small" type="primary" onClick={() => void onRespond(runId, true)}>
            同意
          </Button>
          <Button size="small" danger onClick={() => void onRespond(runId, false)}>
            拒绝
          </Button>
        </Space>
      ) : null}
    </div>
  );
}

export default function Chat() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const cancelRef = useRef<(() => void) | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    void listAgents().then(setAgents);
    return () => cancelRef.current?.();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [msgs]);

  const published = useMemo(() => agents.filter((a) => a.status === 'published'), [agents]);
  const selected = useMemo(
    () => published.find((a) => a.id === selectedId) ?? null,
    [published, selectedId]
  );
  const realMode = Boolean(selected?.applicationId);

  const send = async () => {
    const agent = selected;
    const text = input.trim();
    if (!agent || !text || sending) return;
    setInput('');
    setSending(true);

    const userMsg: ChatMessage = { role: 'user', content: text };
    const assistantIdx = msgs.length + 1;
    setMsgs((prev) => [...prev, userMsg, { role: 'assistant', content: '' }]);

    const patchMsg = (patch: Partial<ChatMessage>) =>
      setMsgs((prev) => prev.map((m, i) => (i === assistantIdx ? { ...m, ...patch } : m)));

    if (agent.applicationId) {
      // 真实模式：创建 Run 并订阅事件流
      try {
        const run = await createRun({
          applicationId: agent.applicationId,
          runtimeType: 'agent',
          input: { message: text },
        });
        patchMsg({ runId: run.id, status: run.status });
        cancelRef.current = openRunEventStream(
          run.id,
          (e) =>
            setMsgs((prev) =>
              prev.map((m, i) => {
                if (i !== assistantIdx) return m;
                if ((m.events ?? []).some((x) => x.id === e.id)) return m; // 按 id 去重
                return { ...m, events: [...(m.events ?? []), e] };
              })
            ),
          () => {
            void getRun(run.id).then((r) => {
              if (!r) {
                patchMsg({ content: '执行完成，可在管理端查看运行详情。' });
              } else if (r.status === 'failed') {
                patchMsg({
                  status: r.status,
                  content: r.error ? `执行失败：${r.error}` : '执行失败，可在管理端查看运行详情。',
                });
              } else {
                const result = r.output?.result;
                patchMsg({
                  status: r.status,
                  content:
                    typeof result === 'string' ? result : '执行完成，可在管理端查看运行详情。',
                });
              }
              setSending(false);
            });
          }
        );
      } catch {
        patchMsg({ status: 'failed', content: '创建运行失败，请检查后端服务后重试。' });
        setSending(false);
      }
      return;
    }

    // 模拟模式：可解释步骤流（最后一步 type === 'done' 时填充回答）
    cancelRef.current = openSimulatedTrace((step) => {
      if (step.type === 'done' || step.type === 'failed') setSending(false);
      setMsgs((prev) =>
        prev.map((m, i) => {
          if (i !== assistantIdx) return m;
          const content =
            step.type === 'done'
              ? '（模拟模式）已基于知识库完成回答。管理员发布该 Agent 后可接入真实运行。'
              : step.type === 'failed'
                ? '（模拟模式）执行失败，请稍后重试。'
                : m.content;
          return { ...m, steps: [...(m.steps ?? []), step], content };
        })
      );
    }, text);
  };

  const respond = async (runId: string, approve: boolean) => {
    try {
      await respondRun(
        runId,
        approve
          ? { decisions: [{ type: 'approve' }] }
          : { decisions: [{ type: 'reject', message: '用户拒绝' }] }
      );
      message.info('已提交人工响应');
      setMsgs((prev) => prev.map((m) => (m.runId === runId ? { ...m, status: 'running' } : m)));
    } catch {
      message.error('提交人工响应失败，请重试');
    }
  };

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 48px)', overflow: 'hidden' }}>
      {/* 左侧 Agent 面板 */}
      <div
        style={{
          width: 240,
          flexShrink: 0,
          background: '#fff',
          borderRight: '1px solid #f0f0f0',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div style={{ padding: '12px 16px', fontWeight: 600, borderBottom: '1px solid #f0f0f0' }}>
          Agent
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: 8 }}>
          {published.length === 0 ? (
            <EmptyState description="暂无已发布 Agent" />
          ) : (
            published.map((a) => {
              const active = a.id === selectedId;
              return (
                <div
                  key={a.id}
                  onClick={() => setSelectedId(a.id)}
                  style={{
                    padding: '8px 10px',
                    borderRadius: 8,
                    cursor: 'pointer',
                    marginBottom: 4,
                    background: active ? '#f0f5ff' : 'transparent',
                  }}
                >
                  <div style={{ fontWeight: 500 }}>{a.name}</div>
                  <div
                    style={{
                      fontSize: 12,
                      color: '#8c8c8c',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {a.description}
                  </div>
                </div>
              );
            })
          )}
        </div>
        <div style={{ padding: '8px 16px', fontSize: 12, color: '#8c8c8c', borderTop: '1px solid #f0f0f0' }}>
          仅展示已发布 Agent
        </div>
      </div>

      {/* 右侧对话区 */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {selected ? (
          <>
            <div
              style={{
                padding: '10px 20px',
                borderBottom: '1px solid #f0f0f0',
                background: '#fff',
                fontSize: 12,
                color: '#8c8c8c',
              }}
            >
              {selected.name} · 当前模式：{realMode ? '真实运行' : '模拟演示'}
            </div>
            <div style={{ flex: 1, overflow: 'auto', padding: '20px 24px' }}>
              {msgs.map((m, i) =>
                m.role === 'user' ? (
                  <div key={i} style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16 }}>
                    <div
                      style={{
                        maxWidth: 640,
                        padding: '10px 14px',
                        borderRadius: 12,
                        background: '#2f54eb',
                        color: '#fff',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        lineHeight: 1.7,
                      }}
                    >
                      {m.content}
                    </div>
                  </div>
                ) : (
                  <div key={i} style={{ display: 'flex', marginBottom: 16 }}>
                    <div
                      style={{
                        padding: '10px 14px',
                        borderRadius: 12,
                        background: '#fff',
                        border: '1px solid #f0f0f0',
                      }}
                    >
                      <AssistantBubble m={m} onRespond={(id, ok) => void respond(id, ok)} />
                    </div>
                  </div>
                )
              )}
              <div ref={bottomRef} />
            </div>
            <div
              style={{
                padding: '12px 20px',
                borderTop: '1px solid #f0f0f0',
                background: '#fff',
                display: 'flex',
                gap: 8,
                alignItems: 'flex-end',
              }}
            >
              <Input.TextArea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.nativeEvent.isComposing) return;
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                placeholder="输入消息，Enter 发送，Shift+Enter 换行"
                autoSize={{ minRows: 1, maxRows: 4 }}
              />
              <Button
                type="primary"
                icon={<SendOutlined />}
                loading={sending}
                disabled={sending || !input.trim()}
                onClick={() => void send()}
              >
                发送
              </Button>
            </div>
          </>
        ) : (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <EmptyState description="请选择一个 Agent 开始对话" />
          </div>
        )}
      </div>
    </div>
  );
}
