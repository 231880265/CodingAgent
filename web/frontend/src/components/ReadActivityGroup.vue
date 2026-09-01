<script setup lang="ts">
import { computed } from "vue";
import type { ToolActivityPair } from "../utils/runPresentation";
import { formatTime, readPayload, readStringArgument } from "../utils/presentation";

const props = withDefaults(defineProps<{
  activities: ToolActivityPair[];
  kind?: "read" | "workspace" | "exploration";
}>(), {
  kind: "read",
});

const rows = computed(() => props.activities.map((activity, index) => {
  const args = readPayload(activity.started?.payload, "args");
  const finished = activity.finished;
  const name = stringValue(readPayload(
    activity.started?.payload ?? activity.finished?.payload,
    "name",
  ));
  const touched = stringList(readPayload(finished?.payload, "touchedPaths"));
  const path = readStringArgument(args, "path", "file_path") || touched[0] || `文件 ${index + 1}`;
  const ok = readPayload(finished?.payload, "ok") === true;
  const detail = stringValue(readPayload(finished?.payload, "detail"));
  const durationMs = readPayload(finished?.payload, "durationMs");
  const note = activity.notes
    .map((event) => stringValue(readPayload(event.payload, "text")))
    .filter(Boolean)
    .join("\n\n");
  return {
    key: activity.key,
    name,
    args,
    detail,
    note,
    path,
    ok,
    pending: finished == null,
    durationMs: typeof durationMs === "number" ? durationMs : 0,
    duration: typeof durationMs === "number" ? `${durationMs} ms` : "",
    occurredAt: finished?.occurredAt ?? activity.started?.occurredAt ?? "",
  };
}));

const failedCount = computed(() => rows.value.filter((row) => !row.pending && !row.ok).length);
const pendingCount = computed(() => rows.value.filter((row) => row.pending).length);
const readCount = computed(() => rows.value.filter((row) => row.name === "read_file").length);
const workspaceCount = computed(() => rows.value.filter((row) => row.name === "list_dir").length);
const latestTime = computed(() => rows.value.at(-1)?.occurredAt ?? "");
const firstTime = computed(() => rows.value[0]?.occurredAt ?? "");
const elapsed = computed(() => formatElapsed(groupElapsedMs(rows.value)));
const timeTitle = computed(() => {
  if (!firstTime.value) return "";
  const start = formatTime(firstTime.value);
  const end = latestTime.value ? formatTime(latestTime.value) : start;
  return start === end ? start : `${start}–${end}`;
});
const variant = computed(() => {
  if (!failedCount.value) return "neutral";
  return props.kind === "exploration" ? "warning" : "error";
});
const marker = computed(() => {
  if (!failedCount.value) return "›";
  return props.kind === "exploration" ? "!" : "×";
});
const title = computed(() => {
  if (props.kind === "exploration") {
    if (pendingCount.value) return "正在探索代码库";
    const facts = [
      workspaceCount.value ? `检查 ${workspaceCount.value} 个位置` : "",
      readCount.value ? `读取 ${readCount.value} 个相关文件` : "",
    ].filter(Boolean).join(" · ");
    if (failedCount.value) return `探索代码库 · ${facts || `${rows.value.length} 次调用`} · ${failedCount.value} 个失败`;
    return facts ? `探索代码库 · ${facts}` : "探索代码库";
  }
  const subject = props.kind === "workspace" ? "个位置" : "个文件";
  const action = props.kind === "workspace" ? "查看" : "读取";
  if (pendingCount.value) return `正在${action} ${rows.value.length} ${subject}`;
  if (failedCount.value) return `${action} ${rows.value.length} ${subject}，${failedCount.value} 个失败`;
  return `已${action} ${rows.value.length} ${subject}`;
});
const detailLabel = computed(() =>
  props.kind === "exploration"
    ? "查看文件与工具调用"
    : props.kind === "workspace"
      ? "查看路径与工具详情"
      : "查看读取文件",
);

function rowState(row: { name: string; pending: boolean; ok: boolean }): string {
  const action = row.name === "list_dir" ? "查看" : "读取";
  if (row.pending) return `${action}中`;
  return row.ok ? `已${action}` : "失败";
}

function groupElapsedMs(values: Array<{ occurredAt: string; durationMs: number }>): number {
  if (!values.length) return 0;
  const start = Date.parse(values[0]!.occurredAt);
  const end = Date.parse(values.at(-1)?.occurredAt ?? "");
  if (Number.isFinite(start) && Number.isFinite(end) && end > start) return end - start;
  return values.reduce((total, row) => total + row.durationMs, 0);
}

function formatElapsed(value: number): string {
  if (value <= 0) return "";
  if (value < 1000) return "<1s";
  if (value < 60_000) return `${Math.max(1, Math.round(value / 1000))}s`;
  return `${Math.floor(value / 60_000)}m ${Math.round((value % 60_000) / 1000)}s`;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
</script>

<template>
  <article
    class="timeline-event tool-activity read-activity-group"
    :class="{
      'workspace-activity-group': kind === 'workspace',
      'exploration-activity-group': kind === 'exploration',
      'is-pending': pendingCount > 0,
    }"
    :data-variant="variant"
  >
    <div class="event-marker" aria-hidden="true">{{ marker }}</div>
    <div class="event-content">
      <header class="event-header">
        <div class="event-title-row">
          <strong>{{ title }}<span v-if="pendingCount" class="progress-dots" aria-hidden="true">...</span></strong>
        </div>
        <span v-if="pendingCount || elapsed" class="event-meta" :title="timeTitle">
          {{ pendingCount ? "进行中" : elapsed }}
        </span>
      </header>

      <details class="event-detail read-group-detail">
        <summary>{{ detailLabel }}</summary>
        <ul class="read-file-list">
          <li v-for="row in rows" :key="row.key" class="read-file-item">
            <details>
              <summary>
                <code>{{ row.path }}</code>
                <span :data-state="row.pending ? 'pending' : row.ok ? 'success' : 'error'">
                  {{ rowState(row) }}
                </span>
              </summary>
              <div class="read-file-detail">
                <p v-if="row.note" class="agent-note-copy">{{ row.note }}</p>
                <small v-if="row.occurredAt">时间 {{ formatTime(row.occurredAt) }}</small>
                <small v-if="row.duration">耗时 {{ row.duration }}</small>
                <div v-if="row.args" class="detail-block">
                  <span>工具参数</span>
                  <pre>{{ JSON.stringify(row.args, null, 2) }}</pre>
                </div>
                <div v-if="row.detail" class="detail-block">
                  <span>原始结果</span>
                  <pre>{{ row.detail }}</pre>
                </div>
              </div>
            </details>
          </li>
        </ul>
      </details>
    </div>
  </article>
</template>
