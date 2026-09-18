// 统一服务层：页面只 import 本模块，不直接访问 client/mock。
// 策略（web-ui-spec.md §2.2）：后端已有的域接真实 API，缺失域用演示数据。
// 演示数据统一带 source: 'demo' 或 id 前缀 demo-，列表与真实数据合并展示。
import { request, ApiError } from './client';
import type {
  Agent,
  Application,
  DatasetCase,
  EvaluationTask,
  EvolutionCandidate,
  EvolutionTask,
  KnowledgeBase,
  KnowledgeDocument,
  MetricsSnapshot,
  RetrievalHit,
  Run,
  RuntimeEvent,
  Tool,
  TraceStep,
  Workflow,
} from '../types';
import {
  buildChatTrace,
  buildDemoTrace,
  demoAbTests,
  demoAgents,
  demoArtifacts,
  demoCases,
  demoCandidates,
  demoDocuments,
  demoEvaluationTasks,
  demoEvolutionTasks,
  demoKnowledgeBases,
  demoRuns,
  demoTools,
  demoWorkflows,
} from './mockData';

const isDemoRun = (id: string) => id.startsWith('demo-run-');

// ---------------------------------------------------------------------------
// 应用（真实 API）
// ---------------------------------------------------------------------------
interface RawApplication {
  id: string;
  name: string;
  metadata: Record<string, unknown>;
}

