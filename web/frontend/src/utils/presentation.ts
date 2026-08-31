import type { HakoEventType, RunStatus, StopReason } from "../types/api";

export const STATUS_LABELS: Record<RunStatus | "IDLE", string> = {
  IDLE: "未开始",
  PENDING: "等待启动",
  RUNNING: "执行中",
  WAITING_APPROVAL: "等待批准",
  CANCELLING: "正在取消",
  COMPLETED: "已完成",
  FAILED: "已停止",
  CANCELLED: "已取消",
};

export const STOP_REASON_LABELS: Record<StopReason, string> = {
  done_read_only: "只读完成",
  done_verified: "修改后验证完成",
  done_unverified: "修改后未验证",
  incomplete: "回答未完成",
  max_steps: "达到本轮安全预算",
  stuck: "检测到重复调用",
  denied: "用户拒绝操作",
  cancelled: "用户取消本轮",
  error: "运行错误",
};

export const TOOL_LABELS: Record<string, string> = {
  list_dir: "查看工作区",
  read_file: "读取文件",
  edit_file: "局部编辑",
  write_file: "写入文件",
  run_command: "执行命令",
  delegate_readonly: "只读调查",
};

export const EVENT_LABELS: Record<HakoEventType, string> = {
  session_status: "会话状态",
  worker_exited: "Worker 已退出",
  run_status: "Run 状态",
  run_started: "任务启动",
  turn_started: "模型决策",
  assistant_text: "Agent 说明",
  tool_call_started: "工具开始",
  tool_call_finished: "工具结果",
  context_stats: "上下文",
  verification_required: "需要验证",
  continuation_required: "继续行动",
  subagent_started: "只读调查开始",
  subagent_finished: "只读调查结束",
  run_finished: "内核结束",
  agent_error: "Agent 错误",
  approval_required: "等待批准",
  approval_resolved: "审批已处理",
  run_result: "Run 结果",
  worker_error: "Worker 错误",
  run_cancelled: "Run 取消",
  stream_gap: "事件缺口",
};

export function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function formatTokens(value: number | null | undefined): string {
  if (value == null) return "尚无数据";
  if (value < 1000) return `${value}`;
  if (value >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
  return `${(value / 1000).toFixed(1)}k`;
}

export function formatToolName(value: unknown): string {
  if (typeof value !== "string") return "未知工具";
  return TOOL_LABELS[value] ?? value;
}

export type StatusTone = "neutral" | "success" | "warning" | "danger";

export function statusPresentation(
  status: RunStatus | "IDLE",
  stopReason: StopReason | null = null,
): { label: string; tone: StatusTone } {
  if (status === "COMPLETED") {
    return stopReason === "done_verified"
      ? { label: "已验证完成", tone: "success" }
      : { label: "已完成", tone: "neutral" };
  }
  if (["WAITING_APPROVAL", "CANCELLING", "CANCELLED"].includes(status)) {
    return { label: STATUS_LABELS[status], tone: "warning" };
  }
  if (status === "FAILED") {
    if (stopReason === "max_steps") {
      return { label: "等待继续", tone: "warning" };
    }
    if (stopReason === "done_unverified") {
      return { label: "已结束 · 验证不足", tone: "warning" };
    }
    if (stopReason && stopReason !== "error") {
      return { label: `已停止 · ${STOP_REASON_LABELS[stopReason]}`, tone: "warning" };
    }
    return { label: "运行错误", tone: "danger" };
  }
  return { label: STATUS_LABELS[status], tone: "neutral" };
}

export function readPayload(payload: unknown, key: string): unknown {
  if (!payload || typeof payload !== "object") return undefined;
  return (payload as Record<string, unknown>)[key];
}
