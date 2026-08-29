<script setup lang="ts">
import { computed } from "vue";
import type { HakoEvent, StopReason } from "../types/api";
import {
  EVENT_LABELS,
  STATUS_LABELS,
  STOP_REASON_LABELS,
  formatTime,
  formatTokens,
  formatToolName,
  readPayload,
} from "../utils/presentation";

const props = defineProps<{
  event: HakoEvent;
}>();

const isCompact = computed(() =>
  ["turn_started", "context_stats", "task_status", "approval_resolved"].includes(
    props.event.type,
  ),
);

const variant = computed(() => {
  if (["agent_error", "worker_error"].includes(props.event.type)) return "error";
  if (
    props.event.type === "tool_call_finished" &&
    readPayload(props.event.payload, "ok") === false
  ) {
    return "error";
  }
  if (
    [
      "verification_required",
      "continuation_required",
      "approval_required",
      "stream_gap",
    ].includes(props.event.type)
  ) {
    return "warning";
  }
  if (
    props.event.type === "task_result" &&
    readPayload(props.event.payload, "success") === true
  ) {
    return "success";
  }
  return "neutral";
});

const marker = computed(() => {
  if (variant.value === "error") return "×";
  if (variant.value === "warning") return "!";
  if (variant.value === "success") return "✓";
  if (props.event.type.startsWith("tool_call")) return "›";
  if (props.event.type === "assistant_text") return "a";
  return "·";
});

const title = computed(() => {
  const payload = props.event.payload;
  if (props.event.type === "tool_call_started") {
    return `${formatToolName(readPayload(payload, "name"))}开始`;
  }
  if (props.event.type === "tool_call_finished") {
    return formatToolName(readPayload(payload, "name"));
  }
  if (props.event.type === "turn_started") {
    return `第 ${readPayload(payload, "step") ?? "?"} 轮`;
  }
  return EVENT_LABELS[props.event.type];
});

const summary = computed(() => {
  const payload = props.event.payload;
  switch (props.event.type) {
    case "run_started":
      return String(readPayload(payload, "model") ?? "模型未报告");
    case "tool_call_started":
      return argumentSubject(readPayload(payload, "args"));
    case "tool_call_finished":
      return String(readPayload(payload, "summary") ?? "工具已返回");
    case "context_stats":
      return `${formatTokens(numberValue(readPayload(payload, "usedTokens")))} / ${formatTokens(numberValue(readPayload(payload, "limit")))} tokens`;
    case "task_status": {
      const current = String(readPayload(payload, "current") ?? "");
      return STATUS_LABELS[current as keyof typeof STATUS_LABELS] ?? current;
    }
    case "approval_required": {
      const tool = readPayload(payload, "tool");
      return formatToolName(readPayload(tool, "name"));
    }
    case "approval_resolved":
      return decisionLabel(readPayload(payload, "decision"));
    case "run_finished": {
      const reason = readPayload(payload, "reason") as StopReason | undefined;
      return reason ? STOP_REASON_LABELS[reason] : "内核已停止";
    }
    case "task_result":
      return readPayload(payload, "success") === true
        ? "Verified Finish"
        : "任务未满足完成条件";
    case "subagent_finished":
      return `${readPayload(payload, "steps") ?? 0} 步 · ${formatTokens(numberValue(readPayload(payload, "totalTokens")))} tokens`;
    default:
      return "";
  }
});

const body = computed(() => {
  const payload = props.event.payload;
  if (props.event.type === "assistant_text") {
    return stringValue(readPayload(payload, "text"));
  }
  if (
    [
      "verification_required",
      "continuation_required",
      "agent_error",
      "worker_error",
      "task_cancelled",
      "stream_gap",
    ].includes(props.event.type)
  ) {
    return stringValue(readPayload(payload, "message"));
  }
  if (props.event.type === "subagent_started") {
    return stringValue(readPayload(payload, "task"));
  }
  return "";
});

const detail = computed(() => {
  if (props.event.type === "tool_call_finished") {
    return stringValue(readPayload(props.event.payload, "detail"));
  }
  if (props.event.type === "tool_call_started") {
    const args = readPayload(props.event.payload, "args");
    return args ? JSON.stringify(args, null, 2) : "";
  }
  return "";
});

const duration = computed(() => {
  const value = numberValue(readPayload(props.event.payload, "durationMs"));
  return value == null ? "" : `${value} ms`;
});

function argumentSubject(args: unknown): string {
  const command = readPayload(args, "command");
  if (typeof command === "string") return command;
  const path = readPayload(args, "path");
  if (typeof path === "string") return path;
  return "参数已提交";
}

function decisionLabel(value: unknown): string {
  if (value === "ALLOW_ONCE") return "允许这一次";
  if (value === "ALLOW_SESSION") return "本任务同类允许";
  if (value === "DENY") return "已拒绝";
  return "审批已处理";
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
</script>

<template>
  <article
    class="timeline-event"
    :class="{ 'is-compact': isCompact }"
    :data-variant="variant"
  >
    <div class="event-marker" aria-hidden="true">{{ marker }}</div>
    <div class="event-content">
      <header class="event-header">
        <div class="event-title-row">
          <strong>{{ title }}</strong>
          <span v-if="summary" class="event-summary">{{ summary }}</span>
        </div>
        <div class="event-meta">
          <span v-if="duration">{{ duration }}</span>
          <time :datetime="event.occurredAt">{{ formatTime(event.occurredAt) }}</time>
        </div>
      </header>

      <p v-if="body" class="event-body">{{ body }}</p>

      <details v-if="detail" class="event-detail" :open="variant === 'error'">
        <summary>{{ event.type === "tool_call_started" ? "查看参数" : "查看结果" }}</summary>
        <pre>{{ detail }}</pre>
      </details>
    </div>
  </article>
</template>
