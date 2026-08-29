import type {
  Approval,
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
  TaskStatus,
  TaskSummary,
} from "../types/api";

type DemoPhase = "EDIT" | "TEST" | null;

const TERMINAL_STATES = new Set<TaskStatus>([
  "COMPLETED",
  "FAILED",
  "CANCELLED",
]);

function clone<T>(value: T): T {
  return structuredClone(value);
}

export class MockGateway implements HakoGateway {
  readonly mode = "mock" as const;

  private task: TaskResource | null = null;
  private summary: TaskSummary | null = null;
  private events: HakoEvent[] = [];
  private listeners = new Set<(event: HakoEvent) => void>();
  private timers = new Set<number>();
  private nextEventId = 1;
  private phase: DemoPhase = null;
  private editApplied = false;

  async checkHealth(): Promise<HealthResponse> {
    return {
      schemaVersion: "1.0",
      status: "UP",
      version: "mock-0.1.0",
      worker: {
        pythonConfigured: true,
        entrypointReadable: true,
      },
    };
  }

  async createTask(request: CreateTaskRequest): Promise<TaskResource> {
    if (this.task && !TERMINAL_STATES.has(this.task.status)) {
      throw new Error("当前已有任务在运行，请先等待完成或取消。");
    }

    this.clearTimers();
    this.events = [];
    this.summary = null;
    this.nextEventId = 1;
    this.phase = null;
    this.editApplied = false;

    const taskId = crypto.randomUUID();
    const createdAt = new Date().toISOString();
    this.task = {
      schemaVersion: "1.0",
      taskId,
      status: "STARTING",
      workspace: request.workspace,
      prompt: request.prompt,
      options: clone(request.options),
      createdAt,
      startedAt: null,
      finishedAt: null,
      progress: {
        step: null,
        maxSteps: request.options.maxSteps,
        usedTokens: null,
        contextLimit: null,
        messageCount: null,
      },
      pendingApproval: null,
      outcome: null,
      error: null,
      links: {
        self: `/api/v1/tasks/${taskId}`,
        events: `/api/v1/tasks/${taskId}/events`,
        summary: `/api/v1/tasks/${taskId}/summary`,
      },
    };

    this.schedule(360, () => {
      if (!this.task) return;
      this.task.startedAt = new Date().toISOString();
      this.emit("run_started", {
        task: this.task.prompt,
        model: "deepseek-ai/DeepSeek-V4-Flash",
        cwd: this.task.workspace,
      });
      this.setStatus("RUNNING", "Worker 已启动");
    });
    this.schedule(760, () => this.turnStarted(1));
    this.schedule(1100, () =>
      this.emit("assistant_text", {
        text: "我先确认仓库结构和现有测试，再定位 Header 读取路径。",
      }),
    );
    this.schedule(1450, () =>
      this.emit("tool_call_started", {
        callId: "call-list",
        name: "list_dir",
        args: { path: "." },
      }),
    );
    this.schedule(1660, () =>
      this.emit("tool_call_finished", {
        callId: "call-list",
        name: "list_dir",
        ok: true,
        summary: "发现 router、tests 等 6 项",
        detail: "router/\ntests/\npyproject.toml\nREADME.md",
        durationMs: 4,
      }),
    );
    this.schedule(1950, () =>
      this.emit("tool_call_started", {
        callId: "call-read",
        name: "read_file",
        args: { path: "router/headers.py", offset: 1, limit: 160 },
      }),
    );
    this.schedule(2220, () =>
      this.emit("tool_call_finished", {
        callId: "call-read",
        name: "read_file",
        ok: true,
        summary: "router/headers.py 1–86",
        detail:
          "当前实现直接使用 headers.get(name)，无法覆盖大小写和下划线写法。",
        durationMs: 6,
      }),
    );
    this.schedule(2520, () => {
      this.emit("context_stats", {
        usedTokens: 3820,
        limit: 1000000,
        messageCount: 7,
      });
      if (this.task) {
        this.task.progress.usedTokens = 3820;
        this.task.progress.contextLimit = 1000000;
        this.task.progress.messageCount = 7;
      }
    });
    this.schedule(2850, () =>
      this.emit("assistant_text", {
        text: "根因是 Header 名称在进入优先级选择前没有统一规范化。我会只改这一层，并保留原有五级选择顺序。",
      }),
    );
    this.schedule(3250, () => this.requestEditApproval());

    return clone(this.task);
  }

  async getTask(taskId: string): Promise<TaskResource> {
    return clone(this.requireTask(taskId));
  }

  streamTaskEvents(taskId: string, handlers: GatewayEventHandlers): () => void {
    this.requireTask(taskId);
    for (const event of this.events) {
      handlers.onEvent(clone(event));
    }
    const listener = (event: HakoEvent) => handlers.onEvent(clone(event));
    this.listeners.add(listener);
    window.setTimeout(() => handlers.onOpen?.(), 0);
    return () => this.listeners.delete(listener);
  }