export async function listApplications(): Promise<Application[]> {
  try {
    const res = await request<{ applications: RawApplication[] }>('/applications');
    return res.applications.map((a) => ({ id: a.id, name: a.name, metadata: a.metadata ?? {} }));
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Agents（演示数据 + 发布时创建真实 application）
// ---------------------------------------------------------------------------
export async function listAgents(): Promise<Agent[]> {
  return [...demoAgents];
}

export async function getAgent(id: string): Promise<Agent | null> {
  return demoAgents.find((a) => a.id === id) ?? null;
}

export function saveAgent(agent: Agent): void {
  const idx = demoAgents.findIndex((a) => a.id === agent.id);
  if (idx >= 0) {
    demoAgents[idx] = { ...agent, updatedAt: new Date().toISOString() };
  } else {
    demoAgents.push({ ...agent, updatedAt: new Date().toISOString() });
  }
}

/** 发布 Agent：创建真实后端 application，模型配置写入 metadata（平台约定）。 */
export async function publishAgent(agent: Agent): Promise<Agent> {
  const app = await request<RawApplication>('/applications', {
    method: 'POST',
    json: {
      name: agent.name,
      metadata: {
        agent: { model: `${agent.model.provider}:${agent.model.name}`, agentId: agent.id, version: agent.version },
      },
    },
  });
  agent.applicationId = app.id;
  agent.status = 'published';
  saveAgent(agent);
  return agent;
}

// ---------------------------------------------------------------------------
// Workflows（演示数据）
// ---------------------------------------------------------------------------
export async function listWorkflows(): Promise<Workflow[]> {
  return [...demoWorkflows];
}

export async function getWorkflow(id: string): Promise<Workflow | null> {
  return demoWorkflows.find((w) => w.id === id) ?? null;
}

export function saveWorkflow(wf: Workflow): void {
  const idx = demoWorkflows.findIndex((w) => w.id === wf.id);
  if (idx >= 0) demoWorkflows[idx] = { ...wf, updatedAt: new Date().toISOString() };
}

// ---------------------------------------------------------------------------
// Knowledge（演示数据）
// ---------------------------------------------------------------------------
export async function listKnowledgeBases(): Promise<KnowledgeBase[]> {
  return [...demoKnowledgeBases];
}

export async function getKnowledgeBase(id: string): Promise<KnowledgeBase | null> {
  return demoKnowledgeBases.find((k) => k.id === id) ?? null;
}

export async function listDocuments(kbId: string): Promise<KnowledgeDocument[]> {
  return demoDocuments.filter((d) => d.kbId === kbId);
}

export async function getDocument(docId: string): Promise<KnowledgeDocument | null> {
  return demoDocuments.find((d) => d.id === docId) ?? null;
}

export function deleteDocument(docId: string): void {
  const idx = demoDocuments.findIndex((d) => d.id === docId);
  if (idx >= 0) demoDocuments.splice(idx, 1);
}

export interface RetrievalTestOptions {
  strategy: 'vector' | 'bm25' | 'hybrid';
  topK: number;
  reranker: boolean;
}

/** 检索测试（模拟打分）：关键词命中 + 确定性伪随机分，保证可复现。 */
export async function retrievalTest(
  kbId: string,
  query: string,
  opts: RetrievalTestOptions
): Promise<RetrievalHit[]> {
  const docs = demoDocuments.filter((d) => d.kbId === kbId && d.status === 'completed');
  const kw = query.trim().toLowerCase();
  const hash = (s: string) => [...s].reduce((acc, c) => (acc * 31 + c.charCodeAt(0)) % 997, 7);
  const hits: RetrievalHit[] = [];
  for (const doc of docs) {
    for (const chunk of doc.chunks) {
      const overlap = kw ? (chunk.content.toLowerCase().includes(kw) ? 0.35 : 0) : 0;
      const base = 0.35 + (hash(chunk.id + opts.strategy) % 45) / 100; // 0.35~0.80
      const score = Math.min(0.99, base + overlap);
      if (score < 0.4) continue;
      hits.push({
        rank: 0,
        document: doc.name,
        chunkId: chunk.id,
        content: chunk.content,
        retrievalScore: Number(score.toFixed(4)),
        rerankScore: null,
        metadata: { anchor: chunk.anchor, strategy: opts.strategy },
      });
    }
  }
  hits.sort((a, b) => b.retrievalScore - a.retrievalScore);
  const top = hits.slice(0, Math.max(5, opts.topK));
  if (opts.reranker) {
    // Rerank 后顺序扰动 + 生成 rerank 分
    top.sort(
      (a, b) =>
        b.retrievalScore * 0.6 + (hash(b.chunkId + 'rr') % 30) / 100 -
        (a.retrievalScore * 0.6 + (hash(a.chunkId + 'rr') % 30) / 100)
    );
    top.forEach((h, i) => {
      h.rerankScore = Number(Math.min(0.99, h.retrievalScore + 0.05 - i * 0.01).toFixed(4));
    });
  }
  top.forEach((h, i) => {
    h.rank = i + 1;
  });
  return top;
}

// ---------------------------------------------------------------------------
// Tools（真实 GET /tools + 演示元数据合并）
// ---------------------------------------------------------------------------
export async function listTools(): Promise<Tool[]> {
  let real: Array<{ name: string; description: string; parameters: Record<string, unknown> }> = [];
  try {
    const res = await request<{ tools: typeof real }>('/tools');
    real = res.tools ?? [];
  } catch {
    real = [];
  }
  const merged: Tool[] = demoTools.map((t) => {
    const found = real.find((r) => r.name === t.name);
    return found ? { ...t, description: found.description || t.description } : t;
  });
  const known = new Set(merged.map((t) => t.name));
  for (const r of real) {
    if (known.has(r.name)) continue;
    merged.push({
      name: r.name,
      provider: 'runtime',
      type: 'builtin',
      permission: 'ask',
      riskLevel: 'medium',
      status: 'enabled',
      usage: 0,
      description: r.description,
      parameters: r.parameters,
    });
  }
  return merged;
}

// ---------------------------------------------------------------------------
// Runs（真实 API 为主，合并演示 Run）
// ---------------------------------------------------------------------------
const mapRun = (r: Record<string, unknown>, nameMap: Map<string, string>): Run => ({
  id: String(r.id),
  applicationId: String(r.application_id ?? ''),
  applicationName: nameMap.get(String(r.application_id ?? '')),
  sessionId: (r.session_id as string) ?? undefined,
  runtimeType: (r.runtime_type as Run['runtimeType']) ?? 'agent',
  status: (r.status as Run['status']) ?? 'queued',
  input: (r.input as Record<string, unknown>) ?? {},
  output: (r.output as Record<string, unknown> | null) ?? null,
  error: (r.error as string | null) ?? null,
  createdAt: String(r.created_at ?? ''),
  startedAt: (r.started_at as string | null) ?? null,
  completedAt: (r.completed_at as string | null) ?? null,
});

async function applicationNameMap(): Promise<Map<string, string>> {
  const map = new Map<string, string>();
  for (const app of await listApplications()) map.set(app.id, app.name);
  return map;
}

function sortRuns(runs: Run[]): Run[] {
  return runs.sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
}

export async function listRuns(): Promise<Run[]> {
  const nameMap = await applicationNameMap();
  let real: Run[] = [];
  try {
    const res = await request<{ runs: Record<string, unknown>[] }>('/runs');
    real = res.runs.map((r) => mapRun(r, nameMap));
  } catch {
    real = [];
  }
  return sortRuns([...real, ...demoRuns]);
}

export async function listRunsByApplication(applicationId: string): Promise<Run[]> {
  const nameMap = await applicationNameMap();
  let real: Run[] = [];
  try {
    const res = await request<{ runs: Record<string, unknown>[] }>(
      `/runs?application_id=${encodeURIComponent(applicationId)}`
    );
    real = res.runs.map((r) => mapRun(r, nameMap));
  } catch {
    real = [];
  }
  return sortRuns([...real]);
}

export async function getRun(id: string): Promise<Run | null> {
  if (isDemoRun(id)) return demoRuns.find((r) => r.id === id) ?? null;
  try {
    const r = await request<Record<string, unknown>>(`/runs/${id}`);
    return mapRun(r, await applicationNameMap());
  } catch {
    return null;
  }
}

export async function listRunEvents(runId: string): Promise<RuntimeEvent[]> {
  if (isDemoRun(runId)) return [];
  try {
    const res = await request<{ events: Record<string, unknown>[] }>(`/runs/${runId}/events`);
    return res.events.map(mapEvent);
  } catch {
    return [];
  }
}

export async function listRunArtifacts(
  runId: string
): Promise<Array<{ id: string; name: string; uri: string; metadata: Record<string, unknown>; createdAt: string }>> {
  if (isDemoRun(runId)) {
    return demoArtifacts(runId).map((a) => ({
      id: a.id,
      name: a.name,
      uri: a.uri,
      metadata: a.metadata,
      createdAt: a.created_at,
    }));
  }
  try {
    const res = await request<{ artifacts: Record<string, unknown>[] }>(`/runs/${runId}/artifacts`);
    return res.artifacts.map((a) => ({
      id: String(a.id),
      name: String(a.name),
      uri: String(a.uri),
      metadata: (a.metadata as Record<string, unknown>) ?? {},
      createdAt: String(a.created_at ?? ''),
    }));
  } catch {
    return [];
  }
}

const mapEvent = (e: Record<string, unknown>): RuntimeEvent => ({
  id: String(e.id ?? ''),
  runId: String(e.run_id ?? ''),
  eventType: String(e.event_type ?? ''),
  payload: (e.payload as Record<string, unknown>) ?? {},
  createdAt: String(e.created_at ?? ''),
});

export interface CreateRunParams {
  applicationId: string;
  runtimeType: 'agent' | 'workflow';
  input: Record<string, unknown>;
  sessionId?: string;
}

export async function createRun(params: CreateRunParams): Promise<Run> {
  const r = await request<Record<string, unknown>>('/runs', {
    method: 'POST',
    json: {
      application_id: params.applicationId,
      runtime_type: params.runtimeType,
      input: params.input,
      ...(params.sessionId ? { session_id: params.sessionId } : {}),
    },
  });
  return mapRun(r, await applicationNameMap());
}

export async function cancelRun(runId: string): Promise<void> {
  await request(`/runs/${runId}/cancel`, { method: 'POST' });
}

export async function retryRun(runId: string): Promise<void> {
  await request(`/runs/${runId}/retry`, { method: 'POST' });
}

export async function respondRun(runId: string, response: unknown): Promise<void> {
  await request(`/runs/${runId}/respond`, { method: 'POST', json: { response } });
}

/** Metrics：真实快照 + 零值兜底（页面不需要感知 API 可用性）。 */
export async function getMetrics(): Promise<MetricsSnapshot> {
  const zero = { started: 0, completed: 0, failed: 0 };
  const fallback: MetricsSnapshot = {
    counters: {},
    runs: { ...zero, cancelled: 0, paused: 0, resumed: 0 },
    llm: { ...zero },
    tools: { ...zero },
    nodes: { ...zero },
    checkpoints_created: 0,
    approvals: { required: 0, received: 0 },
  };
  try {
    const m = await request<Record<string, unknown>>('/metrics');
    const sub = (k: string): Record<string, number> =>
      (m[k] as Record<string, number>) ?? {};
    return {
      counters: (m.counters as Record<string, number>) ?? {},
      runs: { ...fallback.runs, ...sub('runs') },
      llm: { ...zero, ...sub('llm') },
      tools: { ...zero, ...sub('tools') },
      nodes: { ...zero, ...sub('nodes') },
      checkpoints_created: Number(m.checkpoints_created ?? 0),
      approvals: { required: 0, received: 0, ...(m.approvals as Record<string, number>) },
    };
  } catch {
    return fallback;
  }
}

// ---------------------------------------------------------------------------
// Run 事件流：SSE（真实） / 定时注入（演示）
// ---------------------------------------------------------------------------
export type Unsubscribe = () => void;

export function openRunEventStream(
  runId: string,
  onEvent: (event: RuntimeEvent) => void,
  onDone?: () => void
): Unsubscribe {
  if (isDemoRun(runId)) {
    // 演示 Run：按 trace 脚本的时间轴注入事件
    const run = demoRuns.find((r) => r.id === runId);
    const trace: TraceStep[] = run ? buildDemoTrace(run) : buildChatTrace(runId);
    const timers = trace.map((step) =>
      window.setTimeout(() => {
        onEvent({
          id: `demo-evt-${step.time}`,
          runId,
          eventType: demoStepEventType(step),
          payload: { title: step.title, detail: step.detail, ...(step.data ?? {}) },
          createdAt: new Date().toISOString(),
        });
      }, step.time * 400)
    );
    if (onDone) timers.push(window.setTimeout(onDone, (trace.length + 1) * 400));
    return () => timers.forEach(clearTimeout);
  }
  // 真实 Run：fetch 流式解析 SSE（event/id/data 帧）
  const controller = new AbortController();
  (async () => {
    try {
      const res = await fetch(`/api/v1/runs/${runId}/events/stream`, {
        signal: controller.signal,
        headers: { Accept: 'text/event-stream' },
      });
      if (!res.ok || !res.body) throw new ApiError(res.status, res.statusText);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const data = frame
            .split('\n')
            .filter((l) => l.startsWith('data:'))
            .map((l) => l.slice(5).trim())
            .join('');
          if (!data) continue; // keep-alive 注释行
          try {
            onEvent(mapEvent(JSON.parse(data)));
          } catch {
            /* 忽略坏帧 */
          }
        }
      }
      onDone?.();
    } catch (err) {
      if (!(err instanceof DOMException && err.name === 'AbortError')) onDone?.();
    }
  })();
  return () => controller.abort();
}

