<script setup lang="ts">
import { computed } from "vue";
import type { HakoEvent } from "../types/api";
import {
  formatTime,
  formatToolName,
  readPayload,
} from "../utils/presentation";

const props = defineProps<{
  started: HakoEvent | null;
  finished: HakoEvent | null;
  notes?: HakoEvent[];
}>();

const source = computed(() => props.finished ?? props.started);
const name = computed(() =>
  String(readPayload(source.value?.payload, "name") ?? "unknown"),
);
const args = computed(() => readPayload(props.started?.payload, "args"));
const ok = computed(() => readPayload(props.finished?.payload, "ok") === true);
const verificationKind = computed(() =>
  stringValue(readPayload(props.finished?.payload, "verificationKind")),
);
const subject = computed(() => {
  const command = readPayload(args.value, "command");
  if (typeof command === "string") return command;
  const path = readPayload(args.value, "path");
  if (typeof path === "string") return path;
  const changed = pathList("modifiedPaths")[0] ?? pathList("createdPaths")[0];
  return changed ?? "";
});
const title = computed(() => {
  if (!props.finished) return pendingTitle(name.value);
  if (name.value === "run_command") {
    const noun = verificationNoun(verificationKind.value);
    return noun ? `${noun}${ok.value ? "通过" : "失败"}` : `命令执行${ok.value ? "成功" : "失败"}`;
  }
  if (name.value === "read_file") return ok.value ? "已读取文件" : "读取文件失败";
  if (name.value === "list_dir") return ok.value ? "已查看工作区" : "查看工作区失败";
  if (name.value === "edit_file") return ok.value ? "已完成局部修改" : "局部修改失败";
  if (name.value === "write_file") {
    return ok.value
      ? pathList("createdPaths").length
        ? "已创建文件"
        : "已写入文件"
      : "写入文件失败";
  }
  if (name.value === "delegate_readonly") return ok.value ? "只读调查完成" : "只读调查失败";
  return `${formatToolName(name.value)}${ok.value ? "完成" : "失败"}`;
});
const variant = computed(() => {
  if (!props.finished) return "neutral";
  if (!ok.value) return "error";
  return verificationKind.value ? "success" : "neutral";
});
const marker = computed(() => {
  if (!props.finished) return "·";
  if (!ok.value) return "×";
  return verificationKind.value ? "✓" : "›";
});
const summary = computed(() =>
  stringValue(readPayload(props.finished?.payload, "summary")),
);
const detail = computed(() =>
  stringValue(readPayload(props.finished?.payload, "detail")),
);
const duration = computed(() => {
  const value = readPayload(props.finished?.payload, "durationMs");
  return typeof value === "number" ? `${value} ms` : "";
});
const noteText = computed(() =>
  (props.notes ?? [])
    .map((event) => stringValue(readPayload(event.payload, "text")))
    .filter(Boolean)
    .join("\n\n"),
);
const outcomeLine = computed(() => {
  if (!props.finished) return "工具正在执行，结果到达后会在这里更新。";
  if (!ok.value) {
    if (/ParserError|InvalidEndOfLine|unexpected token|解析/i.test(detail.value)) {
      return "PowerShell 未接受这条组合命令；尚不能据此判断代码本身失败。";
    }
    if (/not recognized|not found|无法识别|找不到/i.test(detail.value)) {
      return "当前环境找不到这条命令；需要调整执行方式或工具链。";
    }
    return "本次执行未成功；Agent 可以根据原始输出调整下一步。";
  }
  if (name.value === "read_file") {
    return "读取完成，内容用于后续定位。";
  }
  if (name.value === "list_dir") {
    return "已获取仓库结构。";
  }
  if (["edit_file", "write_file"].includes(name.value)) {
    return "修改已落盘，等待修改后验证。";
  }
  if (name.value === "run_command" && verificationKind.value) {
    return joinSentences(summary.value, "已记录为验证证据。" );
  }
  if (name.value === "run_command") {
    return "执行成功，但不单独作为完成证据。";
  }
  return summary.value;
});
const detailsLabel = computed(() => {
  if (!ok.value && props.finished) return "查看 stderr 与原始命令";
  if (["edit_file", "write_file"].includes(name.value)) return "查看修改内容与工具参数";
  if (name.value === "run_command") return "查看执行证据";
  if (["read_file", "list_dir"].includes(name.value)) return "查看读取内容与工具参数";
  return "查看工具详情";
});
const hasDetails = computed(
  () => Boolean(args.value) || Boolean(detail.value) || Boolean(noteText.value) || Boolean(duration.value) || changeFacts.value.length > 0,
);
const changeFacts = computed(() => {
  const groups = [
    ["新增", pathList("createdPaths")],
    ["修改", pathList("modifiedPaths")],
    ["删除", pathList("deletedPaths")],
    ["构建产物", pathList("derivedPaths")],
  ] as const;
  return groups.filter(([, paths]) => paths.length > 0);
});
const oldText = computed(() => stringValue(readPayload(args.value, "old_text")));
const newText = computed(() => stringValue(readPayload(args.value, "new_text")));
const writtenContent = computed(() => stringValue(readPayload(args.value, "content")));
const hasChangePreview = computed(() =>
  Boolean(oldText.value) || Boolean(newText.value) || Boolean(writtenContent.value),
);

