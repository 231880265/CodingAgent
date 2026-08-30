<script setup lang="ts">
import type { HakoEvent } from "../types/api";
import type { RunPresentation } from "../utils/runPresentation";
import MarkdownContent from "./MarkdownContent.vue";
import RunDiagnostics from "./RunDiagnostics.vue";

defineProps<{ event: HakoEvent; view: RunPresentation }>();
</script>

<template>
  <div class="assistant-answer">
    <MarkdownContent :content="view.finalText" />
    <RunDiagnostics :view="view" />
  </div>
</template>
