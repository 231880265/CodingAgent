<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { Skeleton } from "vant";
import type { HakoEvent } from "../types/api";
import {
  formatTime,
  formatTokens,
  readPayload,
  readStringArgument,
} from "../utils/presentation";
import { deriveRunPresentation } from "../utils/runPresentation";
import EventItem from "./EventItem.vue";
import GoalResult from "./GoalResult.vue";
import FileChangeGroup from "./FileChangeGroup.vue";
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
type ReadGroupEntry =
  | { kind: "exploration-group"; key: string; runId: string; activities: ToolEntry[] }
  | {
      kind: "file-change-group";
      key: string;
      runId: string;
      path: string;
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

interface ActiveProgress {
  label: string;
  animated: boolean;
}

const props = defineProps<{ events: HakoEvent[]; active: boolean }>();
const scrollArea = ref<HTMLElement | null>(null);
const followTail = ref(true);
const AUTO_SCROLL_THROTTLE_MS = 80;
const BOTTOM_TOLERANCE_PX = 96;
let autoScrollTimer: ReturnType<typeof setTimeout> | null = null;
const hiddenTypes = new Set([
  "turn_started",
  "context_stats",
  "session_status",
  "run_status",
  "worker_exited",
  "acceptance_planned",
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
    group.activities = collapseToolActivities(group.activities);
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

const activeProgress = computed<ActiveProgress | null>(() => {
  if (!props.active) return null;
  const events = props.events.filter(
    (event) => (event.runId ?? "legacy-run") === latestRunId.value,
  );
  if (!events.length) return { label: "正在启动专属 Worker", animated: true };

  const pendingCalls = new Set<string>();
  for (const event of events) {
    if (event.type === "tool_call_started") {
      pendingCalls.add(stringValue(readPayload(event.payload, "callId")));
    } else if (event.type === "tool_call_finished") {
      pendingCalls.delete(stringValue(readPayload(event.payload, "callId")));
    }
  }
  // 未完成工具已经由 ToolActivity 自身显示真实动作与参数，避免重复一行。
  if (pendingCalls.size) return null;

  const lastApproval = [...events].reverse().find(
    (event) => ["approval_required", "approval_resolved"].includes(event.type),
  );
  if (lastApproval?.type === "approval_required") {
    return { label: "等待你确认后继续", animated: false };
  }

  const last = events.at(-1);
  if (!last || ["run_result", "run_finished", "run_cancelled"].includes(last.type)) return null;

  // turn/context 事件只是诊断心跳。面向用户的进度应回看最近一次真实说明或工具结果，
  // 这样既比固定文案具体，也不会凭空声称已经找到某个根因。
  const significant = [...events].reverse().find((event) =>
    ["assistant_text", "tool_call_finished"].includes(event.type),
  );
  if (significant?.type === "assistant_text") {
    const summary = summarizeAssistantText(
      stringValue(readPayload(significant.payload, "text")),
    );
    return {
      label: summary ? `正在根据当前判断继续：${summary}` : "正在选择下一步操作",
      animated: true,
    };
  }
  if (significant?.type === "tool_call_finished") {
    return progressAfterTool(events, significant);
  }
  return { label: "正在思考您的问题…", animated: true };
});

watch(
  () => props.events.length,
  () => scheduleAutoScroll(),
  { flush: "post", immediate: true },
);

function handleScroll(): void {
  const element = scrollArea.value;
  if (!element) return;
  followTail.value = distanceFromBottom(element) <= BOTTOM_TOLERANCE_PX;
}

function handleWheel(event: WheelEvent): void {
  // 在浏览器完成滚动布局前先记录用户向上浏览的意图，防止已排队的自动滚动抢回底部。
  if (event.deltaY < 0) followTail.value = false;
}

function scheduleAutoScroll(): void {
  if (!followTail.value || autoScrollTimer) return;
  autoScrollTimer = setTimeout(() => {
    autoScrollTimer = null;
    if (!followTail.value) return;
    void nextTick().then(() => {
      const element = scrollArea.value;
      if (!element || !followTail.value) return;
      element.scrollTop = element.scrollHeight;
    });
  }, AUTO_SCROLL_THROTTLE_MS);
}

function distanceFromBottom(element: HTMLElement): number {
  return Math.max(0, element.scrollHeight - element.scrollTop - element.clientHeight);
}

onBeforeUnmount(() => {
  if (autoScrollTimer) clearTimeout(autoScrollTimer);
  autoScrollTimer = null;
});

function numberValue(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function progressAfterTool(events: HakoEvent[], event: HakoEvent): ActiveProgress {
  const name = stringValue(readPayload(event.payload, "name"));
  const ok = readPayload(event.payload, "ok") === true;
  const subject = toolSubject(events, event);
  if (name === "read_file") {
    return {
      label: subject
        ? `已读取 ${compactPath(subject)}，正在结合相关代码定位问题`
        : "已读取代码，正在结合相关实现定位问题",
      animated: true,
    };
  }
  if (name === "list_dir") {
    return {
      label: subject
        ? `已查看 ${compactPath(subject)}，正在选择需要深入读取的文件`
        : "已查看仓库结构，正在选择需要深入读取的文件",
      animated: true,
    };
  }
  if (name === "delegate_readonly") {
    return { label: "只读调查已返回，正在整合调查结论", animated: true };
  }
  if (["edit_file", "write_file"].includes(name)) {
    const count = changedPathCount(events);
    return {
      label: count
        ? `已修改 ${count} 个文件，正在检查影响并选择验证方式`
        : "修改已落盘，正在检查影响并选择验证方式",
      animated: true,
    };
  }
  if (name === "run_command") {
    const verificationKind = stringValue(readPayload(event.payload, "verificationKind"));
    const noun = verificationKind === "test"
      ? "测试"
      : verificationKind === "build"
        ? "构建"
        : verificationKind === "check"
          ? "静态检查"
          : "命令";
    if (!ok) return { label: `${noun}未通过，正在根据输出调整下一步`, animated: true };
    if (verificationKind) {
      return { label: `${noun}已通过，正在检查是否满足完成条件`, animated: true };
    }
    return { label: "命令执行完成，正在分析输出", animated: true };
  }
  return { label: "正在继续处理当前任务", animated: true };
}

function toolSubject(events: HakoEvent[], finished: HakoEvent): string {
  const touched = stringList(readPayload(finished.payload, "touchedPaths"));
  if (touched[0]) return touched[0];
  const callId = stringValue(readPayload(finished.payload, "callId"));
  const started = [...events].reverse().find((event) =>
    event.type === "tool_call_started"
      && stringValue(readPayload(event.payload, "callId")) === callId,
  );
  const args = readPayload(started?.payload, "args");
  return readStringArgument(args, "path", "file_path") || stringValue(readPayload(args, "command"));
}

function changedPathCount(events: HakoEvent[]): number {
  const paths = new Set<string>();
  for (const event of events) {
    if (event.type !== "tool_call_finished") continue;
    if (readPayload(event.payload, "ok") !== true) continue;
    const name = stringValue(readPayload(event.payload, "name"));
    if (!["edit_file", "write_file"].includes(name)) continue;
    for (const key of ["createdPaths", "modifiedPaths", "deletedPaths"]) {
      for (const path of stringList(readPayload(event.payload, key))) paths.add(path);
    }
  }
  return paths.size;
}

function compactPath(value: string): string {
  const parts = value.replaceAll("\\", "/").split("/").filter(Boolean);
  return parts.slice(-2).join("/") || value;
}

function summarizeAssistantText(value: string): string {
  const plain = value
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[`*_>#-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!plain) return "";
  const first = plain.split(/(?<=[。！？!?])\s*/)[0] || plain;
  return first.length > 72 ? `${first.slice(0, 72)}…` : first;
}

function entryTime(entry: ActivityEntry): string {
  if (
    entry.kind === "exploration-group"
    || entry.kind === "file-change-group"
  ) {
    const last = entry.activities.at(-1);
    return last ? entryTime(last) : "";
  }
  if (entry.kind === "tool") {
    return entry.finished?.occurredAt ?? entry.started?.occurredAt ?? "";
  }
  return entry.event.occurredAt;
}

function collapseToolActivities(activities: ActivityEntry[]): ActivityEntry[] {
  const collapsed: ActivityEntry[] = [];
  let stage: ToolEntry[] = [];

  const flushStage = (): void => {
    const first = stage[0];
    if (!first) return;

    const buckets = new Map<string, {
      path: string;
      firstIndex: number;
      activities: ToolEntry[];
      changed: boolean;
    }>();
    stage.forEach((activity, index) => {
      const path = toolPath(activity) || `调用 ${index + 1}`;
      const key = normalizePath(path) || `__unknown-${index}`;
      const bucket = buckets.get(key) ?? {
        path,
        firstIndex: index,
        activities: [],
        changed: false,
      };
      bucket.activities.push(activity);
      bucket.changed ||= ["edit_file", "write_file"].includes(toolName(activity));
      buckets.set(key, bucket);
    });

    const output: Array<{
      firstIndex: number;
      entry: ReadGroupEntry;
    }> = [];
    const exploration: Array<{ index: number; activity: ToolEntry }> = [];
    for (const bucket of buckets.values()) {
      if (bucket.changed) {
        output.push({
          firstIndex: bucket.firstIndex,
          entry: {
            kind: "file-change-group",
            key: `file-change-${normalizePath(bucket.path)}-${bucket.activities[0]?.key}-${bucket.activities.at(-1)?.key}`,
            runId: first.runId,
            path: bucket.path,
            activities: bucket.activities,
          },
        });
      } else {
        for (const activity of bucket.activities) {
          exploration.push({ index: stage.indexOf(activity), activity });
        }
      }
    }
    if (exploration.length) {
      exploration.sort((left, right) => left.index - right.index);
      output.push({
        firstIndex: exploration[0]!.index,
        entry: {
          kind: "exploration-group",
          key: `exploration-group-${exploration[0]!.activity.key}-${exploration.at(-1)?.activity.key}`,
          runId: first.runId,
          activities: exploration.map((item) => item.activity),
        },
      });
    }
    output.sort((left, right) => left.firstIndex - right.firstIndex);
    collapsed.push(...output.map((item) => item.entry));
    stage = [];
  };

  for (const activity of activities) {
    const name = activity.kind === "tool" ? toolName(activity) : "";
    if (["read_file", "list_dir", "edit_file", "write_file"].includes(name)) {
      if (stage.length && beginsNewFileStage(activity as ToolEntry)) flushStage();
      stage.push(activity as ToolEntry);
      continue;
    }
    // 命令、审批、错误和其他非文件事件都是阶段边界。同一路径在边界后
    // 再次返修会生成新卡，避免把“初次实现”和“测试后修复”混为一谈。
    flushStage();
    collapsed.push(activity);
  }
  flushStage();
  return collapsed;
}

function toolName(activity: ToolEntry): string {
  const payload = activity.started?.payload ?? activity.finished?.payload;
  return stringValue(readPayload(payload, "name"));
}

function toolPath(activity: ToolEntry): string {
  const args = readPayload(activity.started?.payload, "args");
  const requested = readStringArgument(args, "path", "file_path", "file", "filePath");
  if (requested) return requested;
  for (const key of ["modifiedPaths", "createdPaths", "deletedPaths", "touchedPaths"]) {
    const candidate = stringList(readPayload(activity.finished?.payload, key))[0];
    if (candidate) return candidate;
  }
  return "";
}

function normalizePath(value: string): string {
  return value
    .replaceAll("\\", "/")
    .replace(/\/{2,}/g, "/")
    .replace(/^\.\//, "")
    .replace(/\/+$/, "")
    .toLowerCase();
}

function beginsNewFileStage(activity: ToolEntry): boolean {
  const note = activity.notes
    .map((event) => stringValue(readPayload(event.payload, "text")))
    .join(" ");
  return /(?:接下来|下一步|现在(?:运行|验证|测试)|开始(?:验证|测试)|重新(?:检查|验证)|根据.{0,16}(?:失败|结果))/u.test(note);
}
</script>

<template>
  <div
    ref="scrollArea"
    class="timeline-scroll"
    @scroll.passive="handleScroll"
    @wheel.passive="handleWheel"
  >
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
                <span v-if="runtimeFacts.step != null">模型决策 {{ runtimeFacts.step }} 次</span>
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
                  v-if="entry.kind === 'exploration-group'"
                  :activities="entry.activities"
                  kind="exploration"
                />
                <FileChangeGroup
                  v-else-if="entry.kind === 'file-change-group'"
                  :path="entry.path"
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

            <p
              v-if="active && group.runId === latestRunId && activeProgress"
              class="assistant-pending"
              :data-waiting="!activeProgress.animated"
              aria-live="polite"
            >
              {{ activeProgress.label }}<span v-if="activeProgress.animated" class="progress-dots" aria-hidden="true">...</span>
            </p>

            <GoalResult
              v-if="group.result"
              :event="group.result.event"
              :events="group.result.runEvents"
            />
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
