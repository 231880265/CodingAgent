import { computed, onBeforeUnmount, ref } from "vue";
import { gateway, gatewayMode } from "../services/gateway";
import type {
  Approval,
  ApprovalDecision,
  CreateTaskRequest,
  HakoEvent,
  TaskError,
  TaskOutcome,
  TaskResource,
  TaskStatus,
  TaskSummary,
} from "../types/api";

const ACTIVE_STATES = new Set<TaskStatus>([
  "CREATED",
  "STARTING",
  "RUNNING",
  "WAITING_APPROVAL",
  "CANCELLING",
]);

export function useTaskController() {
  const task = ref<TaskResource | null>(null);
  const events = ref<HakoEvent[]>([]);
  const summary = ref<TaskSummary | null>(null);
  const model = ref<string | null>(null);
  const connection = ref<"CHECKING" | "UP" | "DOWN">("CHECKING");
  const streamConnected = ref(false);
  const actionPending = ref(false);
  const approvalPending = ref(false);
  const errorMessage = ref("");
  const receivedEventIds = new Set<number>();
  let closeStream: (() => void) | null = null;

  const status = computed<TaskStatus | "IDLE">(
    () => task.value?.status ?? "IDLE",
  );
  const isActive = computed(() =>
    task.value ? ACTIVE_STATES.has(task.value.status) : false,
  );
  const pendingApproval = computed<Approval | null>(
    () => task.value?.pendingApproval ?? null,
  );
  const contextPercent = computed(() => {
    const progress = task.value?.progress;
    if (!progress?.usedTokens || !progress.contextLimit) return 0;
    return Math.min(100, (progress.usedTokens / progress.contextLimit) * 100);
  });

  async function initialize(): Promise<void> {
    try {
      const health = await gateway.checkHealth();
      connection.value = health.status;
    } catch (error) {
      connection.value = "DOWN";
      errorMessage.value = toMessage(error);
      return;
    }
    if (gatewayMode === "api") {
      const taskId = new URL(window.location.href).searchParams.get("task");
      if (taskId) {
        try {
          await restoreTask(taskId);
        } catch (error) {
          errorMessage.value = toMessage(error);
        }
      }
    }
  }

  async function startTask(request: CreateTaskRequest): Promise<void> {
    actionPending.value = true;
    errorMessage.value = "";
    closeStream?.();
    closeStream = null;
    streamConnected.value = false;
    receivedEventIds.clear();
    events.value = [];
    summary.value = null;
    model.value = null;

    try {
      const created = await gateway.createTask(request);
      task.value = created;
      rememberTaskInUrl(created.taskId);
      connectStream(created.taskId);
    } catch (error) {
      errorMessage.value = toMessage(error);
    } finally {
      actionPending.value = false;
    }
  }

  async function resolveApproval(decision: ApprovalDecision): Promise<void> {
    if (!task.value?.pendingApproval) return;
    approvalPending.value = true;
    errorMessage.value = "";
    try {
      await gateway.respondApproval(
        task.value.taskId,
        task.value.pendingApproval.approvalId,
        decision,
      );
    } catch (error) {
      errorMessage.value = toMessage(error);
    } finally {
      approvalPending.value = false;
    }
  }

  async function cancelTask(): Promise<void> {
    if (!task.value || !isActive.value) return;
    actionPending.value = true;
    errorMessage.value = "";
    try {
      const response = await gateway.cancelTask(task.value.taskId);
      task.value.status = response.status;
    } catch (error) {
      errorMessage.value = toMessage(error);
    } finally {
      actionPending.value = false;
    }
  }

  function dismissError(): void {
    errorMessage.value = "";
  }

  function handleEvent(event: HakoEvent): void {
    if (receivedEventIds.has(event.eventId)) return;
    receivedEventIds.add(event.eventId);
    events.value.push(event);
    if (!task.value) return;

    switch (event.type) {
      case "run_started":
        model.value = readString(event.payload, "model");
        task.value.status = "RUNNING";
        task.value.startedAt ??= event.occurredAt;
        break;
      case "turn_started":
        task.value.progress.step = readNumber(event.payload, "step");
        task.value.progress.maxSteps =
          readNumber(event.payload, "maxSteps") ?? task.value.progress.maxSteps;
        break;
      case "context_stats":
        task.value.progress.usedTokens = readNumber(event.payload, "usedTokens");
        task.value.progress.contextLimit = readNumber(event.payload, "limit");
        task.value.progress.messageCount = readNumber(event.payload, "messageCount");
        break;
      case "task_status": {
        const current = readString(event.payload, "current") as TaskStatus | null;
        if (current) task.value.status = current;
        break;
      }
      case "approval_required":
        task.value.pendingApproval = event.payload as unknown as Approval;
        task.value.status = "WAITING_APPROVAL";
        break;
      case "approval_resolved":
        task.value.pendingApproval = null;
        task.value.status = "RUNNING";
        break;
      case "task_result": {
        const outcome = event.payload as unknown as TaskOutcome;
        task.value.outcome = outcome;
        task.value.status = outcome.success ? "COMPLETED" : "FAILED";
        task.value.finishedAt = event.occurredAt;
        finishStream();
        void refreshSummary();
        break;
      }
      case "worker_error":
        task.value.status = "FAILED";
        task.value.error = {
          code: readString(event.payload, "code") ?? "WORKER_ERROR",
          message: readString(event.payload, "message") ?? "Worker 运行失败。",
        } satisfies TaskError;
        task.value.finishedAt = event.occurredAt;
        finishStream();
        void refreshSummary();
        break;
      case "task_cancelled":
        task.value.status = "CANCELLED";
        task.value.finishedAt = event.occurredAt;
        finishStream();
        void refreshSummary();
        break;
      default:
        break;
    }
  }

  async function refreshSummary(): Promise<void> {
    if (!task.value) return;
    try {
      summary.value = await gateway.getSummary(task.value.taskId);
    } catch (error) {
      errorMessage.value = toMessage(error);
    }
  }

  async function restoreTask(taskId: string): Promise<void> {
    const restored = await gateway.getTask(taskId);
    task.value = restored;
    events.value = [];
    summary.value = null;
    model.value = null;
    receivedEventIds.clear();
    connectStream(taskId);
    if (!ACTIVE_STATES.has(restored.status)) {
      summary.value = await gateway.getSummary(taskId);
    }
  }

  function connectStream(taskId: string): void {
    closeStream?.();
    streamConnected.value = false;
    closeStream = gateway.streamTaskEvents(taskId, {
      onEvent: handleEvent,
      onOpen: () => {
        streamConnected.value = true;
      },
      onDisconnect: () => {
        streamConnected.value = false;
      },
      onError: (error) => {
        errorMessage.value = error.message;
      },
    });
  }

  function finishStream(): void {
    streamConnected.value = false;
    closeStream?.();
    closeStream = null;
  }

  function rememberTaskInUrl(taskId: string): void {
    if (gatewayMode !== "api") return;
    const url = new URL(window.location.href);
    url.searchParams.set("task", taskId);
    window.history.replaceState(null, "", url);
  }

  onBeforeUnmount(() => closeStream?.());

  return {
    gatewayMode: gatewayMode as "mock" | "api",
    task,
    events,
    summary,
    model,
    connection,
    streamConnected,
    status,
    isActive,
    pendingApproval,
    contextPercent,
    actionPending,
    approvalPending,
    errorMessage,
    initialize,
    startTask,
    resolveApproval,
    cancelTask,
    dismissError,
  };
}

function readString(payload: unknown, key: string): string | null {
  if (!payload || typeof payload !== "object") return null;
  const value = (payload as Record<string, unknown>)[key];
  return typeof value === "string" ? value : null;
}

function readNumber(payload: unknown, key: string): number | null {
  if (!payload || typeof payload !== "object") return null;
  const value = (payload as Record<string, unknown>)[key];
  return typeof value === "number" ? value : null;
}

function toMessage(error: unknown): string {
  return error instanceof Error ? error.message : "发生了未分类错误。";
}
