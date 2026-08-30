<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { Stepper } from "vant";
import type {
  AttachmentInput,
  CreateRunRequest,
  CreateSessionRequest,
  SessionResource,
} from "../types/api";

const props = defineProps<{
  active: boolean;
  busy: boolean;
  disabled: boolean;
  mode: "mock" | "api";
  session: SessionResource | null;
}>();

const emit = defineEmits<{
  start: [request: CreateSessionRequest];
  continue: [request: CreateRunRequest];
  cancel: [];
}>();

const form = reactive({
  workspace: props.mode === "mock" ? "D:\\demo\\router-header" : "",
  prompt: "",
  maxSteps: 40,
});
const attachments = ref<AttachmentInput[]>([]);
const validationMessage = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
let previousRunId = "";

const sendDisabled = computed(() => {
  if (props.active || props.busy || props.disabled || !form.prompt.trim()) return true;
  if (!props.session) return !form.workspace.trim();
  return !props.session.canContinue;
});
const cancelPending = computed(
  () => props.busy || props.session?.currentRun.status === "CANCELLING",
);
const placeholder = computed(() => {
  if (props.disabled) return "正在查看历史会话；返回当前会话后可继续输入";
  if (props.active) return "当前 Run 执行中…";
  if (props.session?.canContinue) return "继续追问，或追加一个新的工程目标";
  if (props.session) return "等待当前 Run 或 Session 状态稳定…";
  return "描述要完成的代码任务";
});

watch(
  () => props.session,
  (session, previous) => {
    if (!session) {
      if (previous) form.workspace = previous.workspace;
      form.prompt = "";
      attachments.value = [];
      previousRunId = "";
      return;
    }
    form.workspace = session.workspace;
    form.maxSteps = session.currentRun.options.maxSteps;
    if (session.currentRun.runId !== previousRunId) {
      form.prompt = "";
      attachments.value = [];
      previousRunId = session.currentRun.runId;
    }
  },
  { deep: true },
);

function chooseAttachments(): void {
  if (props.active || props.busy || props.disabled) return;
  fileInput.value?.click();
}

async function addAttachments(event: Event): Promise<void> {
  validationMessage.value = "";
  const input = event.target as HTMLInputElement;
  const files = [...(input.files ?? [])];
  input.value = "";
  if (attachments.value.length + files.length > 5) {
    validationMessage.value = "每个 Run 最多附加 5 个文本文件。";
    return;
  }
  const next = [...attachments.value];
  for (const file of files) {
    const mediaType = file.type || inferMediaType(file.name);
    if (!isTextAttachment(file.name, mediaType)) {
      validationMessage.value = `${file.name} 不是当前支持的文本、日志或代码附件。`;
      return;
    }
    const content = await file.text();
    if (!content.trim() || content.includes("\0")) {
      validationMessage.value = `${file.name} 为空或疑似二进制文件。`;
      return;
    }
    next.push({ name: file.name, mediaType, content });
  }
  if (requestBytes(form.prompt, next) > 48 * 1024) {
    validationMessage.value = "任务文字与附件合计不能超过约 48 KiB。请截取最相关的日志片段。";
    return;
  }
  attachments.value = next;
}

function removeAttachment(index: number): void {
  attachments.value.splice(index, 1);
}

function submit(): void {
  validationMessage.value = "";
  const prompt = form.prompt.trim();
  if (!prompt || sendDisabled.value) return;
  if (prompt.length > 20_000 || requestBytes(prompt, attachments.value) > 48 * 1024) {
    validationMessage.value = "任务文字与附件合计不能超过约 48 KiB。";
    return;
  }

  if (props.session) {
    emit("continue", {
      prompt,
      attachments: [...attachments.value],
      options: { maxSteps: Number(form.maxSteps) },
    });
    return;
  }

  const workspace = form.workspace.trim();
  if (!workspace) {
    validationMessage.value = "请先设置 Agent 实际操作的本机工作区。";
    return;
  }
  if (!/^(?:[A-Za-z]:[\\/]|\/)/.test(workspace)) {
    validationMessage.value = "工作区必须使用绝对路径。";
    return;
  }
  emit("start", {
    workspace,
    prompt,
    attachments: [...attachments.value],
    options: { maxSteps: Number(form.maxSteps) },
  });
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  if (!sendDisabled.value) submit();
}

