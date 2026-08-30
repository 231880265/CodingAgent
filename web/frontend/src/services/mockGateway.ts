import type {
  Approval,
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
  RunOutcome,
  RunResource,
  RunSummary,
  SessionCloseResponse,
  SessionHistory,
  SessionHistoryItem,
  SessionHistoryList,
  SessionResource,
} from "../types/api";

export class MockGateway implements HakoGateway {
  readonly mode = "mock" as const;
  private session: SessionResource | null = null;
  private readonly handlers = new Set<GatewayEventHandlers>();
  private readonly events: HakoEvent[] = [];
  private readonly runs: Array<RunResource & { summary: RunSummary | null }> = [];
  private readonly history = new Map<string, SessionHistory>();
  private eventId = 0;

  async checkHealth(): Promise<HealthResponse> {
    return {
      schemaVersion: "1.0",
      status: "UP",
      version: "0.1.0-mock",
      worker: { pythonConfigured: true, entrypointReadable: true },
    };
  }

  async createSession(request: CreateSessionRequest): Promise<SessionResource> {
    if (this.session && !["CLOSED", "FAILED"].includes(this.session.status)) {
      throw new Error("当前 Session 尚未关闭。");
    }
    this.handlers.clear();
    this.events.length = 0;
    this.runs.length = 0;
    this.eventId = 0;
    const sessionId = crypto.randomUUID();
    const run = this.makeRun(request.prompt, request.options.maxSteps, request.attachments);
    this.runs.push({ ...clone(run), summary: null });
    this.session = {
      schemaVersion: "1.0",
      sessionId,
      status: "OPENING",
      workspace: request.workspace,
      runCount: 1,
      canContinue: false,
      createdAt: new Date().toISOString(),
      closedAt: null,
      worker: {
        workerId: crypto.randomUUID(),
        pid: 24001,
        alive: true,
        status: "STARTING",
      },
      currentRun: run,
      links: {
        self: `/api/v1/sessions/${sessionId}`,
        events: `/api/v1/sessions/${sessionId}/events`,
        runs: `/api/v1/sessions/${sessionId}/runs`,
        currentSummary: `/api/v1/sessions/${sessionId}/runs/${run.runId}/summary`,
      },
    };
    this.emit("session_status", { previous: null, current: "OPENING", reason: "启动演示 Worker" });
    this.emit("run_status", { previous: null, current: "PENDING", reason: "Run 已排队" }, run.runId);
    setTimeout(() => this.openAndRun(run.runId), 120);
    return clone(this.session);
  }

  async createRun(sessionId: string, request: CreateRunRequest): Promise<SessionResource> {
    const session = this.requireSession(sessionId);
    if (session.status !== "OPEN" || !session.canContinue) {
      throw new Error("当前 Session 暂时不能接收后续 Run。");
    }
    const run = this.makeRun(
      request.prompt,
      request.options?.maxSteps ?? session.currentRun.options.maxSteps,
      request.attachments,
    );
    session.currentRun = run;
    session.runCount += 1;
    session.canContinue = false;
    session.links.currentSummary = `/api/v1/sessions/${sessionId}/runs/${run.runId}/summary`;
    this.runs.push({ ...clone(run), summary: null });
    this.emit("run_status", { previous: null, current: "PENDING", reason: "复用 Conversation" }, run.runId);
    setTimeout(() => this.beginRun(run.runId), 100);
    return clone(session);
  }

  async getSession(sessionId: string): Promise<SessionResource> {
    return clone(this.requireSession(sessionId));
  }

  streamSessionEvents(sessionId: string, handlers: GatewayEventHandlers): () => void {
    this.requireSession(sessionId);
    this.handlers.add(handlers);
    queueMicrotask(() => {
      handlers.onOpen?.();
      for (const event of this.events) handlers.onEvent(clone(event));
    });
    return () => this.handlers.delete(handlers);
  }

