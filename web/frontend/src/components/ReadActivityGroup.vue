<script setup lang="ts">
import { computed } from "vue";
import type { ToolActivityPair } from "../utils/runPresentation";
import { formatTime, readPayload } from "../utils/presentation";

const props = defineProps<{ activities: ToolActivityPair[] }>();

const rows = computed(() => props.activities.map((activity, index) => {
  const args = readPayload(activity.started?.payload, "args");
  const finished = activity.finished;
  const touched = stringList(readPayload(finished?.payload, "touchedPaths"));
  const path = stringValue(readPayload(args, "path")) || touched[0] || `文件 ${index + 1}`;
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
  if (pendingCount.value) return `正在读取 ${rows.value.length} 个文件`;
  if (failedCount.value) return `读取 ${rows.value.length} 个文件，${failedCount.value} 个失败`;
  return `已读取 ${rows.value.length} 个文件`;
});

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
  <article class="timeline-event tool-activity read-activity-group" :data-variant="variant">
    <div class="event-marker" aria-hidden="true">{{ marker }}</div>
    <div class="event-content">
      <header class="event-header">
        <div class="event-title-row"><strong>{{ title }}</strong></div>
        <time v-if="latestTime" class="event-meta" :datetime="latestTime">{{ formatTime(latestTime) }}</time>
      </header>

      <details class="event-detail read-group-detail">
        <summary>查看读取文件</summary>
        <ul class="read-file-list">
          <li v-for="row in rows" :key="row.key" class="read-file-item">
            <details>
              <summary>
                <code>{{ row.path }}</code>
                <span :data-state="row.pending ? 'pending' : row.ok ? 'success' : 'error'">
                  {{ row.pending ? "读取中" : row.ok ? "已读取" : "失败" }}
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
