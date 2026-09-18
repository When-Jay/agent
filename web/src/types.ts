// 平台领域类型（web-ui-spec.md）。仅描述 UI 需要的形状。

export type RunStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'waiting_for_human';

export interface Run {
  id: string;
  applicationId: string;
  applicationName?: string;
  sessionId?: string;
  runtimeType: 'agent' | 'workflow';
  status: RunStatus;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  error: string | null;
  createdAt: string;
  startedAt: string | null;
  completedAt: string | null;
  /** demo 标记：来自 Mock 演示数据，仅用于页面展示 */
  source?: 'real' | 'demo';
}

export interface RuntimeEvent {
  id: string;
  runId: string;
  eventType: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

export interface Application {
  id: string;
  name: string;
  metadata: Record<string, unknown>;
}

export interface AgentVersion {
  version: string;
  status: 'draft' | 'published' | 'archived';
  createdBy: string;
  createdAt: string;
  evaluation?: string;
  released: boolean;
}

export interface AgentToolRef {
  name: string;
  type: 'builtin' | 'mcp';
  permission: 'allow' | 'ask' | 'deny';
  riskLevel: 'low' | 'medium' | 'high';
}

export interface Agent {
  id: string;
  name: string;
  description: string;
  type: 'chat' | 'task';
  status: 'draft' | 'published' | 'disabled';
  version: string;
  owner: string;
  createdAt: string;
  updatedAt: string;
  model: { provider: string; name: string; temperature: number; maxTokens: number };
  prompt: { system: string; userTemplate: string; variables: string[] };
  knowledge: {
    baseIds: string[];
    strategy: 'vector' | 'bm25' | 'hybrid';
    topK: number;
    reranker: string;
    queryRewrite: boolean;
    citation: boolean;
  };
  skills: string[];
  tools: AgentToolRef[];
  memory: {
    enabled: boolean;
    scope: 'user' | 'session';
    writePolicy: string;
    readPolicy: string;
  };
  runtime: {
    maxTurns: number;
    maxTokens: number;
    maxCost: number;
    maxToolCalls: number;
    maxExecutionTime: number;
    checkpoint: boolean;
    hitl: boolean;
    sandbox: boolean;
  };
  versions: AgentVersion[];
  /** 发布后对应真实后端 application 的 id（可空：仅演示数据） */
  applicationId?: string;
}

export interface WorkflowNode {
  id: string;
  name: string;
  type:
    | 'start'
    | 'llm'
    | 'agent'
    | 'knowledge'
    | 'tool'
    | 'condition'
    | 'parallel'
    | 'human'
    | 'end';
  config: Record<string, unknown>;
}

export interface Workflow {
  id: string;
  name: string;
  version: string;
  status: 'draft' | 'published' | 'disabled';
  runCount: number;
  successRate: number;
  updatedAt: string;
  description: string;
  nodes: WorkflowNode[];
  edges: Array<[string, string]>;
}

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  documentCount: number;
  chunkCount: number;
  status: 'active' | 'indexing' | 'error';
  permission: string[];
  updatedAt: string;
  embeddingModel: string;
  chunkStrategy: string;
}

export interface DocumentChunk {
  id: string;
  content: string;
  parent: string;
  anchor: string;
  metadata: Record<string, unknown>;
  embeddingStatus: 'done' | 'pending' | 'failed';
}

export interface KnowledgeDocument {
  id: string;
  kbId: string;
  name: string;
  size: number;
  type: string;
  version: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  chunkCount: number;
  updatedAt: string;
  chunks: DocumentChunk[];
}

export interface RetrievalHit {
  rank: number;
  document: string;
  chunkId: string;
  content: string;
  retrievalScore: number;
  rerankScore: number | null;
  metadata: Record<string, unknown>;
}

export interface Tool {
  name: string;
  provider: string;
  type: 'builtin' | 'mcp';
  permission: 'allow' | 'ask' | 'deny';
  riskLevel: 'low' | 'medium' | 'high';
  status: 'enabled' | 'disabled';
  usage: number;
  description: string;
  server?: string;
  rateLimit?: string;
  parameters?: Record<string, unknown>;
}

export interface RubricCriterion {
  name: string;
  definition: string;
  positiveExample: string;
  negativeExample: string;
  scoringRule: string;
  evidenceRequirement: string;
  threshold: number;
}

export interface Rubric {
  id: string;
  name: string;
  dimensions: Array<{ name: string; criteria: RubricCriterion[] }>;
}

export interface DatasetCase {
  id: string;
  type: 'good' | 'bad' | 'golden' | 'challenge' | 'calibration' | 'inspection';
  input: string;
  expected: string;
  traceId?: string;
  failureType?: string;
  tags: string[];
}

export interface EvaluationTask {
  id: string;
  name: string;
  target: string;
  dataset: string;
  rubric: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  passRate: number | null;
  createdAt: string;
  results: Record<string, number>;
  source?: 'real' | 'demo';
}

export interface EvolutionTask {
  id: string;
  name: string;
  target: string;
  diagnosis: string;
  optimizer: string;
  status: 'pending' | 'running' | 'completed' | 'rejected' | 'human_review';
  bestCandidate: string | null;
  createdAt: string;
  pipeline: Array<{ stage: string; status: 'done' | 'active' | 'pending' | 'failed' }>;
  source?: 'real' | 'demo';
}

export interface EvolutionCandidate {
  id: string;
  taskId: string;
  name: string;
  scores: {
    validation: number;
    regression: number;
    challenge: number;
    cost: number; // 相对基线变化（负值=下降）
    latency: number;
    safety: number;
  };
  editCount: number;
  editScope: string;
}

export interface MetricsSnapshot {
  counters: Record<string, number>;
  runs: { started: number; completed: number; failed: number; cancelled: number; paused: number; resumed: number };
  llm: { started: number; completed: number; failed: number };
  tools: { started: number; completed: number; failed: number };
  nodes: { started: number; completed: number; failed: number };
  checkpoints_created: number;
  approvals: { required: number; received: number };
}

/** 演示 Trace：Chat 模拟运行 / demo run 详情使用 */
export interface TraceStep {
  time: number; // 相对秒
  type: 'llm' | 'tool' | 'retrieval' | 'hitl' | 'node' | 'checkpoint' | 'done' | 'failed';
  title: string;
  detail?: string;
  data?: Record<string, unknown>;
}
