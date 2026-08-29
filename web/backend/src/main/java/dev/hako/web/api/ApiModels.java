package dev.hako.web.api;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;

public final class ApiModels {
    private ApiModels() {}

    public record CreateTaskRequest(
            @NotBlank @Size(max = 4096) String workspace,
            @NotBlank @Size(max = 20000) String prompt,
            @Valid TaskOptions options) {}

    public record TaskOptions(@Min(1) @Max(100) Integer maxSteps) {
        public int valueOrDefault() {
            return maxSteps == null ? 40 : maxSteps;
        }
    }

    public enum ApprovalDecision {
        ALLOW_ONCE,
        ALLOW_SESSION,
        DENY
    }

    public record ApprovalDecisionRequest(@NotNull ApprovalDecision decision) {}

    public record ApprovalResponse(
            String schemaVersion,
            UUID taskId,
            UUID approvalId,
            String status,
            ApprovalDecision decision,
            Instant acceptedAt) {}

    public record CancelResponse(
            String schemaVersion,
            UUID taskId,
            String status,
            String message) {}

    public record WorkerHealth(boolean pythonConfigured, boolean entrypointReadable) {}

    public record HealthResponse(
            String schemaVersion,
            String status,
            String version,
            WorkerHealth worker) {}

    public record ErrorDetail(
            String code,
            String message,
            UUID requestId,
            Map<String, Object> details) {}

    public record ErrorEnvelope(String schemaVersion, ErrorDetail error) {}
}