function isTextAttachment(name: string, mediaType: string): boolean {
  if (mediaType.startsWith("text/")) return true;
  if (["application/json", "application/xml", "application/yaml", "application/x-yaml", "application/toml", "application/javascript"].includes(mediaType)) return true;
  return /\.(?:log|txt|md|json|ya?ml|toml|xml|csv|py|java|kt|go|rs|c|cc|cpp|h|hpp|js|ts|vue|css|html|sql|sh|ps1)$/i.test(name);
}

function inferMediaType(name: string): string {
  if (/\.json$/i.test(name)) return "application/json";
  if (/\.xml$/i.test(name)) return "application/xml";
  return "text/plain";
}

function requestBytes(prompt: string, values: AttachmentInput[]): number {
  return new TextEncoder().encode(
    prompt + values.map((value) => value.name + value.mediaType + value.content).join(""),
  ).length;
}
</script>

<template>
  <form class="conversation-composer" @submit.prevent="submit">
    <div class="composer-box" :data-disabled="active || disabled">
      <div v-if="!session" class="composer-workspace-row">
        <span class="folder-mark" aria-hidden="true"></span>
        <label class="visually-hidden" for="workspace-path">工作区绝对路径</label>
        <input
          v-model="form.workspace"
          id="workspace-path"
          name="workspace"
          type="text"
          autocomplete="off"
          placeholder="D:\\path\\to\\repository"
          aria-label="工作区绝对路径"
        />
      </div>
      <div v-if="attachments.length" class="attachment-list" aria-label="本轮附件">
        <span v-for="(attachment, index) in attachments" :key="`${attachment.name}-${index}`" class="attachment-chip">
          <span>{{ attachment.name }}</span>
          <button type="button" :aria-label="`移除 ${attachment.name}`" @click="removeAttachment(index)">×</button>
        </span>
      </div>
      <textarea
        v-model="form.prompt"
        id="goal-prompt"
        name="prompt"
        :disabled="active || disabled || (Boolean(session) && !session?.canContinue)"
        :placeholder="placeholder"
        :maxlength="20000"
        rows="1"
        aria-label="工程任务"
        @keydown="handleKeydown"
      ></textarea>
      <input
        ref="fileInput"
        class="visually-hidden"
        name="attachments"
        type="file"
        multiple
        accept=".log,.txt,.md,.json,.yaml,.yml,.toml,.xml,.csv,.py,.java,.kt,.go,.rs,.c,.cc,.cpp,.h,.hpp,.js,.ts,.vue,.css,.html,.sql,.sh,.ps1,text/*,application/json,application/xml"
        @change="addAttachments"
      />
      <div class="composer-toolbar">
        <div class="composer-left-actions">
          <button
            type="button"
            class="attachment-button"
            :disabled="active || busy || disabled"
            title="给当前会话添加文本附件"
            aria-label="添加附件"
            @click="chooseAttachments"
          >
            <span aria-hidden="true">＋</span>
          </button>
          <details v-if="!session" class="composer-settings">
            <summary>设置</summary>
            <div>
              <span>最大模型决策</span>
              <Stepper v-model="form.maxSteps" :min="1" :max="100" integer />
            </div>
          </details>
        </div>
        <div class="composer-actions">
          <button
            v-if="active"
            type="button"
            class="stop-button"
            :disabled="cancelPending"
            :aria-label="cancelPending ? '正在停止当前 Run' : '停止当前 Run'"
            title="停止本轮"
            @click="emit('cancel')"
          >
            <span v-if="cancelPending" class="send-spinner" aria-hidden="true"></span>
            <span v-else class="stop-glyph" aria-hidden="true"></span>
          </button>
          <button
            v-else
            type="submit"
            class="send-button"
            :disabled="sendDisabled"
            :aria-label="session ? '发送后续 Run' : '创建 Session 并开始 Run'"
          >
            <span v-if="busy" class="send-spinner" aria-hidden="true"></span>
            <span v-else aria-hidden="true">↑</span>
          </button>
        </div>
      </div>
    </div>

    <p v-if="validationMessage" class="composer-validation" role="alert">
      {{ validationMessage }}
    </p>
  </form>
</template>