  async respondApproval(
    taskId: string,
    approvalId: string,
    decision: ApprovalDecision,
  ): Promise<ApprovalResponse> {
    const task = this.requireTask(taskId);
    const approval = task.pendingApproval;
    if (!approval || approval.approvalId !== approvalId) {
      throw new Error("该审批已失效或不属于当前任务。 ");
    }
    if (!approval.allowedDecisions.includes(decision)) {
      throw new Error("当前风险等级不允许这个审批选项。 ");
    }

    const resolvedAt = new Date().toISOString();
    approval.status = "RESOLVED";
    approval.resolvedAt = resolvedAt;
    approval.decision = decision;
    this.emit("approval_resolved", {
      approvalId,
      decision,
      resolvedAt,
    }, "WORKER");
    task.pendingApproval = null;
    this.setStatus("RUNNING", decision === "DENY" ? "用户拒绝操作" : "用户已批准操作");

    if (decision === "DENY") {
      this.schedule(420, () => this.finishDenied());
    } else if (this.phase === "EDIT") {
      this.scheduleEditExecution();
    } else if (this.phase === "TEST") {
      this.scheduleVerification();
    }
    this.phase = null;

    return {
      schemaVersion: "1.0",
      taskId,
      approvalId,
      status: "ACCEPTED",
      decision,
      acceptedAt: resolvedAt,
    };
  }

  async cancelTask(taskId: string): Promise<CancelResponse> {
    const task = this.requireTask(taskId);
    if (TERMINAL_STATES.has(task.status)) {
      if (task.status !== "CANCELLED") {
        throw new Error("已结束的任务不能取消。");
      }
      return {
        schemaVersion: "1.0",
        taskId,
        status: "CANCELLED",
        message: "任务已经取消。",
      };
    }

    this.clearTimers();
    task.pendingApproval = null;
    this.setStatus("CANCELLING", "正在终止 Worker 进程树");
    this.schedule(520, () => {
      if (!this.task) return;
      const finishedAt = new Date().toISOString();
      this.summary = {
        schemaVersion: "1.0",
        taskId,
        status: "CANCELLED",
        success: false,
        stopReason: null,
        steps: this.task.progress.step ?? 0,
        totalTokens: this.task.progress.usedTokens ?? 0,
        finalText: "任务由用户取消。已发生的文件修改没有自动回滚。",
        changedPaths: this.editApplied ? ["router/headers.py"] : [],
        verification: [],
        error: null,
        finishedAt,
      };
      this.task.finishedAt = finishedAt;
      this.task.outcome = this.toOutcome(this.summary);
      this.emit("task_cancelled", {
        message: "Worker 已停止；已经写入的文件不会自动回滚。",
        forced: false,
      }, "WEB");
      this.setStatus("CANCELLED", "任务已取消");
    });

    return {
      schemaVersion: "1.0",
      taskId,
      status: "CANCELLING",
      message: "正在终止 Worker 进程树；已发生的文件修改不会自动回滚。",
    };
  }

  async getSummary(taskId: string): Promise<TaskSummary> {
    this.requireTask(taskId);
    if (!this.summary) {
      throw new Error("任务尚未结束，摘要还不可用。");
    }
    return clone(this.summary);
  }

  private requestEditApproval(): void {
    this.phase = "EDIT";
    this.requestApproval("edit_file", {
      path: "router/headers.py",
      old_text: "raw = headers.get(name)",
      new_text: "raw = get_header_case_insensitive(headers, name)",
    });
  }

  private scheduleEditExecution(): void {
    this.schedule(240, () =>
      this.emit("tool_call_started", {
        callId: "call-edit",
        name: "edit_file",
        args: {
          path: "router/headers.py",
          old_text: "raw = headers.get(name)",
          new_text: "raw = get_header_case_insensitive(headers, name)",
        },
      }),
    );
    this.schedule(620, () => {
      this.editApplied = true;
      this.emit("tool_call_finished", {
        callId: "call-edit",
        name: "edit_file",
        ok: true,
        summary: "已更新 router/headers.py（唯一匹配）",
        detail: "1 处唯一匹配已替换；原文件编码和换行保持不变。",
        durationMs: 8,
      });
    });
    this.schedule(980, () => this.turnStarted(2));
    this.schedule(1260, () =>
      this.emit("assistant_text", {
        text: "修改已经落盘。按完成协议，最后一次修改之后还需要新的可执行验证。",
      }),
    );
    this.schedule(1640, () => {
      this.phase = "TEST";
      this.requestApproval("run_command", { command: "pytest -q" });
    });
  }

  private scheduleVerification(): void {
    this.schedule(240, () =>
      this.emit("tool_call_started", {
        callId: "call-test",
        name: "run_command",
        args: { command: "pytest -q" },
      }),
    );
    this.schedule(1180, () =>
      this.emit("tool_call_finished", {
        callId: "call-test",
        name: "run_command",
        ok: true,
        summary: "4 passed in 0.08s",
        detail: "....\n4 passed in 0.08s",
        durationMs: 941,
      }),
    );
    this.schedule(1440, () => {
      if (!this.task) return;
      this.task.progress.usedTokens = 7210;
      this.task.progress.contextLimit = 1000000;
      this.task.progress.messageCount = 13;
      this.emit("context_stats", {
        usedTokens: 7210,
        limit: 1000000,
        messageCount: 13,
      });
    });
    this.schedule(1720, () =>
      this.emit("assistant_text", {
        text: "Header 查找已统一规范化，五级优先顺序未改变；完整测试通过。",
      }),
    );
    this.schedule(2050, () => this.finishSuccess());
  }