  async respondApproval(
    sessionId: string,
    runId: string,
    approvalId: string,
    decision: ApprovalDecision,
  ): Promise<ApprovalResponse> {
    const session = this.requireSession(sessionId);
    const run = this.requireCurrentRun(runId);
    const approval = run.pendingApproval;
    if (!approval || approval.approvalId !== approvalId) throw new Error("审批已失效。");
    run.pendingApproval = null;
    run.status = "RUNNING";
    this.emit("approval_resolved", { approvalId, decision, resolvedAt: new Date().toISOString() }, runId);
    this.emit("run_status", { previous: "WAITING_APPROVAL", current: "RUNNING", reason: "审批已返回 Agent" }, runId);
    if (decision === "DENY") {
      setTimeout(() => this.finishRun(runId, false), 100);
    } else {
      setTimeout(() => this.executeWriteAndVerify(runId), 100);
    }
    return {
      schemaVersion: "1.0",
      sessionId,
      runId,
      approvalId,
      status: "ACCEPTED",
      decision,
      acceptedAt: new Date().toISOString(),
    };
  }

  async cancelRun(sessionId: string, runId: string): Promise<CancelResponse> {
    this.requireSession(sessionId);
    const run = this.requireCurrentRun(runId);
    if (run.status === "CANCELLED") return this.cancelResponse(run);
    if (!["PENDING", "RUNNING", "WAITING_APPROVAL", "CANCELLING"].includes(run.status)) {
      throw new Error("已结束的 Run 不能取消。");
    }
    if (run.status !== "CANCELLING") {
      const previous = run.status;
      run.status = "CANCELLING";
      run.pendingApproval = null;
      this.emit("run_status", { previous, current: "CANCELLING", reason: "正在协作式取消" }, runId);
      setTimeout(() => {
        if (!this.session || this.session.currentRun.runId !== runId) return;
        run.status = "CANCELLED";
        run.finishedAt = new Date().toISOString();
        run.outcome = this.outcome(false, "cancelled", "本轮已取消；已落盘修改保留。", []);
        this.session.canContinue = true;
        this.emit("run_cancelled", { message: "Worker 保活，Conversation 继续可用。" }, runId);
        this.emit("run_status", { previous: "CANCELLING", current: "CANCELLED", reason: "取消完成" }, runId);
        this.storeRunSummary(run);
      }, 180);
    }
    return {
      schemaVersion: "1.0",
      sessionId,
      runId,
      status: "CANCELLING",
      message: "正在取消当前 Run；Session 保持 OPEN。",
    };
  }

  async closeSession(sessionId: string): Promise<SessionCloseResponse> {
    const session = this.requireSession(sessionId);
    if (["PENDING", "RUNNING", "WAITING_APPROVAL", "CANCELLING"].includes(session.currentRun.status)) {
      throw new Error("请先等待当前 Run 取消完成。");
    }
    if (session.status === "CLOSED") {
      return { schemaVersion: "1.0", sessionId, status: "CLOSED" };
    }
    const previous = session.status;
    session.status = "CLOSING";
    session.canContinue = false;
    this.emit("session_status", { previous, current: "CLOSING", reason: "回收演示 Worker" });
    setTimeout(() => {
      if (!this.session || this.session.sessionId !== sessionId) return;
      session.status = "CLOSED";
      session.closedAt = new Date().toISOString();
      session.worker.alive = false;
      session.worker.status = "EXITED";
      this.emit("worker_exited", { workerId: session.worker.workerId, expected: true });
      this.emit("session_status", { previous: "CLOSING", current: "CLOSED", reason: "会话已关闭" });
      this.snapshotHistory();
    }, 180);
    return { schemaVersion: "1.0", sessionId, status: "CLOSING" };
  }

  async getRunSummary(sessionId: string, runId: string): Promise<RunSummary> {
    this.requireSession(sessionId);
    const saved = this.runs.find((run) => run.runId === runId)?.summary;
    if (!saved) throw new Error("Run 摘要尚不可用。");
    return clone(saved);
  }

  async listSessionHistory(): Promise<SessionHistoryList> {
    this.snapshotHistory();
    const sessions: SessionHistoryItem[] = [...this.history.values()]
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
      .map((item) => ({
        sessionId: item.sessionId,
        workspace: item.workspace,
        status: item.status,
        runCount: item.runCount,
        createdAt: item.createdAt,
        closedAt: item.closedAt,
        lastPrompt: item.runs.at(-1)?.prompt ?? null,
      }));
    return { schemaVersion: "1.0", sessions };
  }

