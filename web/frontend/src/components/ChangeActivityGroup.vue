<script setup lang="ts">
import { computed } from "vue";
import type { ToolActivityPair } from "../utils/runPresentation";
import { formatTime, readPayload, readStringArgument } from "../utils/presentation";

const props = defineProps<{ activities: ToolActivityPair[] }>();

const rows = computed(() => props.activities.map((activity, index) => {
  const args = readPayload(activity.started?.payload, "args");
  const finished = activity.finished;
  const created = stringList(readPayload(finished?.payload, "createdPaths"));
  const modified = stringList(readPayload(finished?.payload, "modifiedPaths"));
  const deleted = stringList(readPayload(finished?.payload, "deletedPaths"));
  const path = readStringArgument(args, "path", "file_path")
    || created[0]
    || modified[0]
    || deleted[0]
    || `修改 ${index + 1}`;
  const ok = readPayload(finished?.payload, "ok") === true;
  const durationMs = readPayload(finished?.payload, "durationMs");
  return {
    key: activity.key,
    args,
    path,
    action: created.length ? "创建" : deleted.length ? "删除" : "修改",
    ok,
    pending: finished == null,
    detail: stringValue(readPayload(finished?.payload, "detail")),
    note: activity.notes
      .map((event) => stringValue(readPayload(event.payload, "text")))
      .filter(Boolean)
      .join("\n\n"),
    duration: typeof durationMs === "number" ? `${durationMs} ms` : "",
    occurredAt: finished?.occurredAt ?? activity.started?.occurredAt ?? "",
    oldText: readStringArgument(args, "old_text", "old_string"),
    newText: readStringArgument(args, "new_text", "new_string"),
    content: stringValue(readPayload(args, "content")),
  };
}));

const failedCount = computed(() => rows.value.filter((row) => !row.pending && !row.ok).length);
const pendingCount = computed(() => rows.value.filter((row) => row.pending).length);
const uniquePathCount = computed(() => new Set(rows.value.map((row) => row.path)).size);
const latestTime = computed(() => rows.value.at(-1)?.occurredAt ?? "");
const variant = computed(() => failedCount.value ? "error" : "neutral");
const marker = computed(() => failedCount.value ? "×" : "›");
const title = computed(() => {
  if (pendingCount.value) return `正在修改 ${uniquePathCount.value} 个文件`;
  if (failedCount.value) return `已执行 ${rows.value.length} 次修改，${failedCount.value} 次失败`;
  if (uniquePathCount.value === rows.value.length) return `已修改 ${uniquePathCount.value} 个文件`;
  return `已完成 ${rows.value.length} 次修改，涉及 ${uniquePathCount.value} 个文件`;
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
  <article
    class="timeline-event tool-activity read-activity-group change-activity-group"
    :class="{ 'is-pending': pendingCount > 0 }"
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
        <summary>查看修改文件</summary>
        <ul class="read-file-list">
          <li v-for="row in rows" :key="row.key" class="read-file-item">
            <details>
              <summary>
                <code>{{ row.path }}</code>
                <span :data-state="row.pending ? 'pending' : row.ok ? 'success' : 'error'">
                  {{ row.pending ? `${row.action}中` : row.ok ? `已${row.action}` : `${row.action}失败` }}
                </span>
              </summary>
              <div class="read-file-detail">
                <p v-if="row.note" class="agent-note-copy">{{ row.note }}</p>
                <small v-if="row.duration">耗时 {{ row.duration }}</small>
                <div v-if="row.oldText" class="detail-block code-diff-before">
                  <span>修改前</span>
                  <pre>{{ row.oldText }}</pre>
                </div>
                <div v-if="row.newText" class="detail-block code-diff-after">
                  <span>修改后</span>
                  <pre>{{ row.newText }}</pre>
                </div>
                <div v-if="row.content" class="detail-block">
                  <span>写入内容</span>
                  <pre>{{ row.content }}</pre>
                </div>
                <div v-if="row.args && !row.oldText && !row.newText && !row.content" class="detail-block">
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
