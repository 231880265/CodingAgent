export type SessionStatus = "OPENING" | "OPEN" | "CLOSING" | "CLOSED" | "FAILED";
export type RunStatus =
  | "PENDING"
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
  | "cancelled"
  | "error";

export type ApprovalDecision = "ALLOW_ONCE" | "ALLOW_SESSION" | "DENY";
export type RiskLevel = "NORMAL" | "HIGH";

export interface RunOptions {
  maxSteps: number;
}

export interface AttachmentInput {
  name: string;
  mediaType: string;
  content: string;
}

export interface AttachmentMetadata {
  name: string;
  mediaType: string;
  bytes: number;
}

export interface RunProgress {
  step: number | null;
  maxSteps: number;
  usedTokens: number | null;
  contextLimit: number | null;
  messageCount: number | null;
}

export interface Approval {
  approvalId: string;
  sessionId: string;
  runId: string;
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

export interface RunError {
  code: string;
  message: string;
}

export interface RunOutcome {
  success: boolean;
  stopReason: StopReason | null;
  steps: number;
  totalTokens: number;
  finalText: string;
  changedPaths: string[];
  verification: VerificationEvidence[];
  error: RunError | null;
}

export interface RunResource {
  runId: string;
  status: RunStatus;
  prompt: string;
  options: RunOptions;
  attachments: AttachmentMetadata[];
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  progress: RunProgress;
  pendingApproval: Approval | null;
  outcome: RunOutcome | null;
  error: RunError | null;
}

export interface SessionResource {
  schemaVersion: "1.0";
  sessionId: string;
  status: SessionStatus;
  workspace: string;
  runCount: number;
  canContinue: boolean;
  createdAt: string;
  closedAt: string | null;
  worker: {
    workerId: string;
    pid: number | null;
    alive: boolean;
    status: "NOT_STARTED" | "STARTING" | "READY" | "EXITED";
  };
  currentRun: RunResource;
  links: {
    self: string;
    events: string;
    runs: string;
    currentSummary: string | null;
  };
}

export interface RunSummary extends RunOutcome {
  schemaVersion: "1.0";
  sessionId: string;
  runId: string;
  status: Extract<RunStatus, "COMPLETED" | "FAILED" | "CANCELLED">;
  finishedAt: string;
}

export interface CreateSessionRequest {
  workspace: string;
  prompt: string;
  attachments: AttachmentInput[];
  options: RunOptions;
}

export interface CreateRunRequest {
  prompt: string;
  attachments: AttachmentInput[];
  options?: RunOptions;
}

export type HakoEventType =
  | "session_status"
  | "worker_exited"
  | "run_status"
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
  | "approval_required"
  | "approval_resolved"
  | "run_result"
  | "worker_error"
  | "run_cancelled"
  | "stream_gap";

export interface HakoEvent<TPayload = Record<string, unknown>> {
  schemaVersion: "1.0";
  eventId: number;
  sessionId: string;
  runId?: string;
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
  sessionId: string;
  runId: string;
  approvalId: string;
  status: "ACCEPTED";
  decision: ApprovalDecision;
  acceptedAt: string;
}

export interface CancelResponse {
  schemaVersion: "1.0";
  sessionId: string;
  runId: string;
  status: "CANCELLING" | "CANCELLED";
  message: string;
}

export interface SessionCloseResponse {
  schemaVersion: "1.0";
  sessionId: string;
  status: "CLOSING" | "CLOSED" | "FAILED";
}

export interface SessionHistoryItem {
  sessionId: string;
  workspace: string;
  status: SessionStatus;
  runCount: number;
  createdAt: string;
  closedAt: string | null;
  lastPrompt: string | null;
}

export interface SessionHistoryList {
  schemaVersion: "1.0";
  sessions: SessionHistoryItem[];
}

export interface SessionHistory {
  schemaVersion: "1.0";
  sessionId: string;
  workspace: string;
  status: SessionStatus;
  workerId: string;
  runCount: number;
  createdAt: string;
  closedAt: string | null;
  runs: Array<RunResource & { summary: RunSummary | null }>;
  events: HakoEvent[];
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
  createSession(request: CreateSessionRequest): Promise<SessionResource>;
  createRun(sessionId: string, request: CreateRunRequest): Promise<SessionResource>;
  getSession(sessionId: string): Promise<SessionResource>;
  streamSessionEvents(sessionId: string, handlers: GatewayEventHandlers): () => void;
  respondApproval(
    sessionId: string,
    runId: string,
    approvalId: string,
    decision: ApprovalDecision,
  ): Promise<ApprovalResponse>;
  cancelRun(sessionId: string, runId: string): Promise<CancelResponse>;
  closeSession(sessionId: string): Promise<SessionCloseResponse>;
  getRunSummary(sessionId: string, runId: string): Promise<RunSummary>;
  listSessionHistory(): Promise<SessionHistoryList>;
  getSessionHistory(sessionId: string): Promise<SessionHistory>;
}
