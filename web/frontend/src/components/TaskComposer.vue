<script setup lang="ts">
import { reactive, ref, watch } from "vue";
import { Button, Field, Stepper } from "vant";
import type { CreateTaskRequest, TaskResource } from "../types/api";

const props = defineProps<{
  disabled: boolean;
  busy: boolean;
  mode: "mock" | "api";
  task: TaskResource | null;
}>();

const emit = defineEmits<{
  submit: [request: CreateTaskRequest];
}>();

const form = reactive({
  workspace: "D:\\demo\\router-header",
  prompt:
    "复现 Header 大小写混用导致的路由失效，做最小修复并补充回归测试，最后运行完整测试。",
  maxSteps: 40,
});
const validationMessage = ref("");

watch(
  () => props.task,
  (task) => {
    if (!task) return;
    form.workspace = task.workspace;
    form.prompt = task.prompt;
    form.maxSteps = task.options.maxSteps;
  },
  { immediate: true },
);

function submit(): void {
  validationMessage.value = "";
  const workspace = form.workspace.trim();
  const prompt = form.prompt.trim();
  if (!workspace) {
    validationMessage.value = "请输入目标仓库的绝对路径。";
    return;
  }
  if (!/^(?:[A-Za-z]:[\\/]|\/)/.test(workspace)) {
    validationMessage.value = "workspace 必须使用绝对路径。";
    return;
  }
  if (!prompt) {
    validationMessage.value = "请说明希望 Agent 完成的工程任务。";
    return;
  }
  if (prompt.length > 20_000) {
    validationMessage.value = "任务描述不能超过 20,000 个字符。";
    return;
  }

  emit("submit", {
    workspace,
    prompt,
    options: { maxSteps: Number(form.maxSteps) },
  });
}
</script>

<template>
  <section class="composer" aria-labelledby="composer-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">TASK</p>
        <h2 id="composer-title">新任务</h2>
      </div>
      <span class="single-task-note">单任务运行</span>
    </div>

    <form class="task-form" @submit.prevent="submit">
      <label class="field-label" for="workspace">工作区</label>
      <Field
        id="workspace"
        v-model="form.workspace"
        class="control-field"
        :disabled="disabled"
        autocomplete="off"
        placeholder="D:\\path\\to\\repo"
        aria-describedby="workspace-hint"
      />
      <p id="workspace-hint" class="field-hint">
        只接受后端允许根目录内的绝对路径。
      </p>

      <label class="field-label" for="prompt">工程任务</label>
      <Field
        id="prompt"
        v-model="form.prompt"
        class="control-field task-prompt"
        type="textarea"
      :autosize="{ minHeight: 104, maxHeight: 220 }"
        :maxlength="20000"
        :disabled="disabled"
        placeholder="说明问题、期望结果和验证要求"
      />

      <div class="step-row">
        <div>
          <label class="field-label" for="max-steps">最大回合</label>
          <p class="field-hint">到达上限后明确停止，不无限消耗。</p>
        </div>
        <Stepper
          id="max-steps"
          v-model="form.maxSteps"
          :min="1"
          :max="100"
          :step="1"
          :disabled="disabled"
          integer
        />
      </div>

      <p v-if="validationMessage" class="form-error" role="alert">
        {{ validationMessage }}
      </p>

      <Button
        block
        class="start-button"
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="disabled"
        loading-text="正在创建任务"
      >
        {{ disabled ? "当前任务进行中" : "开始任务" }}
      </Button>
    </form>

    <div class="composer-footnote">
      <span aria-hidden="true">⌁</span>
      <span v-if="mode === 'mock'">演示模式不会读取或修改磁盘文件。</span>
      <span v-else>密钥只由 Python 环境读取，不进入浏览器。</span>
    </div>
  </section>
</template>
