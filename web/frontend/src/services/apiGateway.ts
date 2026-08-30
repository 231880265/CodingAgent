import type {
  ApprovalDecision,
  ApprovalResponse,
  CancelResponse,
  CreateRunRequest,
  CreateSessionRequest,
  GatewayEventHandlers,
  HakoEvent,
  HakoEventType,
  HakoGateway,
  HealthResponse,
  RunSummary,
  SessionCloseResponse,
  SessionHistory,
  SessionHistoryList,
  SessionResource,
} from "../types/api";

const SSE_EVENT_TYPES: HakoEventType[] = [
  "session_status",
  "worker_exited",
  "run_status",
  "run_started",
  "turn_started",
  "assistant_text",
  "tool_call_started",
  "tool_call_finished",
  "context_stats",
  "verification_required",
  "continuation_required",
  "subagent_started",
  "subagent_finished",
  "run_finished",
  "agent_error",
  "approval_required",
  "approval_resolved",
  "run_result",
  "worker_error",
  "run_cancelled",
  "stream_gap",
];

interface ErrorEnvelope {
  error?: { code?: string; message?: string };
}

export class ApiGateway implements HakoGateway {
  readonly mode = "api" as const;
  private readonly baseUrl: string;

  constructor(baseUrl = "/api/v1") {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  checkHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>("/health");
  }

  createSession(request: CreateSessionRequest): Promise<SessionResource> {
    return this.request<SessionResource>("/sessions", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  createRun(sessionId: string, request: CreateRunRequest): Promise<SessionResource> {
    return this.request<SessionResource>(
      `/sessions/${encodeURIComponent(sessionId)}/runs`,
      { method: "POST", body: JSON.stringify(request) },
    );
  }

  getSession(sessionId: string): Promise<SessionResource> {
    return this.request<SessionResource>(`/sessions/${encodeURIComponent(sessionId)}`);
  }

  streamSessionEvents(sessionId: string, handlers: GatewayEventHandlers): () => void {
    const source = new EventSource(
      `${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}/events`,
    );
    const delivered = new Set<number>();
    const receive = (message: MessageEvent<string>) => {
      try {
        const event = JSON.parse(message.data) as HakoEvent;
        if (event.sessionId !== sessionId || delivered.has(event.eventId)) return;
        delivered.add(event.eventId);
        handlers.onEvent(event);
      } catch (error) {
        handlers.onError?.(
          error instanceof Error ? error : new Error("无法解析 SSE 事件。"),
        );
      }
    };
    for (const type of SSE_EVENT_TYPES) {
      source.addEventListener(type, receive as EventListener);
    }
    source.onopen = () => handlers.onOpen?.();
    source.onerror = () => handlers.onDisconnect?.();
    return () => source.close();
  }

  respondApproval(
    sessionId: string,
    runId: string,
    approvalId: string,
    decision: ApprovalDecision,
  ): Promise<ApprovalResponse> {
    return this.request<ApprovalResponse>(
      `/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}`
        + `/approvals/${encodeURIComponent(approvalId)}`,
      { method: "POST", body: JSON.stringify({ decision }) },
    );
  }

  cancelRun(sessionId: string, runId: string): Promise<CancelResponse> {
    return this.request<CancelResponse>(
      `/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/cancel`,
      { method: "POST" },
    );
  }

  closeSession(sessionId: string): Promise<SessionCloseResponse> {
    return this.request<SessionCloseResponse>(
      `/sessions/${encodeURIComponent(sessionId)}/close`,
      { method: "POST" },
    );
  }

  getRunSummary(sessionId: string, runId: string): Promise<RunSummary> {
    return this.request<RunSummary>(
      `/sessions/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}/summary`,
    );
  }

  listSessionHistory(): Promise<SessionHistoryList> {
    return this.request<SessionHistoryList>("/sessions");
  }

  getSessionHistory(sessionId: string): Promise<SessionHistory> {
    return this.request<SessionHistory>(
      `/sessions/${encodeURIComponent(sessionId)}/history`,
    );
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
    if (!response.ok) {
      let message = `请求失败（HTTP ${response.status}）。`;
      try {
        const payload = (await response.json()) as ErrorEnvelope;
        if (payload.error?.message) message = payload.error.message;
      } catch {
        // HTTP 状态仍足以形成稳定错误。
      }
      throw new Error(message);
    }
    return (await response.json()) as T;
  }
}
