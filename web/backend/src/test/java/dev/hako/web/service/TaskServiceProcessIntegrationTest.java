package dev.hako.web.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import dev.hako.web.api.ApiException;
import dev.hako.web.api.ApiModels.ApprovalDecision;
import dev.hako.web.api.ApiModels.CreateTaskRequest;
import dev.hako.web.api.ApiModels.TaskOptions;
import dev.hako.web.config.WebProperties;
import dev.hako.web.worker.ProcessWorkerLauncher;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

class TaskServiceProcessIntegrationTest {
    @TempDir
    Path workspace;

    private ProcessWorkerLauncher launcher;
    private TaskService service;

    @BeforeEach
    void setUp() throws Exception {
        Path repositoryRoot = Path.of(System.getProperty("user.dir"))
                .resolve("../..")
                .normalize()
                .toRealPath();
        Path python = findPython(repositoryRoot);
        WebProperties properties = new WebProperties();
        properties.setRepositoryRoot(repositoryRoot);
        properties.setAllowedRoots(List.of(workspace));
        properties.setPythonExecutable(python.toString());
        properties.setWorkerEntrypoint(Path.of("web/worker/fake_worker.py"));
        properties.setStartTimeout(Duration.ofSeconds(3));
        properties.setKillGracePeriod(Duration.ofMillis(300));
        ObjectMapper mapper = new ObjectMapper();
        launcher = new ProcessWorkerLauncher(properties, mapper);
        service = new TaskService(properties, mapper, launcher);
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
    void completesTwoApprovalRunAndReturnsEvidenceSummary() throws Exception {
        UUID taskId = create("修复 Header 大小写问题并验证");

        ObjectNode first = awaitStatus(taskId, "WAITING_APPROVAL");
        UUID firstApproval = approvalId(first);
        var accepted = service.respondApproval(
                taskId,
                firstApproval,
                ApprovalDecision.ALLOW_ONCE);
        assertEquals("ACCEPTED", accepted.status());

        ObjectNode second = awaitDifferentApproval(taskId, firstApproval);
        service.respondApproval(
                taskId,
                approvalId(second),
                ApprovalDecision.ALLOW_ONCE);

        awaitStatus(taskId, "COMPLETED");
        ObjectNode summary = service.getSummary(taskId);
        assertTrue(summary.path("success").booleanValue());
        assertEquals("done_verified", summary.path("stopReason").asText());
        assertEquals("router/headers.py", summary.path("changedPaths").path(0).asText());
        assertEquals("test", summary.path("verification").path(0).path("kind").asText());
        assertTrue(summary.path("verification").path(0).path("summary").asText().contains("passed"));
    }

    @Test
    void deniedApprovalEndsAsFailedWithoutClaimingChanges() throws Exception {
        UUID taskId = create("拒绝写入演示");
        ObjectNode waiting = awaitStatus(taskId, "WAITING_APPROVAL");

        service.respondApproval(taskId, approvalId(waiting), ApprovalDecision.DENY);

        awaitStatus(taskId, "FAILED");
        ObjectNode summary = service.getSummary(taskId);
        assertFalse(summary.path("success").booleanValue());
        assertEquals("denied", summary.path("stopReason").asText());
        assertTrue(summary.path("changedPaths").isEmpty());
    }

    @Test
    void highRiskApprovalRejectsSessionScope() throws Exception {
        UUID taskId = create("[fake:high-risk] 高风险审批演示");
        ObjectNode waiting = awaitStatus(taskId, "WAITING_APPROVAL");
        UUID approvalId = approvalId(waiting);

        ApiException error = assertThrows(
                ApiException.class,
                () -> service.respondApproval(
                        taskId,
                        approvalId,
                        ApprovalDecision.ALLOW_SESSION));
        assertEquals("DECISION_NOT_ALLOWED", error.code());

        service.respondApproval(taskId, approvalId, ApprovalDecision.DENY);
        awaitStatus(taskId, "FAILED");
    }

    @Test
    void malformedWorkerOutputBecomesProtocolFailure() throws Exception {
        UUID taskId = create("[fake:invalid-json] 协议错误演示");

        awaitStatus(taskId, "FAILED");
        ObjectNode summary = service.getSummary(taskId);
        assertEquals("WORKER_PROTOCOL_ERROR", summary.path("error").path("code").asText());
    }

    @Test
    void cancellationStopsWaitingWorkerAndProducesHonestSummary() throws Exception {
        UUID taskId = create("取消演示");
        awaitStatus(taskId, "WAITING_APPROVAL");

        assertEquals("CANCELLING", service.cancelTask(taskId).status());

        awaitStatus(taskId, "CANCELLED");
        ObjectNode summary = service.getSummary(taskId);
        assertEquals("CANCELLED", summary.path("status").asText());
        assertFalse(summary.path("success").booleanValue());
        assertTrue(summary.path("finalText").asText().contains("自动回滚"));
    }

    private UUID create(String prompt) {
        ObjectNode resource = service.createTask(new CreateTaskRequest(
                workspace.toString(),
                prompt,
                new TaskOptions(8)));
        return UUID.fromString(resource.path("taskId").asText());
    }

    private ObjectNode awaitStatus(UUID taskId, String expected) throws Exception {
        long deadline = System.nanoTime() + Duration.ofSeconds(8).toNanos();
        ObjectNode latest = service.getTask(taskId);
        while (System.nanoTime() < deadline) {
            latest = service.getTask(taskId);
            if (expected.equals(latest.path("status").asText())) {
                return latest;
            }
            Thread.sleep(20);
        }
        throw new AssertionError(
                "等待状态 " + expected + " 超时，当前为 " + latest.path("status").asText());
    }

    private ObjectNode awaitDifferentApproval(UUID taskId, UUID previous) throws Exception {
        long deadline = System.nanoTime() + Duration.ofSeconds(8).toNanos();
        ObjectNode latest = service.getTask(taskId);
        while (System.nanoTime() < deadline) {
            latest = service.getTask(taskId);
            String raw = latest.path("pendingApproval").path("approvalId").asText();
            if (!raw.isBlank() && !previous.toString().equals(raw)) {
                assertEquals("WAITING_APPROVAL", latest.path("status").asText());
                return latest;
            }
            Thread.sleep(20);
        }
        throw new AssertionError("等待第二次审批超时");
    }

    private static UUID approvalId(ObjectNode task) {
        return UUID.fromString(task.path("pendingApproval").path("approvalId").asText());
    }

    private static Path findPython(Path repositoryRoot) {
        List<Path> candidates = List.of(
                repositoryRoot.resolve(".venv/Scripts/python.exe"),
                repositoryRoot.resolve(".venv/bin/python"));
        return candidates.stream()
                .filter(Files::isRegularFile)
                .findFirst()
                .orElseThrow(() -> new IllegalStateException("测试需要仓库内 .venv Python。"));
    }
}
