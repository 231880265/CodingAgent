package dev.hako.web.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import dev.hako.web.api.ApiException;
import dev.hako.web.api.ApiModels.ApprovalDecision;
import dev.hako.web.api.ApiModels.CreateRunRequest;
import dev.hako.web.api.ApiModels.CreateSessionRequest;
import dev.hako.web.api.ApiModels.RunOptions;
import dev.hako.web.config.WebProperties;
import dev.hako.web.worker.ProcessWorkerLauncher;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import javax.sql.DataSource;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

class SessionServiceProcessIntegrationTest {
    Path workspace;

    private ProcessWorkerLauncher launcher;
    private SessionService service;

    @BeforeEach
    void setUp() throws Exception {
        Path repositoryRoot = Path.of(System.getProperty("user.dir"))
                .resolve("../..")
                .normalize()
                .toRealPath();
        workspace = repositoryRoot
                .resolve("web/backend/target/test-workspaces")
                .resolve(UUID.randomUUID().toString());
        Files.createDirectories(workspace);
        Path python = findPython(repositoryRoot);
        WebProperties properties = new WebProperties();
        properties.setRepositoryRoot(repositoryRoot);
        properties.setAllowedRoots(List.of(workspace));
        properties.setPythonExecutable(python.toString());
        properties.setWorkerEntrypoint(Path.of("web/worker/fake_worker.py"));
        properties.setStartTimeout(Duration.ofSeconds(3));
        properties.setCancelTimeout(Duration.ofSeconds(2));
        properties.setKillGracePeriod(Duration.ofMillis(300));
        ObjectMapper mapper = new ObjectMapper();
        launcher = new ProcessWorkerLauncher(properties, mapper);
        SessionHistoryRepository history = historyRepository(workspace.resolve("history.db"), mapper);
        service = new SessionService(properties, mapper, launcher, history);
    }

    @AfterEach
    void tearDown() {
        if (service != null) {
            service.shutdown();
        }
        if (launcher != null) {
            launcher.shutdown();
        }
    }

    @Test
    void completesAValidatedRunAndReturnsEvidence() throws Exception {
        SessionRun ids = create("fix header normalization and verify");
        approveBoth(ids.sessionId(), ids.runId());

        ObjectNode completed = awaitRunStatus(ids.sessionId(), "COMPLETED");
        assertEquals("OPEN", completed.path("status").asText());
        ObjectNode summary = service.getRunSummary(ids.sessionId(), ids.runId());
        assertTrue(summary.path("success").booleanValue());
        assertEquals("done_verified", summary.path("stopReason").asText());
        assertEquals("router/headers.py", summary.path("changedPaths").path(0).asText());
        assertEquals("test", summary.path("verification").path(0).path("kind").asText());
    }

    @Test
    void secondRunReusesWorkerAndConversationButGetsANewRunId() throws Exception {
        SessionRun first = create("first run");
        approveBoth(first.sessionId(), first.runId());
        ObjectNode firstDone = awaitRunStatus(first.sessionId(), "COMPLETED");
        String workerId = firstDone.path("worker").path("workerId").asText();

        ObjectNode continued = service.createRun(
                first.sessionId(),
                new CreateRunRequest("continue using prior context", List.of(), new RunOptions(8)));
        UUID secondRunId = runId(continued);
        assertNotEquals(first.runId(), secondRunId);
        assertEquals(2, continued.path("runCount").asInt());
        assertEquals(workerId, continued.path("worker").path("workerId").asText());

        approveBoth(first.sessionId(), secondRunId);
        awaitRunStatus(first.sessionId(), "COMPLETED");
        assertTrue(service.getRunSummary(first.sessionId(), secondRunId)
                .path("finalText").asText().contains("上一轮上下文"));
    }

