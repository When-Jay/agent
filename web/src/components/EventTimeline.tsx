import { Timeline, Tooltip } from 'antd';
import {
  ApiOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  ExclamationCircleOutlined,
  PlayCircleOutlined,
  RobotOutlined,
  StopOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { RuntimeEvent } from '../types';

// 事件类型 → 中文文案（平台事件清单见 runtime-core 事件枚举）。
const EVENT_META: Record<string, { label: string; color: string; icon: React.ReactNode }> = {
  RunStarted: { label: 'Run 开始', color: 'blue', icon: <PlayCircleOutlined /> },
  RunResumed: { label: 'Run 恢复', color: 'blue', icon: <PlayCircleOutlined /> },
  RunPaused: { label: 'Run 暂停', color: 'orange', icon: <ClockCircleOutlined /> },
  RunCompleted: { label: 'Run 完成', color: 'green', icon: <CheckCircleOutlined /> },
  RunFailed: { label: 'Run 失败', color: 'red', icon: <ExclamationCircleOutlined /> },
  RunCancelled: { label: 'Run 取消', color: 'gray', icon: <StopOutlined /> },
  TurnStarted: { label: '轮次开始', color: 'blue', icon: <RobotOutlined /> },
  TurnCompleted: { label: '轮次完成', color: 'green', icon: <RobotOutlined /> },
  DecisionCreated: { label: '决策产生', color: 'blue', icon: <RobotOutlined /> },
  LLMStarted: { label: 'LLM 调用开始', color: 'blue', icon: <RobotOutlined /> },
  LLMCompleted: { label: 'LLM 调用完成', color: 'green', icon: <RobotOutlined /> },
  LLMFailed: { label: 'LLM 调用失败', color: 'red', icon: <RobotOutlined /> },
  ToolCallStarted: { label: '工具调用开始', color: 'blue', icon: <ApiOutlined /> },
  ToolCallCompleted: { label: '工具调用完成', color: 'green', icon: <ApiOutlined /> },
  ToolCallFailed: { label: '工具调用失败', color: 'red', icon: <ApiOutlined /> },
  NodeStarted: { label: '节点开始', color: 'blue', icon: <ApiOutlined /> },
  NodeCompleted: { label: '节点完成', color: 'green', icon: <ApiOutlined /> },
  NodeFailed: { label: '节点失败', color: 'red', icon: <ApiOutlined /> },
  CheckpointCreated: { label: 'Checkpoint 保存', color: 'purple', icon: <DatabaseOutlined /> },
  ApprovalRequired: { label: '需要人工审批', color: 'orange', icon: <UserOutlined /> },
  HumanResponseReceived: { label: '收到人工响应', color: 'green', icon: <UserOutlined /> },
  SteeringReceived: { label: '收到引导指令', color: 'orange', icon: <UserOutlined /> },
  SteeringInjected: { label: '引导指令注入', color: 'orange', icon: <UserOutlined /> },
  SteeringConsumed: { label: '引导指令消费', color: 'green', icon: <UserOutlined /> },
  BudgetSoftLimit: { label: '预算软限制', color: 'orange', icon: <ClockCircleOutlined /> },
  BudgetFinishing: { label: '预算即将耗尽', color: 'orange', icon: <ClockCircleOutlined /> },
  BudgetHardLimit: { label: '预算硬限制', color: 'red', icon: <ClockCircleOutlined /> },
  BudgetExhausted: { label: '预算耗尽', color: 'red', icon: <ClockCircleOutlined /> },
};

function payloadSummary(payload: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(payload)) {
    const s = typeof v === 'string' ? v : JSON.stringify(v);
    if (s && s.length <= 120) parts.push(`${k}=${s}`);
  }
  return parts.join('　');
}

// 执行事件时间线：Run Detail / Chat 的执行过程展示。
export default function EventTimeline({ events }: { events: RuntimeEvent[] }) {
  if (events.length === 0) {
    return <div style={{ color: '#8c8c8c', padding: 24, textAlign: 'center' }}>暂无事件</div>;
  }
  return (
    <Timeline
      items={events.map((e) => {
        const meta = EVENT_META[e.eventType] ?? { label: e.eventType, color: 'blue', icon: <ApiOutlined /> };
        const summary = payloadSummary(e.payload);
        return {
          color: meta.color,
          dot: meta.icon,
          children: (
            <div>
              <div>
                <b>{meta.label}</b>
                <span style={{ color: '#8c8c8c', marginLeft: 8, fontSize: 12 }}>
                  {e.createdAt ? new Date(e.createdAt).toLocaleTimeString('zh-CN') : ''}
                </span>
              </div>
              {summary ? (
                <Tooltip title={JSON.stringify(e.payload)}>
                  <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 2, wordBreak: 'break-all' }}>{summary}</div>
                </Tooltip>
              ) : null}
            </div>
          ),
        };
      })}
    />
  );
}
