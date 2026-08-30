<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { Skeleton } from "vant";
import type { HakoEvent } from "../types/api";
import { formatTime, formatTokens, readPayload } from "../utils/presentation";
import { deriveRunPresentation } from "../utils/runPresentation";
import EventItem from "./EventItem.vue";
import GoalResult from "./GoalResult.vue";
import ReadActivityGroup from "./ReadActivityGroup.vue";
import ToolActivity from "./ToolActivity.vue";

type TimelineEntry =
  | { kind: "event"; key: string; runId: string; event: HakoEvent; notes: HakoEvent[] }
  | { kind: "goal"; key: string; runId: string; event: HakoEvent; notes: HakoEvent[] }
  | { kind: "result"; key: string; runId: string; event: HakoEvent; runEvents: HakoEvent[] }
  | {
      kind: "tool";
      key: string;
      runId: string;
      started: HakoEvent | null;
      finished: HakoEvent | null;
      notes: HakoEvent[];
    };

type RawActivityEntry = Exclude<TimelineEntry, { kind: "goal" } | { kind: "result" }>;
type ToolEntry = Extract<TimelineEntry, { kind: "tool" }>;
type ReadGroupEntry = {
  kind: "read-group";
  key: string;
  runId: string;
  activities: ToolEntry[];
};
type ActivityEntry = RawActivityEntry | ReadGroupEntry;

interface RunGroup {
  key: string;
  runId: string;
  goal: Extract<TimelineEntry, { kind: "goal" }> | null;
  activities: ActivityEntry[];
  result: Extract<TimelineEntry, { kind: "result" }> | null;
  assistantAt: string;
}

const props = defineProps<{ events: HakoEvent[]; active: boolean }>();
const scrollArea = ref<HTMLElement | null>(null);
const followTail = ref(true);
const hiddenTypes = new Set([
  "turn_started",
  "context_stats",
  "session_status",
  "run_status",
  "worker_exited",
  "approval_required",
  "approval_resolved",
  "run_finished",
]);

const entries = computed<TimelineEntry[]>(() => {
  const result: TimelineEntry[] = [];
  const pendingTools = new Map<string, Extract<TimelineEntry, { kind: "tool" }>>();
  let pendingNotes: HakoEvent[] = [];
  let currentRunId = "legacy-run";

  const runEvents = (runId: string): HakoEvent[] =>
    props.events.filter((event) => (event.runId ?? "legacy-run") === runId);
  const takeNotes = (): HakoEvent[] => {
    const notes = pendingNotes;
    pendingNotes = [];
    return notes;
  };

  for (const event of props.events) {
    const runId = event.runId ?? currentRunId;
    if (event.runId) currentRunId = event.runId;

    if (event.type === "assistant_text") {
      pendingNotes.push(event);
      continue;
    }
    if (event.type === "run_started") {
      result.push({
        kind: "goal",
        key: `user-${runId}-${event.eventId}`,
        runId,
        event,
        notes: takeNotes(),
      });
      continue;
    }
    if (event.type === "run_result") {
      pendingNotes = [];
      result.push({
        kind: "result",
        key: `result-${runId}-${event.eventId}`,
        runId,
        event,
        runEvents: runEvents(runId),
      });
      continue;
    }
    if (event.type === "tool_call_started") {
      const callId = stringValue(readPayload(event.payload, "callId")) || `event-${event.eventId}`;
      const entry: Extract<TimelineEntry, { kind: "tool" }> = {
        kind: "tool",
        key: `tool-${callId}-${event.eventId}`,
        runId,
        started: event,
        finished: null,
        notes: takeNotes(),
      };
      pendingTools.set(callId, entry);
      result.push(entry);
      continue;
    }
    if (event.type === "tool_call_finished") {
      const callId = stringValue(readPayload(event.payload, "callId"));
      const entry = pendingTools.get(callId);
      if (entry) {
        entry.finished = event;
        pendingTools.delete(callId);
      } else {
        result.push({
          kind: "tool",
          key: `tool-result-${callId || event.eventId}-${event.eventId}`,
          runId,
          started: null,
          finished: event,
          notes: takeNotes(),
        });
      }
      continue;
    }
    if (!hiddenTypes.has(event.type)) {
      result.push({
        kind: "event",
        key: `event-${event.eventId}`,
        runId,
        event,
        notes: takeNotes(),
      });
    }
  }

  for (const event of pendingNotes) {
    result.push({
      kind: "event",
      key: `note-${event.eventId}`,
      runId: event.runId ?? currentRunId,
      event,
      notes: [],
    });
  }

  return result;
});