function pathList(key: string): string[] {
  const value = readPayload(props.finished?.payload, key);
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function pendingTitle(tool: string): string {
  if (tool === "read_file") return "正在读取文件";
  if (tool === "list_dir") return "正在查看工作区";
  if (tool === "edit_file") return "正在执行局部修改";
  if (tool === "write_file") return "正在写入文件";
  if (tool === "run_command") return "正在执行命令";
  return `正在${formatToolName(tool)}`;
}

function verificationNoun(kind: string): string {
  if (kind === "test") return "测试";
  if (kind === "build") return "构建";
  if (kind === "check") return "静态检查";
  return "";
}

function joinSentences(...values: string[]): string {
  const parts = values
    .map((value) => value.trim().replace(/[。；;]+$/, ""))
    .filter(Boolean);
  return parts.length ? `${parts.join("；")}。` : "";
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}
</script>

<template>
  <article class="timeline-event tool-activity" :data-variant="variant">
    <div class="event-marker" aria-hidden="true">{{ marker }}</div>
    <div class="event-content">
      <header class="event-header">
        <div class="event-title-row">
          <strong>{{ title }}</strong>
          <code v-if="subject" class="event-subject" :title="subject">{{ subject }}</code>
        </div>
        <time v-if="source" class="event-meta" :datetime="source.occurredAt">{{ formatTime(source.occurredAt) }}</time>
      </header>

      <p v-if="outcomeLine" class="tool-outcome">{{ outcomeLine }}</p>

      <details v-if="hasDetails" class="event-detail">
        <summary>{{ detailsLabel }}</summary>
        <p v-if="noteText" class="agent-note-copy">{{ noteText }}</p>
        <dl v-if="duration || changeFacts.length" class="tool-facts">
          <div v-if="duration"><dt>耗时</dt><dd>{{ duration }}</dd></div>
          <div v-for="[label, paths] in changeFacts" :key="label">
            <dt>{{ label }}</dt>
            <dd>{{ paths.join("、") }}</dd>
          </div>
        </dl>
        <template v-if="hasChangePreview">
          <div v-if="oldText" class="detail-block code-diff-before">
            <span>修改前</span>
            <pre>{{ oldText }}</pre>
          </div>
          <div v-if="newText" class="detail-block code-diff-after">
            <span>修改后</span>
            <pre>{{ newText }}</pre>
          </div>
          <div v-if="writtenContent" class="detail-block">
            <span>写入内容</span>
            <pre>{{ writtenContent }}</pre>
          </div>
        </template>
        <div v-else-if="args" class="detail-block">
          <span>工具参数</span>
          <pre>{{ JSON.stringify(args, null, 2) }}</pre>
        </div>
        <div v-if="detail" class="detail-block">
          <span>原始结果</span>
          <pre>{{ detail }}</pre>
        </div>
      </details>
    </div>
  </article>
</template>
