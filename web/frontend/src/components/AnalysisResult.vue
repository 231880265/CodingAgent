<script setup lang="ts">
import { computed } from "vue";
import type { HakoEvent } from "../types/api";
import type { RunPresentation, ToolActivityPair } from "../utils/runPresentation";
import { readPayload } from "../utils/presentation";
import MarkdownContent from "./MarkdownContent.vue";
import ReadActivityGroup from "./ReadActivityGroup.vue";
import RunDiagnostics from "./RunDiagnostics.vue";
import ToolActivity from "./ToolActivity.vue";

type TraceEntry =
  | { kind: "tool"; key: string; activity: ToolActivityPair }
  | { kind: "read-group"; key: string; activities: ToolActivityPair[] };

const props = defineProps<{ event: HakoEvent; view: RunPresentation }>();
const traceEntries = computed<TraceEntry[]>(() => {
  const entries: TraceEntry[] = [];
  let reads: ToolActivityPair[] = [];
  const flushReads = (): void => {
    const first = reads[0];
    if (!first) return;
    if (reads.length === 1) {
      entries.push({ kind: "tool", key: first.key, activity: first });
    } else if (reads.length > 1) {
      entries.push({
        kind: "read-group",
        key: `analysis-read-group-${first.key}-${reads.at(-1)?.key}`,
        activities: reads,
      });
    }
    reads = [];
  };

  for (const activity of props.view.toolActivities) {
    if (toolName(activity) === "read_file") {
      reads.push(activity);
      continue;
    }
    flushReads();
    entries.push({ kind: "tool", key: activity.key, activity });
  }
  flushReads();
  return entries;
});

function toolName(activity: ToolActivityPair): string {
  const payload = activity.started?.payload ?? activity.finished?.payload;
  const value = readPayload(payload, "name");
  return typeof value === "string" ? value : "";
}
</script>

<template>
  <section class="analysis-result">
    <header class="result-heading">
      <div>
        <span class="result-eyebrow">Analysis Result</span>
        <h3>仓库分析完成</h3>
      </div>
    </header>

    <MarkdownContent :content="view.finalText" />

    <section v-if="view.investigatedPaths.length" class="analysis-scope">
      <h4>调查范围</h4>
      <ul>
        <li v-for="path in view.investigatedPaths" :key="path"><code>{{ path }}</code></li>
      </ul>
    </section>

    <details v-if="view.toolActivities.length" class="analysis-trace">
      <summary>查看调查过程</summary>
      <div class="analysis-trace-list">
        <template v-for="entry in traceEntries" :key="entry.key">
          <ReadActivityGroup
            v-if="entry.kind === 'read-group'"
            :activities="entry.activities"
          />
          <ToolActivity
            v-else
            :started="entry.activity.started"
            :finished="entry.activity.finished"
            :notes="entry.activity.notes"
          />
        </template>
      </div>
    </details>
    <RunDiagnostics :view="view" />
  </section>
</template>