function demoStepEventType(step: TraceStep): string {
  switch (step.type) {
    case 'llm': return 'LLMCompleted';
    case 'tool': return 'ToolCallCompleted';
    case 'retrieval': return 'ToolCallCompleted';
    case 'hitl': return 'ApprovalRequired';
    case 'checkpoint': return 'CheckpointCreated';
    case 'failed': return 'RunFailed';
    case 'done': return 'RunCompleted';
    default: return 'NodeCompleted';
  }
}

/** Chat 模拟模式的可解释步骤流 */
export function openSimulatedTrace(onEvent: (step: TraceStep) => void, message: string): Unsubscribe {
  const trace = buildChatTrace(message);
  const timers = trace.map((step) => window.setTimeout(() => onEvent(step), step.time * 500));
  return () => timers.forEach(clearTimeout);
}

// ---------------------------------------------------------------------------
// Evaluation（真实 + 演示合并）
// ---------------------------------------------------------------------------
const EVAL_STATUS_MAP: Record<string, EvaluationTask['status']> = {
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
};

export async function listEvaluationTasks(): Promise<EvaluationTask[]> {
  let real: EvaluationTask[] = [];
  try {
    const runs = await request<Record<string, unknown>[]>('/evaluation/runs');
    real = runs.map((r) => {
      const summary = (r.summary as Record<string, unknown>) ?? {};
      return {
        id: String(r.id),
        name: `离线评测 ${String(r.id).slice(0, 8)}`,
        target: String(r.agent_version ?? 'dev'),
        dataset: String(r.suite_id ?? '-'),
        rubric: '-',
        status: EVAL_STATUS_MAP[String(r.status ?? 'RUNNING')] ?? 'running',
        passRate: typeof summary.pass_rate === 'number' ? (summary.pass_rate as number) : null,
        createdAt: String(r.created_at ?? ''),
        results: summary as Record<string, number>,
        source: 'real' as const,
      };
    });
  } catch {
    real = [];
  }
  return [...demoEvaluationTasks, ...real];
}