const groups = computed<RunGroup[]>(() => {
  const ordered: RunGroup[] = [];
  const byRunId = new Map<string, RunGroup>();

  const ensureGroup = (runId: string): RunGroup => {
    const existing = byRunId.get(runId);
    if (existing) return existing;
    const group: RunGroup = {
      key: `run-${runId}`,
      runId,
      goal: null,
      activities: [],
      result: null,
      assistantAt: "",
    };
    byRunId.set(runId, group);
    ordered.push(group);
    return group;
  };

  for (const entry of entries.value) {
    const group = ensureGroup(entry.runId);
    if (entry.kind === "goal") {
      group.goal = entry;
      continue;
    }
    if (entry.kind === "result") {
      group.result = entry;
      group.assistantAt = entry.event.occurredAt;
      continue;
    }
    group.activities.push(entry);
    group.assistantAt = entryTime(entry) || group.assistantAt;
  }

  for (const group of ordered) {
    if (!group.assistantAt) group.assistantAt = group.goal?.event.occurredAt ?? "";
    if (!group.result) continue;
    const presentation = deriveRunPresentation(group.result.event, group.result.runEvents);
    if (["conversation", "analysis"].includes(presentation.kind)) {
      group.activities = [];
    }
  }

  for (const group of ordered) {
    group.activities = collapseReadActivities(group.activities);
  }

  return ordered;
});

const latestRunId = computed(() => {
  for (const event of [...props.events].reverse()) {
    if (event.runId) return event.runId;
  }
  return groups.value.at(-1)?.runId ?? "legacy-run";
});

const runtimeFacts = computed(() => {
  const turn = [...props.events].reverse().find((event) => event.type === "turn_started");
  const context = [...props.events].reverse().find((event) => event.type === "context_stats");
  const status = [...props.events].reverse().find((event) => event.type === "run_status");
  return {
    step: numberValue(readPayload(turn?.payload, "step")),
    maxSteps: numberValue(readPayload(turn?.payload, "maxSteps")),
    usedTokens: numberValue(readPayload(context?.payload, "usedTokens")),
    contextLimit: numberValue(readPayload(context?.payload, "limit")),
    messageCount: numberValue(readPayload(context?.payload, "messageCount")),
    statusReason: stringValue(readPayload(status?.payload, "reason")),
    approvalCount: props.events.filter((event) => event.type === "approval_required").length,
  };
});
const hasRuntimeFacts = computed(() =>
  props.events.some((event) => hiddenTypes.has(event.type)),
);

watch(
  () => props.events.length,
  async () => {
    if (!followTail.value) return;
    await nextTick();
    const element = scrollArea.value;
    if (element) element.scrollTop = element.scrollHeight;
  },
);

