package dev.hako.web.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

import dev.hako.web.domain.RunState;
import dev.hako.web.domain.RunStatus;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

class RunMemoryBuilderTest {
    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void hardExitCodeWinsEvenWhenAgentTextClaimsSuccess() {
        UUID sessionId = UUID.randomUUID();
        RunState run = new RunState("修复 CSV 导出", 20, List.of());
        run.status = RunStatus.FAILED;
        run.startedAt = Instant.parse("2026-08-31T01:00:00Z");
        run.finishedAt = Instant.parse("2026-08-31T01:01:00Z");
        run.outcome = mapper.createObjectNode();
        run.outcome.put("stopReason", "done_unverified");
        run.outcome.put("finalText", "测试已经全部通过");

        ObjectNode started = envelope(sessionId, run, 1, "tool_call_started");
        started.withObject("/payload").put("callId", "pytest-1");
        started.withObject("/payload").put("name", "run_command");
        started.withObject("/payload").putObject("args").put("command", "python -m pytest -q");
        ObjectNode finished = envelope(sessionId, run, 2, "tool_call_finished");
        ObjectNode data = finished.withObject("/payload");
        data.put("callId", "pytest-1");
        data.put("name", "run_command");
        data.put("ok", false);
        data.put("summary", "1 failed");
        data.put("verificationKind", "test");
        data.put("verificationCommand", "python -m pytest -q");
        data.put("commandStatus", "failed");
        data.put("exitCode", 1);

        ObjectNode memory = new RunMemoryBuilder(mapper).build(
                sessionId, run, List.of(started, finished));

        assertEquals(1, memory.path("verifications").path(0).path("exitCode").asInt());
        assertFalse(memory.path("verifications").path(0).path("ok").asBoolean());
        assertEquals("测试已经全部通过", memory.path("semanticSummary").path("text").asText());
        assertFalse(memory.path("semanticSummary").path("authoritative").asBoolean());
    }

    @Test
    void recordsObservedFilesAndAcceptanceConstraintsFromEvents() {
        UUID sessionId = UUID.randomUUID();
        RunState run = new RunState("修复发布后仍读取旧版本", 20, List.of());
        run.status = RunStatus.COMPLETED;
        run.startedAt = Instant.parse("2026-08-31T02:00:00Z");
        run.finishedAt = Instant.parse("2026-08-31T02:01:00Z");
        run.outcome = mapper.createObjectNode();

        ObjectNode started = envelope(sessionId, run, 1, "tool_call_started");
        started.withObject("/payload").put("callId", "read-1");
        started.withObject("/payload").put("name", "read_file");
        started.withObject("/payload")
                .putObject("args")
                .put("file_path", "app/repositories/campaign_repository.py");

        ObjectNode finished = envelope(sessionId, run, 2, "tool_call_finished");
        finished.withObject("/payload").put("callId", "read-1");
        finished.withObject("/payload").put("name", "read_file");
        finished.withObject("/payload").put("ok", true);

        ObjectNode acceptance = envelope(sessionId, run, 3, "acceptance_planned");
        acceptance.withObject("/payload")
                .putArray("items")
                .add("发布后线上版本切换到新草稿")
                .add("完整回归测试通过");

        ObjectNode memory = new RunMemoryBuilder(mapper).build(
                sessionId, run, List.of(started, finished, acceptance));

        assertEquals(
                "app/repositories/campaign_repository.py",
                memory.path("observedFiles").path(0).asText());
        assertEquals("发布后线上版本切换到新草稿", memory.path("constraints").path(0).asText());
        assertEquals("完整回归测试通过", memory.path("constraints").path(1).asText());
    }

    private ObjectNode envelope(UUID sessionId, RunState run, long eventId, String type) {
        ObjectNode event = mapper.createObjectNode();
        event.put("eventId", eventId);
        event.put("sessionId", sessionId.toString());
        event.put("runId", run.runId.toString());
        event.put("type", type);
        event.put("occurredAt", "2026-08-31T01:00:00Z");
        event.putObject("payload");
        return event;
    }
}