export interface ABTestItem {
  id: string;
  name: string;
  applicationId: string;
  status: string;
  variants: Array<{ key: string; weight: number; agentVersion?: string }>;
  samplingRate: number;
  createdAt: string;
  source: 'real' | 'demo';
}

export async function listAbTests(): Promise<ABTestItem[]> {
  let real: ABTestItem[] = [];
  try {
    const tests = await request<Record<string, unknown>[]>('/evaluation/ab-tests');
    real = tests.map((t) => ({
      id: String(t.id),
      name: String(t.name ?? t.id),
      applicationId: String(t.application_id ?? ''),
      status: String(t.status ?? 'DRAFT'),
      variants: ((t.variants as Record<string, unknown>[]) ?? []).map((v) => ({
        key: String(v.key ?? ''),
        weight: Number(v.weight ?? 1),
        agentVersion: v.agent_version ? String(v.agent_version) : undefined,
      })),
      samplingRate: Number(t.sampling_rate ?? 1),
      createdAt: String(t.created_at ?? ''),
      source: 'real',
    }));
  } catch {
    real = [];
  }
  return [...demoAbTests, ...real];
}

const CASE_STATUS_MAP: Record<string, DatasetCase['type']> = {
  GOOD: 'good',
  BAD: 'bad',
  GOLDEN: 'golden',
  CHALLENGE: 'challenge',
  CALIBRATION: 'calibration',
  INSPECTION: 'inspection',
  UNCERTAIN: 'inspection',
  UNCOVERED: 'inspection',
};

