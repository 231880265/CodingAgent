<script setup lang="ts">
import { computed } from "vue";
import type { HakoEvent, StopReason } from "../types/api";
import {
  STOP_REASON_LABELS,
  formatTime,
  formatToolName,
  readPayload,
} from "../utils/presentation";
import MarkdownContent from "./MarkdownContent.vue";

const props = defineProps<{
  event: HakoEvent;
  notes?: HakoEvent[];
}>();
const reason = computed(() => readPayload(props.event.payload, "reason") as StopReason | undefined);
const isAgentNote = computed(() => props.event.type === "assistant_text");
const isUserMessage = computed(() => props.event.type === "run_started");
const variant = computed(() => {
  if (["agent_error", "worker_error"].includes(props.event.type)) return "error";
  if (["acceptance_required", "verification_required", "continuation_required", "approval_required", "stream_gap"].includes(props.event.type)) return "warning";
  if (props.event.type === "run_finished" && reason.value === "done_verified") return "success";
  return "neutral";
});
const marker = computed(() => {
  if (variant.value === "error") return "×";
  if (variant.value === "warning") return "!";
  if (variant.value === "success") return "✓";
  return "·";
});
const title = computed(() => {
  switch (props.event.type) {
    case "assistant_text": return "分析进展";
    case "acceptance_required": return "交付面尚未覆盖，继续实现";
    case "verification_required": return "完成证据不足，继续验证";
    case "continuation_required": return "回答尚未形成行动，继续执行";
    case "approval_required": return "等待你的批准";
    case "subagent_started": return "只读调查开始";
    case "subagent_finished": return "只读调查完成";
    case "run_finished":
      if (reason.value === "done_verified") return "完成检查通过";
      if (reason.value === "done_read_only") return "只读任务已完成";
      if (reason.value === "done_unverified") return "任务结束，但验证不足";
      return "任务已停止";
    case "run_cancelled": return "本轮已取消";
    case "agent_error": return "Agent 运行错误";
    case "worker_error": return "Worker 运行错误";
    case "stream_gap": return "事件流存在缺口";
    default: return "状态更新";
  }
});
const summary = computed(() => {
  if (props.event.type === "approval_required") {
    return formatToolName(readPayload(readPayload(props.event.payload, "tool"), "name"));
  }
  if (props.event.type === "run_finished" && reason.value) return STOP_REASON_LABELS[reason.value];
  if (props.event.type === "subagent_finished") return `${readPayload(props.event.payload, "steps") ?? 0} 次模型决策`;
  return "";
});
const body = computed(() => {
  if (props.event.type === "run_started") return stringValue(readPayload(props.event.payload, "task"));
  if (["agent_error", "worker_error", "run_cancelled", "stream_gap"].includes(props.event.type)) {
    return stringValue(readPayload(props.event.payload, "message"));
  }
  if (props.event.type === "subagent_started") return stringValue(readPayload(props.event.payload, "task"));
  if (props.event.type === "verification_required") {
    return "最后一次文件修改后还没有成功验证；hako 不接受模型直接宣布完成。";
  }
  if (props.event.type === "acceptance_required") {
    const items = readPayload(props.event.payload, "missingItems");
    const labels = Array.isArray(items)
      ? items.filter((item): item is string => typeof item === "string")
      : [];
    return labels.length
      ? `用户明确要求的交付面尚未全部覆盖：${labels.join("；")}。`
      : "用户明确要求的交付面尚未全部覆盖，hako 要求 Agent 继续实现。";
  }
  if (props.event.type === "continuation_required") {
    return "上一条回复没有产生工具行动，内核要求 Agent 继续执行。";
  }
  if (isAgentNote.value) return preview(modelNotes.value);
  return "";
});
const modelNotes = computed(() => {
  const events = [
    ...(props.notes ?? []),
    ...(isAgentNote.value ? [props.event] : []),
  ];
  return events
    .map((event) => stringValue(readPayload(event.payload, "text")))
    .filter(Boolean)
    .join("\n\n");
});
const fullMessage = computed(() => isAgentNote.value
  ? ""
  : stringValue(readPayload(props.event.payload, "message")));
const runMetadata = computed(() => {
  if (!isUserMessage.value) return "";
  const model = stringValue(readPayload(props.event.payload, "model"));
  const cwd = stringValue(readPayload(props.event.payload, "cwd"));
  return [model && `模型：${model}`, cwd && `工作区：${cwd}`].filter(Boolean).join("\n");
});

function preview(value: string): string {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.length > 220 ? `${compact.slice(0, 220)}…` : compact;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
</script>

<template>
  <article v-if="isUserMessage" class="user-message">
    <div class="user-prompt-block">
      <header class="user-role">
        <strong>你</strong>
        <time :datetime="event.occurredAt">{{ formatTime(event.occurredAt) }}</time>
      </header>
      <MarkdownContent :content="body" />
      <details v-if="runMetadata" class="run-diagnostics">
        <summary>运行环境</summary>
        <pre>{{ runMetadata }}</pre>
      </details>
    </div>
    <span class="user-avatar" aria-hidden="true">你</span>
  </article>

  <article
    v-else
    class="timeline-event"
    :class="{ 'is-secondary': isAgentNote }"
    :data-variant="variant"
  >
    <div class="event-marker" aria-hidden="true">{{ marker }}</div>
    <div class="event-content">
      <header class="event-header">
        <div class="event-title-row">
          <strong>{{ title }}</strong>
          <span v-if="summary" class="event-summary">{{ summary }}</span>
        </div>
        <time class="event-meta" :datetime="event.occurredAt">{{ formatTime(event.occurredAt) }}</time>
      </header>
      <p v-if="body" class="event-body">{{ body }}</p>
      <details v-if="modelNotes" class="event-detail model-note-detail">
        <summary>{{ isAgentNote ? "展开完整模型说明" : "查看模型说明" }}</summary>
        <MarkdownContent :content="modelNotes" />
      </details>
      <details v-if="fullMessage" class="event-detail">
        <summary>查看内核原文</summary>
        <pre>{{ fullMessage }}</pre>
      </details>
    </div>
  </article>
</template>
