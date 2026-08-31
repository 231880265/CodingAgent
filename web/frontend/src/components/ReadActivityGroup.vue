<script setup lang="ts">
import { computed } from "vue";
import type { ToolActivityPair } from "../utils/runPresentation";
import { formatTime, readPayload, readStringArgument } from "../utils/presentation";

const props = withDefaults(defineProps<{
  activities: ToolActivityPair[];
  kind?: "read" | "workspace";
}>(), {
  kind: "read",
});

const rows = computed(() => props.activities.map((activity, index) => {
  const args = readPayload(activity.started?.payload, "args");
  const finished = activity.finished;
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
    args,
    detail,
    note,
    path,
    ok,
    pending: finished == null,
    duration: typeof durationMs === "number" ? `${durationMs} ms` : "",
    occurredAt: finished?.occurredAt ?? activity.started?.occurredAt ?? "",
  };
}));

const failedCount = computed(() => rows.value.filter((row) => !row.pending && !row.ok).length);
const pendingCount = computed(() => rows.value.filter((row) => row.pending).length);
const latestTime = computed(() => rows.value.at(-1)?.occurredAt ?? "");
const variant = computed(() => failedCount.value ? "error" : "neutral");
const marker = computed(() => failedCount.value ? "×" : "›");
const title = computed(() => {
  const subject = props.kind === "workspace" ? "个位置" : "个文件";
  const action = props.kind === "workspace" ? "查看" : "读取";
  if (pendingCount.value) return `正在${action} ${rows.value.length} ${subject}`;
  if (failedCount.value) return `${action} ${rows.value.length} ${subject}，${failedCount.value} 个失败`;
  return `已${action} ${rows.value.length} ${subject}`;
});
const detailLabel = computed(() =>
  props.kind === "workspace" ? "查看路径与工具详情" : "查看读取文件",
);
const stateLabels = computed(() => props.kind === "workspace"
  ? { pending: "查看中", success: "已查看" }
  : { pending: "读取中", success: "已读取" });

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
        <time v-if="latestTime" class="event-meta" :datetime="latestTime">{{ formatTime(latestTime) }}</time>
      </header>

      <details class="event-detail read-group-detail">
        <summary>{{ detailLabel }}</summary>
        <ul class="read-file-list">
          <li v-for="row in rows" :key="row.key" class="read-file-item">
            <details>
              <summary>
                <code>{{ row.path }}</code>
                <span :data-state="row.pending ? 'pending' : row.ok ? 'success' : 'error'">
                  {{ row.pending ? stateLabels.pending : row.ok ? stateLabels.success : "失败" }}
                </span>
              </summary>
              <div class="read-file-detail">
                <p v-if="row.note" class="agent-note-copy">{{ row.note }}</p>
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
