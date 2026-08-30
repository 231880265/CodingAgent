<script setup lang="ts">
import type { HakoEvent } from "../types/api";
import type { RunPresentation } from "../utils/runPresentation";
import MarkdownContent from "./MarkdownContent.vue";
import RunDiagnostics from "./RunDiagnostics.vue";

defineProps<{ event: HakoEvent; view: RunPresentation }>();

function verificationLabel(kind: string): string {
  if (kind === "test") return "测试";
  if (kind === "build") return "构建";
  if (kind === "check") return "检查";
  return "验证";
}
</script>

<template>
  <section class="verified-result">
    <div class="verified-mark" aria-hidden="true">✓</div>
    <div>
      <header class="result-heading">
        <div>
          <span class="result-eyebrow">Verified Result</span>
          <h3>修改已验证</h3>
        </div>
      </header>

      <dl class="result-overview">
        <div>
          <dt>修改</dt>
          <dd><code v-for="path in view.changedPaths" :key="path">{{ path }}</code></dd>
        </div>
        <div>
          <dt>验证</dt>
          <dd>
            <span v-for="item in view.verification" :key="`${item.step}-${item.command}`">
              {{ verificationLabel(item.kind) }} · {{ item.summary }}
            </span>
          </dd>
        </div>
        <div>
          <dt>结果</dt>
          <dd>最终版本已在最后一次修改后重新验证。</dd>
        </div>
      </dl>

      <details class="verified-evidence">
        <summary>查看完整交付证据</summary>
        <div class="verification-list">
          <div v-for="item in view.verification" :key="item.command">
            <strong>{{ verificationLabel(item.kind) }}通过</strong>
            <span>{{ item.summary }}</span>
            <code>{{ item.command }}</code>
          </div>
        </div>
        <MarkdownContent v-if="view.finalText" :content="view.finalText" />
      </details>
      <RunDiagnostics :view="view" />
    </div>
  </section>
</template>