export async function listCases(): Promise<DatasetCase[]> {
  let real: DatasetCase[] = [];
  try {
    const cases = await request<Record<string, unknown>[]>('/evaluation/cases');
    real = cases.map((c) => {
      const attribution = (c.attribution as Record<string, unknown>) ?? {};
      return {
        id: String(c.id),
        type: CASE_STATUS_MAP[String(c.type ?? 'BAD')] ?? 'bad',
        input: JSON.stringify(c.input ?? {}, null, 0),
        expected: JSON.stringify(c.expected_behavior ?? {}, null, 0),
        traceId: c.trace_id ? String(c.trace_id) : undefined,
        failureType: attribution.failure_type ? String(attribution.failure_type) : undefined,
        tags: [String(c.source ?? ''), String(c.status ?? '')].filter(Boolean),
      };
    });
  } catch {
    real = [];
  }
  return [...real, ...demoCases];
}

// ---------------------------------------------------------------------------
// Evolution（真实 + 演示合并）
// ---------------------------------------------------------------------------
const EVO_STATUS_MAP: Record<string, EvolutionTask['status']> = {
  READY: 'pending',
  PENDING: 'pending',
  RUNNING: 'running',
  IN_PROGRESS: 'running',
  COMPLETED: 'completed',
  ACCEPTED: 'completed',
  REJECTED: 'rejected',
  WAITING_APPROVAL: 'human_review',
  HUMAN_REVIEW: 'human_review',
};