    @Test
    void cancellingRunKeepsSessionAndWorkerAliveForFollowUp() throws Exception {
        SessionRun first = create("cancel this run");
        ObjectNode waiting = awaitRunStatus(first.sessionId(), "WAITING_APPROVAL");
        String workerId = waiting.path("worker").path("workerId").asText();

        assertEquals("CANCELLING", service.cancelRun(first.sessionId(), first.runId()).status());
        ObjectNode cancelled = awaitRunStatus(first.sessionId(), "CANCELLED");
        assertEquals("OPEN", cancelled.path("status").asText());
        assertEquals(workerId, cancelled.path("worker").path("workerId").asText());
        assertTrue(cancelled.path("worker").path("alive").asBoolean());

        ObjectNode followUp = service.createRun(
                first.sessionId(),
                new CreateRunRequest("try a safer follow-up", List.of(), new RunOptions(8)));
        UUID followUpId = runId(followUp);
        approveBoth(first.sessionId(), followUpId);
        awaitRunStatus(first.sessionId(), "COMPLETED");
    }

    @Test
    void denialIsAnObservationAndDoesNotCloseSession() throws Exception {
        SessionRun ids = create("deny write");
        ObjectNode waiting = awaitRunStatus(ids.sessionId(), "WAITING_APPROVAL");

        service.respondApproval(
                ids.sessionId(), ids.runId(), approvalId(waiting), ApprovalDecision.DENY);

        ObjectNode completed = awaitRunStatus(ids.sessionId(), "COMPLETED");
        assertEquals("OPEN", completed.path("status").asText());
        ObjectNode summary = service.getRunSummary(ids.sessionId(), ids.runId());
        assertEquals("done_read_only", summary.path("stopReason").asText());
        assertTrue(summary.path("changedPaths").isEmpty());
    }

    @Test
    void highRiskApprovalRejectsSessionScope() throws Exception {
        SessionRun ids = create("[fake:high-risk] approval demo");
        ObjectNode waiting = awaitRunStatus(ids.sessionId(), "WAITING_APPROVAL");
        UUID approvalId = approvalId(waiting);

        ApiException error = assertThrows(
                ApiException.class,
                () -> service.respondApproval(
                        ids.sessionId(), ids.runId(), approvalId, ApprovalDecision.ALLOW_SESSION));
        assertEquals("DECISION_NOT_ALLOWED", error.code());

        service.respondApproval(ids.sessionId(), ids.runId(), approvalId, ApprovalDecision.DENY);
        awaitRunStatus(ids.sessionId(), "COMPLETED");
    }

    @Test
    void closedSessionRemainsInReadOnlyHistory() throws Exception {
        SessionRun ids = create("persist this session");
        ObjectNode waiting = awaitRunStatus(ids.sessionId(), "WAITING_APPROVAL");
        service.respondApproval(
                ids.sessionId(), ids.runId(), approvalId(waiting), ApprovalDecision.DENY);
        awaitRunStatus(ids.sessionId(), "COMPLETED");

        assertEquals("CLOSING", service.closeSession(ids.sessionId()).status());
        awaitSessionStatus(ids.sessionId(), "CLOSED");

        ObjectNode history = service.getHistory(ids.sessionId());
        assertEquals("CLOSED", history.path("status").asText());
        assertEquals(1, history.path("runs").size());
        assertTrue(history.path("events").size() > 0);
        assertEquals("persist this session", history.path("runs").path(0).path("prompt").asText());
        assertFalse(service.listHistory().path("sessions").isEmpty());
    }

    @Test
    void malformedWorkerOutputFailsSession() throws Exception {
        SessionRun ids = create("[fake:invalid-json] protocol failure");

        ObjectNode failed = awaitSessionStatus(ids.sessionId(), "FAILED");
        assertEquals("FAILED", failed.path("currentRun").path("status").asText());
        ObjectNode summary = service.getRunSummary(ids.sessionId(), ids.runId());
        assertEquals("WORKER_PROTOCOL_ERROR", summary.path("error").path("code").asText());
    }