function handleScroll(): void {
  const element = scrollArea.value;
  if (!element) return;
  followTail.value = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function entryTime(entry: ActivityEntry): string {
  if (entry.kind === "read-group") {
    const last = entry.activities.at(-1);
    return last ? entryTime(last) : "";
  }
  if (entry.kind === "tool") {
    return entry.finished?.occurredAt ?? entry.started?.occurredAt ?? "";
  }
  return entry.event.occurredAt;
}

function collapseReadActivities(activities: ActivityEntry[]): ActivityEntry[] {
  const collapsed: ActivityEntry[] = [];
  let reads: ToolEntry[] = [];

  const flushReads = (): void => {
    const first = reads[0];
    if (!first) return;
    if (reads.length === 1) {
      collapsed.push(first);
    } else if (reads.length > 1) {
      collapsed.push({
        kind: "read-group",
        key: `read-group-${first.key}-${reads.at(-1)?.key}`,
        runId: first.runId,
        activities: reads,
      });
    }
    reads = [];
  };

  for (const activity of activities) {
    if (activity.kind === "tool" && toolName(activity) === "read_file") {
      reads.push(activity);
      continue;
    }
    flushReads();
    collapsed.push(activity);
  }
  flushReads();
  return collapsed;
}

function toolName(activity: ToolEntry): string {
  const payload = activity.started?.payload ?? activity.finished?.payload;
  return stringValue(readPayload(payload, "name"));
}
</script>

<template>
  <div ref="scrollArea" class="timeline-scroll" @scroll="handleScroll">
    <div v-if="events.length" class="timeline-list" aria-live="polite">
      <section
        v-for="group in groups"
        :key="group.key"
        class="conversation-turn"
        :data-run-id="group.runId"
      >
        <EventItem
          v-if="group.goal"
          :event="group.goal.event"
          :notes="group.goal.notes"
        />

        <section
          v-if="group.activities.length || group.result || (active && group.runId === latestRunId)"
          class="assistant-message"
          aria-label="hako 回复"
        >
          <header class="assistant-role">
            <span class="assistant-avatar" aria-hidden="true">h</span>
            <span class="assistant-identity">
              <strong>hako</strong>
              <time v-if="group.assistantAt" :datetime="group.assistantAt">{{ formatTime(group.assistantAt) }}</time>
            </span>
          </header>

          <div class="assistant-content">
            <details
              v-if="active && group.runId === latestRunId && hasRuntimeFacts"
              class="runtime-details"
            >
              <summary>
                运行详情
                <span v-if="runtimeFacts.step != null">模型决策 {{ runtimeFacts.step }} / {{ runtimeFacts.maxSteps }}</span>
              </summary>
              <dl>
                <div v-if="runtimeFacts.usedTokens != null">
                  <dt>上下文</dt>
                  <dd>{{ formatTokens(runtimeFacts.usedTokens) }} / {{ formatTokens(runtimeFacts.contextLimit) }} tokens</dd>
                </div>
                <div v-if="runtimeFacts.messageCount != null">
                  <dt>消息数</dt>
                  <dd>{{ runtimeFacts.messageCount }}</dd>
                </div>
                <div v-if="runtimeFacts.statusReason">
                  <dt>状态说明</dt>
                  <dd>{{ runtimeFacts.statusReason }}</dd>
                </div>
                <div v-if="runtimeFacts.approvalCount">
                  <dt>人工审批</dt>
                  <dd>{{ runtimeFacts.approvalCount }} 次</dd>
                </div>
              </dl>
            </details>

            <div v-if="group.activities.length" class="assistant-activity-stream" aria-label="内部执行过程">
              <template v-for="entry in group.activities" :key="entry.key">
                <ReadActivityGroup
                  v-if="entry.kind === 'read-group'"
                  :activities="entry.activities"
                />
                <ToolActivity
                  v-else-if="entry.kind === 'tool'"
                  :started="entry.started"
                  :finished="entry.finished"
                  :notes="entry.notes"
                />
                <EventItem
                  v-else
                  :event="entry.event"
                  :notes="entry.notes"
                />
              </template>
            </div>

            <GoalResult
              v-if="group.result"
              :event="group.result.event"
              :events="group.result.runEvents"
            />
            <p v-else-if="active && group.runId === latestRunId && !group.activities.length" class="assistant-pending">
              正在理解任务…
            </p>
          </div>
        </section>
      </section>
    </div>

    <div v-else-if="active" class="timeline-loading" aria-live="polite">
      <Skeleton title :row="4" />
      <p>Worker 正在启动，第一条事件到达后会显示在这里。</p>
    </div>

    <div v-else class="timeline-empty">
      <h3>选择工作区，描述任务</h3>
    </div>
  </div>
</template>