interface RawEvolutionTask extends Record<string, unknown> {
  id: string;
  name?: string;
  diagnosis?: Record<string, unknown>;
  strategy?: Record<string, unknown>;
  status?: string;
  created_at?: string;
}

function mapEvolutionTask(t: RawEvolutionTask, source: 'real' | 'demo'): EvolutionTask {
  const diagnosis = (t.diagnosis ?? {}) as Record<string, unknown>;
  const strategy = (t.strategy ?? {}) as Record<string, unknown>;
  return {
    id: t.id,
    name: String(t.name ?? `进化任务 ${t.id.slice(0, 8)}`),
    target: String(diagnosis.target ?? '-'),
    diagnosis: String(diagnosis.type ?? '-'),
    optimizer: String(strategy.optimizer ?? '-'),
    status: EVO_STATUS_MAP[String(t.status ?? 'READY')] ?? 'pending',
    bestCandidate: null,
    createdAt: String(t.created_at ?? ''),
    pipeline: [],
    source,
  };
}

export async function listEvolutionTasks(): Promise<EvolutionTask[]> {
  let real: EvolutionTask[] = [];
  try {
    const tasks = await request<RawEvolutionTask[]>('/evolution/tasks');
    real = tasks.map((t) => mapEvolutionTask(t, 'real'));
  } catch {
    real = [];
  }
  return [...demoEvolutionTasks, ...real];
}

export async function getEvolutionTask(id: string): Promise<EvolutionTask | null> {
  const demo = demoEvolutionTasks.find((t) => t.id === id);
  if (demo) return demo;
  try {
    const t = await request<RawEvolutionTask>(`/evolution/tasks/${id}`);
    return mapEvolutionTask(t, 'real');
  } catch {
    return null;
  }
}

export interface EvolutionRunItem {
  id: string;
  taskId: string;
  status: string;
  createdAt: string;
  source: 'real' | 'demo';
}

export async function listEvolutionRuns(): Promise<EvolutionRunItem[]> {
  try {
    const runs = await request<Record<string, unknown>[]>('/evolution/runs');
    return runs.map((r) => ({
      id: String(r.id),
      taskId: String(r.task_id ?? ''),
      status: String(r.status ?? 'CREATED'),
      createdAt: String(r.created_at ?? ''),
      source: 'real' as const,
    }));
  } catch {
    return [];
  }
}

export async function listCandidates(evolutionRunId: string): Promise<EvolutionCandidate[]> {
  try {
    const candidates = await request<Record<string, unknown>[]>(
      `/evolution/runs/${evolutionRunId}/candidates`
    );
    return candidates.map((c, i) => ({
      id: String(c.id),
      taskId: String(c.task_id ?? ''),
      name: String(c.edit_summary || `候选 ${i + 1}`),
      scores: { validation: 0, regression: 0, challenge: 0, cost: 0, latency: 0, safety: 1 },
      editCount: Array.isArray(c.patch) ? c.patch.length : 0,
      editScope: String(c.edit_summary ?? ''),
    }));
  } catch {
    return [];
  }
}

/** 按进化任务查候选：演示任务走演示数据（真实候选挂在 EvolutionRun 下）。 */
export async function listCandidatesByTask(taskId: string): Promise<EvolutionCandidate[]> {
  const demo = demoCandidates.filter((c) => c.taskId === taskId);
  if (demo.length > 0) return demo;
  const runs = await listEvolutionRuns();
  const run = runs.find((r) => r.taskId === taskId);
  return run ? listCandidates(run.id) : [];
}
