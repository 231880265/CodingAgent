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
