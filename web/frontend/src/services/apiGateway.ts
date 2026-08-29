import type {
  ApprovalDecision,
  ApprovalResponse,
  CancelResponse,
  CreateTaskRequest,
  GatewayEventHandlers,
  HakoEvent,
  HakoEventType,
  HakoGateway,
  HealthResponse,
  TaskResource,
  TaskSummary,
} from "../types/api";

const SSE_EVENT_TYPES: HakoEventType[] = [
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
  "task_status",
  "approval_required",
  "approval_resolved",
  "task_result",
  "worker_error",
  "task_cancelled",
  "stream_gap",
];

const TERMINAL_EVENT_TYPES = new Set<HakoEventType>([
  "task_result",
  "worker_error",
  "task_cancelled",
]);

interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
  };
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

  createTask(request: CreateTaskRequest): Promise<TaskResource> {
    return this.request<TaskResource>("/tasks", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  getTask(taskId: string): Promise<TaskResource> {
    return this.request<TaskResource>(`/tasks/${encodeURIComponent(taskId)}`);
  }

  streamTaskEvents(taskId: string, handlers: GatewayEventHandlers): () => void {
    const source = new EventSource(
      `${this.baseUrl}/tasks/${encodeURIComponent(taskId)}/events`,
    );
    const delivered = new Set<number>();

    const receive = (message: MessageEvent<string>) => {
      try {
        const event = JSON.parse(message.data) as HakoEvent;
        if (delivered.has(event.eventId)) {
          return;
        }
        delivered.add(event.eventId);
        handlers.onEvent(event);
        if (TERMINAL_EVENT_TYPES.has(event.type)) {
          source.close();
        }
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
    taskId: string,
    approvalId: string,
    decision: ApprovalDecision,
  ): Promise<ApprovalResponse> {
    return this.request<ApprovalResponse>(
      `/tasks/${encodeURIComponent(taskId)}/approvals/${encodeURIComponent(approvalId)}`,
      {
        method: "POST",
        body: JSON.stringify({ decision }),
      },
    );
  }

  cancelTask(taskId: string): Promise<CancelResponse> {
    return this.request<CancelResponse>(
      `/tasks/${encodeURIComponent(taskId)}/cancel`,
      { method: "POST" },
    );
  }

  getSummary(taskId: string): Promise<TaskSummary> {
    return this.request<TaskSummary>(
      `/tasks/${encodeURIComponent(taskId)}/summary`,
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
        if (payload.error?.message) {
          message = payload.error.message;
        }
      } catch {
        // HTTP 状态仍然足以生成稳定错误；不再猜测非 JSON 正文。
      }
      throw new Error(message);
    }

    return (await response.json()) as T;
  }
}
