import { computed, onBeforeUnmount, ref } from "vue";
import { gateway, gatewayMode } from "../services/gateway";
import type {
  Approval,
  ApprovalDecision,
  CreateRunRequest,
  CreateSessionRequest,
  HakoEvent,
  RunError,
  RunOutcome,
  RunStatus,
  RunSummary,
  SessionHistory,
  SessionHistoryItem,
  SessionResource,
  SessionStatus,
} from "../types/api";

const ACTIVE_RUN_STATES = new Set<RunStatus>([
  "PENDING",
  "RUNNING",
  "WAITING_APPROVAL",
  "CANCELLING",
]);

export function useSessionController() {
  const session = ref<SessionResource | null>(null);
  const events = ref<HakoEvent[]>([]);
  const summary = ref<RunSummary | null>(null);
  const model = ref<string | null>(null);
  const connection = ref<"CHECKING" | "UP" | "DOWN">("CHECKING");
  const streamConnected = ref(false);
  const actionPending = ref(false);
  const approvalPending = ref(false);
  const errorMessage = ref("");
  const historyItems = ref<SessionHistoryItem[]>([]);
  const historyOpen = ref(false);
  const selectedHistory = ref<SessionHistory | null>(null);
  const receivedEventIds = new Set<number>();
  let closeStream: (() => void) | null = null;

  const currentRun = computed(() => session.value?.currentRun ?? null);
  const runStatus = computed<RunStatus | "IDLE">(
    () => currentRun.value?.status ?? "IDLE",
  );
  const sessionStatus = computed<SessionStatus | "NONE">(
    () => session.value?.status ?? "NONE",
  );
  const isActive = computed(() =>
    currentRun.value ? ACTIVE_RUN_STATES.has(currentRun.value.status) : false,
  );
  const pendingApproval = computed<Approval | null>(
    () => currentRun.value?.pendingApproval ?? null,
  );
  const contextPercent = computed(() => {
    const progress = currentRun.value?.progress;
    if (!progress?.usedTokens || !progress.contextLimit) return 0;
    return Math.min(100, (progress.usedTokens / progress.contextLimit) * 100);
  });
  const displayedEvents = computed(() => selectedHistory.value?.events ?? events.value);
  const viewingHistory = computed(() => selectedHistory.value !== null);

  async function initialize(): Promise<void> {
    try {
      const health = await gateway.checkHealth();
      connection.value = health.status;
      await refreshHistory();
    } catch (error) {
      connection.value = "DOWN";
      errorMessage.value = toMessage(error);
      return;
    }
    if (gatewayMode === "api") {
      const sessionId = new URL(window.location.href).searchParams.get("session");
      if (sessionId) {
        try {
          await restoreSession(sessionId);
        } catch {
          forgetSessionInUrl();
        }
      }
    }
  }

  async function startSession(request: CreateSessionRequest): Promise<void> {
    actionPending.value = true;
    resetLiveView();
    try {
      const created = await gateway.createSession(request);
      session.value = created;
      rememberSessionInUrl(created.sessionId);
      connectStream(created.sessionId);
      await refreshHistory();
    } catch (error) {
      errorMessage.value = toMessage(error);
    } finally {
      actionPending.value = false;
    }
  }

  async function createRun(request: CreateRunRequest): Promise<void> {
    if (!session.value) return;
    actionPending.value = true;
    errorMessage.value = "";
    selectedHistory.value = null;
    try {
      const updated = await gateway.createRun(session.value.sessionId, request);
      session.value = updated;
      summary.value = null;
      if (!closeStream) connectStream(updated.sessionId);
    } catch (error) {
      errorMessage.value = toMessage(error);
    } finally {
      actionPending.value = false;
    }
  }

  async function resolveApproval(decision: ApprovalDecision): Promise<void> {
    const approval = pendingApproval.value;
    if (!session.value || !approval) return;
    approvalPending.value = true;
    errorMessage.value = "";
    try {
      await gateway.respondApproval(
        session.value.sessionId,
        approval.runId,
        approval.approvalId,
        decision,
      );
    } catch (error) {
      errorMessage.value = toMessage(error);
    } finally {
      approvalPending.value = false;
    }
  }

  async function cancelRun(): Promise<void> {
    if (!session.value || !currentRun.value || !isActive.value) return;
    actionPending.value = true;
    errorMessage.value = "";
    try {
      const response = await gateway.cancelRun(
        session.value.sessionId,
        currentRun.value.runId,
      );
      currentRun.value.status = response.status;
    } catch (error) {
      errorMessage.value = toMessage(error);
    } finally {
      actionPending.value = false;
    }
  }

  async function newSession(): Promise<void> {
    if (!session.value) {
      clearClosedSession();
      return;
    }
    actionPending.value = true;
    errorMessage.value = "";
    const sessionId = session.value.sessionId;
    try {
      if (session.value.status === "OPENING") {
        await waitForSession(
          sessionId,
          (latest) => latest.status === "OPEN" || latest.status === "FAILED",
        );
      }
      if (session.value && isActive.value) {
        const runId = session.value.currentRun.runId;
        await gateway.cancelRun(sessionId, runId);
        await waitForSession(
          sessionId,
          (latest) => latest.currentRun.status === "CANCELLED",
        );
      }
      const response = await gateway.closeSession(sessionId);
      if (response.status === "CLOSING") {
        await waitForSession(
          sessionId,
          (latest) => latest.status === "CLOSED" || latest.status === "FAILED",
        );
      }
      await refreshHistory();
      clearClosedSession();
    } catch (error) {
      errorMessage.value = toMessage(error);
    } finally {
      actionPending.value = false;
    }
  }

  async function refreshHistory(): Promise<void> {
    try {
      historyItems.value = (await gateway.listSessionHistory()).sessions;
    } catch (error) {
      if (gatewayMode === "api") errorMessage.value = toMessage(error);
    }
  }

  async function openHistory(sessionId: string): Promise<void> {
    actionPending.value = true;
    errorMessage.value = "";
    try {
      selectedHistory.value = await gateway.getSessionHistory(sessionId);
      historyOpen.value = false;
    } catch (error) {
      errorMessage.value = toMessage(error);
    } finally {
      actionPending.value = false;
    }
  }

  function toggleHistory(): void {
    historyOpen.value = !historyOpen.value;
    if (historyOpen.value) void refreshHistory();
  }

  function closeHistoryView(): void {
    selectedHistory.value = null;
  }

  function dismissError(): void {
    errorMessage.value = "";
  }

  function handleEvent(event: HakoEvent): void {
    // 前端第二道身份过滤：旧 Worker 即使穿过后端也不能污染新 Session。
    if (!session.value || event.sessionId !== session.value.sessionId) return;
    if (receivedEventIds.has(event.eventId)) return;
    receivedEventIds.add(event.eventId);
    events.value.push(event);

    if (event.type === "session_status") {
      const current = readString(event.payload, "current") as SessionStatus | null;
      if (current) session.value.status = current;
      if (current === "CLOSED" || current === "FAILED") finishStream();
      return;
    }
    if (!event.runId || event.runId !== session.value.currentRun.runId) return;
    const run = session.value.currentRun;
    switch (event.type) {
      case "run_started":
        model.value = readString(event.payload, "model");
        run.startedAt ??= event.occurredAt;
        break;
      case "turn_started":
        run.progress.step = readNumber(event.payload, "step");
        run.progress.maxSteps = readNumber(event.payload, "maxSteps") ?? run.progress.maxSteps;
        break;
      case "context_stats":
        run.progress.usedTokens = readNumber(event.payload, "usedTokens");
        run.progress.contextLimit = readNumber(event.payload, "limit");
        run.progress.messageCount = readNumber(event.payload, "messageCount");
        break;
      case "run_status": {
        const current = readString(event.payload, "current") as RunStatus | null;
        if (current) run.status = current;
        if (current && ["COMPLETED", "FAILED", "CANCELLED"].includes(current)) {
          run.finishedAt ??= event.occurredAt;
          void refreshCompletedRun(event.runId);
        }
        break;
      }
      case "approval_required":
        run.pendingApproval = event.payload as unknown as Approval;
        break;
      case "approval_resolved":
        run.pendingApproval = null;
        break;
      case "run_result":
        run.outcome = event.payload as unknown as RunOutcome;
        break;
      case "worker_error":
        run.error = {
          code: readString(event.payload, "code") ?? "WORKER_ERROR",
          message: readString(event.payload, "message") ?? "Worker 运行失败。",
        } satisfies RunError;
        break;
      default:
        break;
    }
  }

  async function refreshCompletedRun(runId: string): Promise<void> {
    if (!session.value) return;
    const sessionId = session.value.sessionId;
    try {
      // 先释放 Session 的继续输入状态，再读取摘要。SSE 终态与摘要 GET 可能隔着
      // 极短的网络竞态，不能因为摘要慢一拍就把整个 Conversation 锁死。
      const latest = await gateway.getSession(sessionId);
      if (session.value?.sessionId !== sessionId) return;
      session.value = latest;
      summary.value = await readSummaryAfterTerminal(sessionId, runId);
      if (session.value?.sessionId !== sessionId) return;
      await refreshHistory();
    } catch (error) {
      errorMessage.value = toMessage(error);
    }
  }

  async function readSummaryAfterTerminal(
    sessionId: string,
    runId: string,
  ): Promise<RunSummary> {
    let lastError: unknown = new Error("Run 摘要尚不可用。");
    for (let attempt = 0; attempt < 4; attempt += 1) {
      try {
        return await gateway.getRunSummary(sessionId, runId);
      } catch (error) {
        lastError = error;
        if (attempt < 3) await delay(75 * (attempt + 1));
      }
    }
    throw lastError;
  }

  async function restoreSession(sessionId: string): Promise<void> {
    const restored = await gateway.getSession(sessionId);
    session.value = restored;
    resetTimeline();
    connectStream(sessionId);
    if (restored.currentRun.status && !ACTIVE_RUN_STATES.has(restored.currentRun.status)) {
      summary.value = await gateway.getRunSummary(sessionId, restored.currentRun.runId);
    }
  }

  function connectStream(sessionId: string): void {
    closeStream?.();
    streamConnected.value = false;
    closeStream = gateway.streamSessionEvents(sessionId, {
      onEvent: handleEvent,
      onOpen: () => { streamConnected.value = true; },
      onDisconnect: () => { streamConnected.value = false; },
      onError: (error) => { errorMessage.value = error.message; },
    });
  }

  async function waitForSession(
    sessionId: string,
    done: (latest: SessionResource) => boolean,
    timeoutMs = 15_000,
  ): Promise<void> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const latest = await gateway.getSession(sessionId);
      if (session.value?.sessionId !== sessionId) return;
      session.value = latest;
      if (done(latest)) return;
      await delay(150);
    }
    throw new Error("等待 Worker 确认状态超时；旧会话仍保留，未创建新 Session。");
  }

  function resetLiveView(): void {
    finishStream();
    session.value = null;
    selectedHistory.value = null;
    resetTimeline();
    errorMessage.value = "";
  }

  function resetTimeline(): void {
    events.value = [];
    summary.value = null;
    model.value = null;
    receivedEventIds.clear();
  }

  function clearClosedSession(): void {
    finishStream();
    session.value = null;
    selectedHistory.value = null;
    resetTimeline();
    forgetSessionInUrl();
  }

  function finishStream(): void {
    streamConnected.value = false;
    closeStream?.();
    closeStream = null;
  }

  function rememberSessionInUrl(sessionId: string): void {
    if (gatewayMode !== "api") return;
    const url = new URL(window.location.href);
    url.searchParams.set("session", sessionId);
    window.history.replaceState(null, "", url);
  }

  function forgetSessionInUrl(): void {
    if (gatewayMode !== "api") return;
    const url = new URL(window.location.href);
    url.searchParams.delete("session");
    window.history.replaceState(null, "", url);
  }

  onBeforeUnmount(() => closeStream?.());

  return {
    gatewayMode: gatewayMode as "mock" | "api",
    session,
    currentRun,
    events,
    displayedEvents,
    summary,
    model,
    connection,
    streamConnected,
    runStatus,
    sessionStatus,
    isActive,
    pendingApproval,
    contextPercent,
    actionPending,
    approvalPending,
    errorMessage,
    historyItems,
    historyOpen,
    selectedHistory,
    viewingHistory,
    initialize,
    startSession,
    createRun,
    resolveApproval,
    cancelRun,
    newSession,
    toggleHistory,
    openHistory,
    closeHistoryView,
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

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
