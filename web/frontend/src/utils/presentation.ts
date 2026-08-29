import type { HakoEventType, StopReason, TaskStatus } from "../types/api";

export const STATUS_LABELS: Record<TaskStatus | "IDLE", string> = {
  IDLE: "未开始",
  CREATED: "已创建",
  STARTING: "正在启动",
  RUNNING: "执行中",
  WAITING_APPROVAL: "等待批准",
  CANCELLING: "正在取消",
  COMPLETED: "已验证完成",
  FAILED: "未完成",
  CANCELLED: "已取消",
};

export const STOP_REASON_LABELS: Record<StopReason, string> = {
  done_read_only: "只读完成",
  done_verified: "修改后验证完成",
  done_unverified: "修改后未验证",
  incomplete: "回复未完成",
  max_steps: "达到步数上限",
  stuck: "检测到重复调用",
  denied: "用户拒绝操作",
  error: "运行错误",
};

export const TOOL_LABELS: Record<string, string> = {
  list_dir: "列出目录",
  read_file: "读取文件",
  edit_file: "局部编辑",
  write_file: "写入文件",
  run_command: "执行命令",
  delegate_readonly: "只读调查",
};

export const EVENT_LABELS: Record<HakoEventType, string> = {
  run_started: "任务启动",
  turn_started: "模型回合",
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
  task_status: "状态更新",
  approval_required: "等待批准",
  approval_resolved: "审批已处理",
  task_result: "任务结果",
  worker_error: "Worker 错误",
  task_cancelled: "任务取消",
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

export function readPayload(
  payload: unknown,
  key: string,
): unknown {
  if (!payload || typeof payload !== "object") return undefined;
  return (payload as Record<string, unknown>)[key];
}
