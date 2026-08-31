package dev.hako.web.api;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public final class ApiModels {
    private ApiModels() {}

    public record CreateSessionRequest(
            @Size(max = 4096) String workspace,
            @NotBlank @Size(max = 20000) String prompt,
            @Valid @Size(max = 5) List<@Valid AttachmentInput> attachments,
            @Valid RunOptions options) {}

    public record RunOptions(@Min(1) @Max(100) Integer maxSteps) {
        public static final int DEFAULT_WEB_SAFETY_BUDGET = 100;

        public int valueOrDefault() {
            return maxSteps == null ? DEFAULT_WEB_SAFETY_BUDGET : maxSteps;
        }
    }

    public record CreateRunRequest(
            @NotBlank @Size(max = 20000) String prompt,
            @Valid @Size(max = 5) List<@Valid AttachmentInput> attachments,
            @Valid RunOptions options) {}

    public record AttachmentInput(
            @NotBlank @Size(max = 255) String name,
            @NotBlank @Size(max = 200) String mediaType,
            @NotBlank @Size(max = 40000) String content) {}

    public enum ApprovalDecision {
        ALLOW_ONCE,
        ALLOW_SESSION,
        DENY
    }

    public record ApprovalDecisionRequest(@NotNull ApprovalDecision decision) {}

    public record ApprovalResponse(
            String schemaVersion,
            UUID sessionId,
            UUID runId,
            UUID approvalId,
            String status,
            ApprovalDecision decision,
            Instant acceptedAt) {}

    public record CancelResponse(
            String schemaVersion,
            UUID sessionId,
            UUID runId,
            String status,
            String message) {}

    public record SessionCloseResponse(
            String schemaVersion,
            UUID sessionId,
            String status) {}

    public record SessionSuspendResponse(
            String schemaVersion,
            UUID sessionId,
            String status) {}

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
