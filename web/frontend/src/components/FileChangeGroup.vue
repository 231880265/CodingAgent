<script setup lang="ts">
import { computed } from "vue";
import type { ToolActivityPair } from "../utils/runPresentation";
import { formatTime, readPayload, readStringArgument } from "../utils/presentation";

const props = defineProps<{
  path: string;
  activities: ToolActivityPair[];
}>();

const rows = computed(() => props.activities.map((activity) => {
  const source = activity.started?.payload ?? activity.finished?.payload;
  const args = readPayload(activity.started?.payload, "args");
  const name = stringValue(readPayload(source, "name"));
  const finished = activity.finished;
  const ok = readPayload(finished?.payload, "ok") === true;
  const durationMs = readPayload(finished?.payload, "durationMs");
  return {
    key: activity.key,
    name,
    action: name === "read_file" ? "读取" : name === "write_file" ? "写入" : "修改",
    args,
    detail: stringValue(readPayload(finished?.payload, "detail")),
    summary: stringValue(readPayload(finished?.payload, "summary")),
    note: activity.notes
      .map((event) => stringValue(readPayload(event.payload, "text")))
      .filter(Boolean)
      .join("\n\n"),
    ok,
    pending: finished == null,
    occurredAt: finished?.occurredAt ?? activity.started?.occurredAt ?? "",
    durationMs: typeof durationMs === "number" ? durationMs : 0,
    oldText: readStringArgument(args, "old_text", "old_string"),
    newText: readStringArgument(args, "new_text", "new_string"),
    content: stringValue(readPayload(args, "content")),
  };
}));

const readRows = computed(() => rows.value.filter((row) => row.name === "read_file"));
const changeRows = computed(() => rows.value.filter((row) =>
  ["edit_file", "write_file"].includes(row.name),
));
const failedChanges = computed(() => changeRows.value.filter((row) => !row.pending && !row.ok));
const pendingChanges = computed(() => changeRows.value.filter((row) => row.pending));
const lastFinishedChange = computed(() =>
  [...changeRows.value].reverse().find((row) => !row.pending),
);
const recovered = computed(() =>
  failedChanges.value.length > 0 && lastFinishedChange.value?.ok === true,
);
const unresolved = computed(() => lastFinishedChange.value?.ok === false);
const rereadAfterChange = computed(() => {
  let lastSuccessfulChange = -1;
  let lastSuccessfulRead = -1;
  rows.value.forEach((row, index) => {
    if (["edit_file", "write_file"].includes(row.name) && row.ok) lastSuccessfulChange = index;
    if (row.name === "read_file" && row.ok) lastSuccessfulRead = index;
  });
  return lastSuccessfulChange >= 0 && lastSuccessfulRead > lastSuccessfulChange;
});
const variant = computed(() =>
  unresolved.value ? "error" : recovered.value ? "warning" : "success",
);
const marker = computed(() =>
  unresolved.value ? "×" : recovered.value ? "!" : pendingChanges.value.length ? "·" : "✓",
);
const statusText = computed(() => {
  if (pendingChanges.value.length) return "修改中";
  if (unresolved.value) return "修改仍失败";
  if (recovered.value) return "失败后已恢复";
  return "修改完成";
});
const facts = computed(() => {
  const parts = [
    readRows.value.length ? `读取 ${readRows.value.length} 次` : "",
    changeRows.value.length ? `修改 ${changeRows.value.length} 次` : "",
    failedChanges.value.length ? `${failedChanges.value.length} 次失败` : "",
    rereadAfterChange.value ? "已重新检查" : "",
  ].filter(Boolean);
  return parts.join(" · ");
});
const elapsed = computed(() => formatElapsed(groupElapsedMs(rows.value)));
const firstTime = computed(() => rows.value[0]?.occurredAt ?? "");
const lastTime = computed(() => rows.value.at(-1)?.occurredAt ?? "");
const timeTitle = computed(() => {
  if (!firstTime.value) return "";
  const start = formatTime(firstTime.value);
  const end = lastTime.value ? formatTime(lastTime.value) : start;
  return start === end ? start : `${start}–${end}`;
});

function rowState(row: { pending: boolean; ok: boolean; action: string }): string {
  if (row.pending) return `${row.action}中`;
  return row.ok ? `${row.action}成功` : `${row.action}失败`;
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

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
</script>

<template>
  <article
    class="timeline-event tool-activity file-change-group"
    :class="{ 'is-pending': pendingChanges.length > 0 }"
    :data-variant="variant"
  >
    <div class="event-marker" aria-hidden="true">{{ marker }}</div>
    <div class="event-content">
      <header class="event-header">
        <div class="event-title-row">
          <strong>修改 <code>{{ path }}</code></strong>
          <span class="file-change-status">{{ statusText }}</span>
        </div>
        <span v-if="elapsed" class="event-meta" :title="timeTitle">{{ elapsed }}</span>
      </header>
      <p class="file-change-facts">{{ facts }}</p>

      <details class="event-detail file-change-detail">
        <summary>查看 Diff 与执行过程</summary>
        <ol class="file-change-calls">
          <li v-for="row in rows" :key="row.key" :data-state="row.pending ? 'pending' : row.ok ? 'success' : 'error'">
            <details>
              <summary>
                <span>{{ rowState(row) }}</span>
                <small v-if="row.occurredAt">{{ formatTime(row.occurredAt) }}</small>
              </summary>
              <div class="read-file-detail">
                <p v-if="row.note" class="agent-note-copy">{{ row.note }}</p>
                <p v-if="row.summary" class="tool-outcome">{{ row.summary }}</p>
                <div v-if="row.oldText" class="detail-block code-diff-before">
                  <span>修改前</span><pre>{{ row.oldText }}</pre>
                </div>
                <div v-if="row.newText" class="detail-block code-diff-after">
                  <span>修改后</span><pre>{{ row.newText }}</pre>
                </div>
                <div v-if="row.content" class="detail-block">
                  <span>写入内容</span><pre>{{ row.content }}</pre>
                </div>
                <div v-if="row.args && !row.oldText && !row.newText && !row.content" class="detail-block">
                  <span>工具参数</span><pre>{{ JSON.stringify(row.args, null, 2) }}</pre>
                </div>
                <div v-if="row.detail" class="detail-block">
                  <span>原始结果</span><pre>{{ row.detail }}</pre>
                </div>
              </div>
            </details>
          </li>
        </ol>
      </details>
    </div>
  </article>
</template>
