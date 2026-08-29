export type TaskStatus =
  | "CREATED"
  | "STARTING"
  | "RUNNING"
  | "WAITING_APPROVAL"
  | "CANCELLING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type StopReason =
  | "done_read_only"
  | "done_verified"
  | "done_unverified"
  | "incomplete"
  | "max_steps"
  | "stuck"
  | "denied"
  | "error";

export type ApprovalDecision = "ALLOW_ONCE" | "ALLOW_SESSION" | "DENY";
export type RiskLevel = "NORMAL" | "HIGH";

export interface TaskOptions {
  maxSteps: number;
}

export interface TaskProgress {
  step: number | null;
  maxSteps: number;
  usedTokens: number | null;
  contextLimit: number | null;
  messageCount: number | null;
}

export interface Approval {
  approvalId: string;
  taskId: string;
  status: "PENDING" | "RESOLVED";
  tool: {
    name: string;
    args: Record<string, unknown>;
  };
  riskLevel: RiskLevel;
  dangerReason: string | null;
  allowedDecisions: ApprovalDecision[];
  requestedAt: string;
  resolvedAt: string | null;
  decision: ApprovalDecision | null;
}

export interface VerificationEvidence {
  kind: string;
  command: string;
  summary: string;
  step: number;
}

export interface TaskError {
  code: string;
  message: string;
}

export interface TaskOutcome {
  success: boolean;
  stopReason: StopReason | null;
  steps: number;
  totalTokens: number;
  finalText: string;
  changedPaths: string[];
  verification: VerificationEvidence[];
  error: TaskError | null;
}

export interface TaskResource {
  schemaVersion: "1.0";
  taskId: string;
  status: TaskStatus;
  workspace: string;
  prompt: string;
  options: TaskOptions;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  progress: TaskProgress;
  pendingApproval: Approval | null;
  outcome: TaskOutcome | null;
  error: TaskError | null;
  links: {
    self: string;
    events: string;
    summary: string;
  };
}

export interface TaskSummary extends TaskOutcome {
  schemaVersion: "1.0";
  taskId: string;
  status: Extract<TaskStatus, "COMPLETED" | "FAILED" | "CANCELLED">;
  finishedAt: string;
}

export interface CreateTaskRequest {
  workspace: string;
  prompt: string;
  options: TaskOptions;
}

export type HakoEventType =
  | "run_started"
  | "turn_started"
  | "assistant_text"
  | "tool_call_started"
  | "tool_call_finished"
  | "context_stats"
  | "verification_required"
  | "continuation_required"
  | "subagent_started"
  | "subagent_finished"
  | "run_finished"
  | "agent_error"
  | "task_status"
  | "approval_required"
  | "approval_resolved"
  | "task_result"
  | "worker_error"
  | "task_cancelled"
  | "stream_gap";

export interface HakoEvent<TPayload = Record<string, unknown>> {
  schemaVersion: "1.0";
  eventId: number;
  taskId: string;
  type: HakoEventType;
  source: "HAKO" | "WORKER" | "WEB";
  occurredAt: string;
  payload: TPayload;
}

export interface HealthResponse {
  schemaVersion: "1.0";
  status: "UP" | "DOWN";
  version: string;
  worker: {
    pythonConfigured: boolean;
    entrypointReadable: boolean;
  };
}

export interface ApprovalResponse {
  schemaVersion: "1.0";
  taskId: string;
  approvalId: string;
  status: "ACCEPTED";
  decision: ApprovalDecision;
  acceptedAt: string;
}

export interface CancelResponse {
  schemaVersion: "1.0";
  taskId: string;
  status: "CANCELLING" | "CANCELLED";
  message: string;
}

export interface GatewayEventHandlers {
  onEvent: (event: HakoEvent) => void;
  onOpen?: () => void;
  onDisconnect?: () => void;
  onError?: (error: Error) => void;
}

export interface HakoGateway {
  readonly mode: "mock" | "api";
  checkHealth(): Promise<HealthResponse>;
  createTask(request: CreateTaskRequest): Promise<TaskResource>;
  getTask(taskId: string): Promise<TaskResource>;
  streamTaskEvents(taskId: string, handlers: GatewayEventHandlers): () => void;
  respondApproval(
    taskId: string,
    approvalId: string,
    decision: ApprovalDecision,
  ): Promise<ApprovalResponse>;
  cancelTask(taskId: string): Promise<CancelResponse>;
  getSummary(taskId: string): Promise<TaskSummary>;
}