    private SessionRun create(String prompt) {
        ObjectNode resource = service.createSession(new CreateSessionRequest(
                workspace.toString(), prompt, List.of(), new RunOptions(8)));
        return new SessionRun(
                UUID.fromString(resource.path("sessionId").asText()),
                runId(resource));
    }

    private ObjectNode awaitRunStatus(UUID sessionId, String expected) throws Exception {
        long deadline = System.nanoTime() + Duration.ofSeconds(8).toNanos();
        ObjectNode latest = service.getSession(sessionId);
        while (System.nanoTime() < deadline) {
            latest = service.getSession(sessionId);
            if (expected.equals(latest.path("currentRun").path("status").asText())) {
                return latest;
            }
            Thread.sleep(20);
        }
        throw new AssertionError("run status timeout: expected=" + expected + ", actual="
                + latest.path("currentRun").path("status").asText());
    }

    private ObjectNode awaitSessionStatus(UUID sessionId, String expected) throws Exception {
        long deadline = System.nanoTime() + Duration.ofSeconds(8).toNanos();
        ObjectNode latest = service.getSession(sessionId);
        while (System.nanoTime() < deadline) {
            latest = service.getSession(sessionId);
            if (expected.equals(latest.path("status").asText())) {
                return latest;
            }
            Thread.sleep(20);
        }
        throw new AssertionError("session status timeout: expected=" + expected
                + ", actual=" + latest.path("status").asText());
    }

    private void approveBoth(UUID sessionId, UUID runId) throws Exception {
        ObjectNode first = awaitRunStatus(sessionId, "WAITING_APPROVAL");
        UUID firstApproval = approvalId(first);
        service.respondApproval(sessionId, runId, firstApproval, ApprovalDecision.ALLOW_ONCE);
        ObjectNode second = awaitDifferentApproval(sessionId, firstApproval);
        service.respondApproval(
                sessionId, runId, approvalId(second), ApprovalDecision.ALLOW_ONCE);
    }

    private ObjectNode awaitDifferentApproval(UUID sessionId, UUID previous) throws Exception {
        long deadline = System.nanoTime() + Duration.ofSeconds(8).toNanos();
        ObjectNode latest = service.getSession(sessionId);
        while (System.nanoTime() < deadline) {
            latest = service.getSession(sessionId);
            String raw = latest.path("currentRun").path("pendingApproval").path("approvalId").asText();
            if (!raw.isBlank() && !previous.toString().equals(raw)) {
                assertEquals("WAITING_APPROVAL", latest.path("currentRun").path("status").asText());
                return latest;
            }
            Thread.sleep(20);
        }
        throw new AssertionError("second approval timeout");
    }

    private static UUID approvalId(ObjectNode session) {
        return UUID.fromString(
                session.path("currentRun").path("pendingApproval").path("approvalId").asText());
    }

    private static UUID runId(ObjectNode session) {
        return UUID.fromString(session.path("currentRun").path("runId").asText());
    }

    private static SessionHistoryRepository historyRepository(Path database, ObjectMapper mapper) {
        DriverManagerDataSource source = new DriverManagerDataSource();
        source.setDriverClassName("org.sqlite.JDBC");
        source.setUrl("jdbc:sqlite:" + database);
        SessionHistoryRepository repository = new SessionHistoryRepository(
                new JdbcTemplate((DataSource) source), mapper);
        repository.initialize();
        return repository;
    }

    private static Path findPython(Path repositoryRoot) {
        return List.of(
                        repositoryRoot.resolve(".venv/Scripts/python.exe"),
                        repositoryRoot.resolve(".venv/bin/python"))
                .stream()
                .filter(Files::isRegularFile)
                .findFirst()
                .orElseThrow(() -> new IllegalStateException("repository .venv Python required"));
    }

    private record SessionRun(UUID sessionId, UUID runId) {}
}