  async getSessionHistory(sessionId: string): Promise<SessionHistory> {
    this.snapshotHistory();
    const stored = this.history.get(sessionId);
    if (!stored) throw new Error("历史 Session 不存在。");
    return clone(stored);
  }

  private openAndRun(runId: string): void {
    if (!this.session || this.session.currentRun.runId !== runId) return;
    this.session.status = "OPEN";
    this.session.worker.status = "READY";
    this.emit("session_status", { previous: "OPENING", current: "OPEN", reason: "演示 Worker 已就绪" });
    this.beginRun(runId);
  }

  private beginRun(runId: string): void {
    const run = this.requireCurrentRun(runId);
    if (run.status !== "PENDING") return;
    run.status = "RUNNING";
    run.startedAt = new Date().toISOString();
    run.progress.step = 1;
    this.emit("run_started", { task: run.prompt, model: "deterministic-fake-worker", cwd: this.session?.workspace }, runId);
    this.emit("run_status", { previous: "PENDING", current: "RUNNING", reason: "Agent 已开始运行" }, runId);
    this.emit("assistant_text", { text: "先读取现有实现与失败信息，确认最小修改边界。" }, runId);
    this.emit("tool_call_started", { callId: `${runId}-read`, name: "read_file", args: { path: "router/headers.py" } }, runId);
    this.emit("tool_call_finished", { callId: `${runId}-read`, name: "read_file", ok: true, summary: "router/headers.py 1-86", detail: "读取成功。", durationMs: 4 }, runId);
    setTimeout(() => this.requestApproval(runId), 150);
  }

  private requestApproval(runId: string): void {
    const run = this.requireCurrentRun(runId);
    if (run.status !== "RUNNING") return;
    const approval: Approval = {
      approvalId: crypto.randomUUID(),
      sessionId: this.session!.sessionId,
      runId,
      status: "PENDING",
      tool: { name: "edit_file", args: { path: "router/headers.py" } },
      riskLevel: "NORMAL",
      dangerReason: null,
      allowedDecisions: ["ALLOW_ONCE", "ALLOW_SESSION", "DENY"],
      requestedAt: new Date().toISOString(),
      resolvedAt: null,
      decision: null,
    };
    run.pendingApproval = approval;
    run.status = "WAITING_APPROVAL";
    this.emit("approval_required", approval as unknown as Record<string, unknown>, runId);
    this.emit("run_status", { previous: "RUNNING", current: "WAITING_APPROVAL", reason: "等待编辑批准" }, runId);
  }

  private executeWriteAndVerify(runId: string): void {
    const run = this.requireCurrentRun(runId);
    if (run.status !== "RUNNING") return;
    run.progress.step = 2;
    this.emit("tool_call_started", { callId: `${runId}-edit`, name: "edit_file", args: { path: "router/headers.py" } }, runId);
    this.emit("tool_call_finished", { callId: `${runId}-edit`, name: "edit_file", ok: true, summary: "已精准更新 router/headers.py", detail: "唯一匹配修改完成。", durationMs: 8, touchedPaths: ["router/headers.py"], modifiedPaths: ["router/headers.py"], verificationKind: "" }, runId);
    this.emit("tool_call_started", { callId: `${runId}-test`, name: "run_command", args: { command: "pytest -q" } }, runId);
    this.emit("tool_call_finished", { callId: `${runId}-test`, name: "run_command", ok: true, summary: "4 passed in 0.08s", detail: "4 passed in 0.08s", durationMs: 941, touchedPaths: [], verificationKind: "test", verificationCommand: "python -m pytest -q" }, runId);
    this.finishRun(runId, true);
  }