  private requestApproval(name: string, args: Record<string, unknown>): void {
    if (!this.task) return;
    const approval: Approval = {
      approvalId: crypto.randomUUID(),
      taskId: this.task.taskId,
      status: "PENDING",
      tool: { name, args },
      riskLevel: "NORMAL",
      dangerReason: null,
      allowedDecisions: ["ALLOW_ONCE", "ALLOW_SESSION", "DENY"],
      requestedAt: new Date().toISOString(),
      resolvedAt: null,
      decision: null,
    };
    this.task.pendingApproval = approval;
    this.emit("approval_required", { ...approval }, "WORKER");
    this.setStatus("WAITING_APPROVAL", `等待批准 ${name}`);
  }

  private finishSuccess(): void {
    if (!this.task) return;
    const finishedAt = new Date().toISOString();
    const outcome = {
      success: true,
      stopReason: "done_verified" as const,
      steps: 2,
      totalTokens: 7210,
      finalText: "Header 查找已统一规范化，五级优先顺序未改变；完整测试通过。",
      changedPaths: ["router/headers.py"],
      verification: [
        {
          kind: "test",
          command: "python -m pytest -q",
          summary: "4 passed in 0.08s",
          step: 2,
        },
      ],
      error: null,
    };
    this.summary = {
      schemaVersion: "1.0",
      taskId: this.task.taskId,
      status: "COMPLETED",
      ...outcome,
      finishedAt,
    };
    this.task.outcome = clone(outcome);
    this.task.finishedAt = finishedAt;
    this.emit("run_finished", {
      reason: outcome.stopReason,
      steps: outcome.steps,
      totalTokens: outcome.totalTokens,
      changedPaths: outcome.changedPaths,
      verification: outcome.verification[0]?.summary ?? "",
    });
    this.emit("task_result", { ...outcome }, "WORKER");
    this.setStatus("COMPLETED", "Verified Finish");
  }

  private finishDenied(): void {
    if (!this.task) return;
    const finishedAt = new Date().toISOString();
    const outcome = {
      success: false,
      stopReason: "denied" as const,
      steps: this.task.progress.step ?? 1,
      totalTokens: this.task.progress.usedTokens ?? 3820,
      finalText: "用户拒绝了有副作用的操作，任务已停止。",
      changedPaths: this.editApplied ? ["router/headers.py"] : [],
      verification: [],
      error: null,
    };
    this.summary = {
      schemaVersion: "1.0",
      taskId: this.task.taskId,
      status: "FAILED",
      ...outcome,
      finishedAt,
    };
    this.task.outcome = clone(outcome);
    this.task.finishedAt = finishedAt;
    this.emit("run_finished", {
      reason: outcome.stopReason,
      steps: outcome.steps,
      totalTokens: outcome.totalTokens,
      changedPaths: outcome.changedPaths,
      verification: "",
    });
    this.emit("task_result", { ...outcome }, "WORKER");
    this.setStatus("FAILED", "用户拒绝操作");
  }

  private turnStarted(step: number): void {
    if (!this.task) return;
    this.task.progress.step = step;
    this.emit("turn_started", {
      step,
      maxSteps: this.task.options.maxSteps,
    });
  }

  private setStatus(current: TaskStatus, reason: string): void {
    if (!this.task || this.task.status === current) return;
    const previous = this.task.status;
    this.task.status = current;
    this.emit("task_status", { previous, current, reason }, "WEB");
  }

  private emit(
    type: HakoEventType,
    payload: Record<string, unknown>,
    source: HakoEvent["source"] = "HAKO",
  ): void {
    if (!this.task) return;
    const event: HakoEvent = {
      schemaVersion: "1.0",
      eventId: this.nextEventId++,
      taskId: this.task.taskId,
      type,
      source,
      occurredAt: new Date().toISOString(),
      payload,
    };
    this.events.push(event);
    for (const listener of this.listeners) {
      listener(event);
    }
  }

  private schedule(delay: number, action: () => void): void {
    const timer = window.setTimeout(() => {
      this.timers.delete(timer);
      action();
    }, delay);
    this.timers.add(timer);
  }

  private clearTimers(): void {
    for (const timer of this.timers) {
      window.clearTimeout(timer);
    }
    this.timers.clear();
  }

  private requireTask(taskId: string): TaskResource {
    if (!this.task || this.task.taskId !== taskId) {
      throw new Error("任务不存在。");
    }
    return this.task;
  }

  private toOutcome(summary: TaskSummary): TaskResource["outcome"] {
    return {
      success: summary.success,
      stopReason: summary.stopReason,
      steps: summary.steps,
      totalTokens: summary.totalTokens,
      finalText: summary.finalText,
      changedPaths: clone(summary.changedPaths),
      verification: clone(summary.verification),
      error: summary.error ? clone(summary.error) : null,
    };
  }
}