  private finishRun(runId: string, changed: boolean): void {
    const run = this.requireCurrentRun(runId);
    if (!["RUNNING", "WAITING_APPROVAL"].includes(run.status)) return;
    const previous = run.status;
    const finalText = changed
      ? (this.session!.runCount > 1 ? "已结合上一轮上下文完成后续验证。" : "最小修复完成，完整测试通过。")
      : "写入被拒绝；保留只读调查结论，工作区未修改。";
    const outcome = this.outcome(
      true,
      changed ? "done_verified" : "done_read_only",
      finalText,
      changed ? ["router/headers.py"] : [],
    );
    run.status = "COMPLETED";
    run.finishedAt = new Date().toISOString();
    run.outcome = outcome;
    this.session!.canContinue = true;
    // 先固化权威摘要，再发布终态事件。这样演示网关与真实后端保持同一契约：
    // 前端看到 COMPLETED 时，摘要端点已经可读。
    this.storeRunSummary(run);
    this.emit("assistant_text", { text: finalText }, runId);
    this.emit("run_finished", { reason: outcome.stopReason, steps: 2, totalTokens: outcome.totalTokens, changedPaths: outcome.changedPaths, verification: outcome.verification.at(-1)?.summary ?? "" }, runId);
    this.emit("run_result", outcome as unknown as Record<string, unknown>, runId);
    this.emit("run_status", { previous, current: "COMPLETED", reason: "权威 RunResult 已返回" }, runId);
  }

  private outcome(
    success: boolean,
    stopReason: RunOutcome["stopReason"],
    finalText: string,
    changedPaths: string[],
  ): RunOutcome {
    return {
      success,
      stopReason,
      steps: 2,
      totalTokens: 7210,
      finalText,
      changedPaths,
      verification: changedPaths.length
        ? [{ kind: "test", command: "python -m pytest -q", summary: "4 passed in 0.08s", step: 2 }]
        : [],
      error: null,
    };
  }

  private storeRunSummary(run: RunResource): void {
    if (!run.outcome || !run.finishedAt) return;
    const summary: RunSummary = {
      schemaVersion: "1.0",
      sessionId: this.session!.sessionId,
      runId: run.runId,
      status: run.status as RunSummary["status"],
      finishedAt: run.finishedAt,
      ...clone(run.outcome),
    };
    const stored = this.runs.find((item) => item.runId === run.runId);
    if (stored) Object.assign(stored, clone(run), { summary });
    this.snapshotHistory();
  }

  private makeRun(prompt: string, maxSteps: number, attachments: CreateRunRequest["attachments"]): RunResource {
    return {
      runId: crypto.randomUUID(),
      status: "PENDING",
      prompt,
      options: { maxSteps },
      attachments: attachments.map((item) => ({ name: item.name, mediaType: item.mediaType, bytes: new TextEncoder().encode(item.content).length })),
      createdAt: new Date().toISOString(),
      startedAt: null,
      finishedAt: null,
      progress: { step: null, maxSteps, usedTokens: null, contextLimit: 1_000_000, messageCount: null },
      pendingApproval: null,
      outcome: null,
      error: null,
    };
  }

  private emit(type: HakoEventType, payload: Record<string, unknown>, runId?: string): void {
    if (!this.session) return;
    const event: HakoEvent = {
      schemaVersion: "1.0",
      eventId: ++this.eventId,
      sessionId: this.session.sessionId,
      ...(runId ? { runId } : {}),
      type,
      source: type.startsWith("run_") || type === "session_status" ? "WEB" : "HAKO",
      occurredAt: new Date().toISOString(),
      payload,
    };
    this.events.push(event);
    for (const handler of this.handlers) handler.onEvent(clone(event));
  }

  private snapshotHistory(): void {
    if (!this.session) return;
    this.history.set(this.session.sessionId, {
      schemaVersion: "1.0",
      sessionId: this.session.sessionId,
      workspace: this.session.workspace,
      status: this.session.status,
      workerId: this.session.worker.workerId,
      runCount: this.session.runCount,
      createdAt: this.session.createdAt,
      closedAt: this.session.closedAt,
      runs: clone(this.runs),
      events: clone(this.events),
    });
  }

  private requireSession(sessionId: string): SessionResource {
    if (!this.session || this.session.sessionId !== sessionId) throw new Error("Session 不存在。");
    return this.session;
  }

  private requireCurrentRun(runId: string): RunResource {
    if (!this.session || this.session.currentRun.runId !== runId) throw new Error("Run 已不是当前 Run。");
    return this.session.currentRun;
  }

  private cancelResponse(run: RunResource): CancelResponse {
    return {
      schemaVersion: "1.0",
      sessionId: this.session!.sessionId,
      runId: run.runId,
      status: "CANCELLED",
      message: "Run 已取消；Session 继续可用。",
    };
  }
}

function clone<T>(value: T): T {
  return structuredClone(value);
}
