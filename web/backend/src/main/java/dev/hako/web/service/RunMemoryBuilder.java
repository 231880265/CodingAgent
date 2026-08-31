package dev.hako.web.service;

import dev.hako.web.domain.RunState;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** Deterministic Run read model: hard facts come from persisted events only. */
final class RunMemoryBuilder {
    private final ObjectMapper mapper;

    RunMemoryBuilder(ObjectMapper mapper) {
        this.mapper = mapper;
    }

    ObjectNode build(UUID sessionId, RunState run, List<ObjectNode> events) {
        ObjectNode memory = mapper.createObjectNode();
        memory.put("schemaVersion", "1.0");
        memory.put("sessionId", sessionId.toString());
        memory.put("runId", run.runId.toString());
        memory.put("status", run.status.name());
        putNullable(memory, "stopReason", text(run.outcome, "stopReason"));
        memory.put("userGoal", clip(run.prompt, 2_000));
        putNullable(memory, "startedAt", run.startedAt == null ? null : run.startedAt.toString());
        putNullable(memory, "finishedAt", run.finishedAt == null ? null : run.finishedAt.toString());

        Set<String> created = new LinkedHashSet<>();
        Set<String> modified = new LinkedHashSet<>();
        Set<String> deleted = new LinkedHashSet<>();
        Set<String> derived = new LinkedHashSet<>();
        Map<String, ObjectNode> startedCalls = new LinkedHashMap<>();
        Map<String, ObjectNode> approvals = new LinkedHashMap<>();
        ArrayNode verifications = memory.putArray("verifications");
        ArrayNode failures = memory.putArray("toolFailures");
        List<EvidenceRef> evidence = new ArrayList<>();

        for (ObjectNode envelope : events) {
            long eventId = envelope.path("eventId").asLong();
            String type = envelope.path("type").asText();
            ObjectNode payload = envelope.path("payload") instanceof ObjectNode object
                    ? object
                    : mapper.createObjectNode();
            if ("tool_call_started".equals(type)) {
                startedCalls.put(payload.path("callId").asText(), payload);
                continue;
            }
            if ("tool_call_finished".equals(type)) {
                addStrings(created, payload.path("createdPaths"));
                addStrings(modified, payload.path("modifiedPaths"));
                addStrings(deleted, payload.path("deletedPaths"));
                addStrings(derived, payload.path("derivedPaths"));
                boolean hasChanges = hasValues(payload.path("createdPaths"))
                        || hasValues(payload.path("modifiedPaths"))
                        || hasValues(payload.path("deletedPaths"));
                if (hasChanges) {
                    evidence.add(new EvidenceRef(eventId, type));
                }

                String callId = payload.path("callId").asText();
                String verificationKind = payload.path("verificationKind").asText("");
                if (!verificationKind.isBlank()) {
                    ObjectNode item = verifications.addObject();
                    item.put("eventId", eventId);
                    item.put("callId", callId);
                    item.put("kind", verificationKind);
                    ObjectNode started = startedCalls.get(callId);
                    putNullable(
                            item,
                            "requestedCommand",
                            started == null ? null : started.path("args").path("command").asText(null));
                    putNullable(item, "executedCommand", payload.path("verificationCommand").asText(null));
                    item.put("ok", payload.path("ok").asBoolean(false));
                    item.put("status", commandStatus(payload));
                    putInteger(item, "exitCode", payload.path("exitCode"));
                    item.put("summary", payload.path("summary").asText(""));
                    item.put("occurredAt", envelope.path("occurredAt").asText(""));
                    evidence.add(new EvidenceRef(eventId, type));
                }
                if (!payload.path("ok").asBoolean(false)) {
                    ObjectNode failure = failures.addObject();
                    failure.put("eventId", eventId);
                    failure.put("callId", callId);
                    failure.put("tool", payload.path("name").asText(""));
                    failure.put("status", commandStatus(payload));
                    putInteger(failure, "exitCode", payload.path("exitCode"));
                    failure.put("summary", payload.path("summary").asText(""));
                }
                continue;
            }
            if ("approval_required".equals(type)) {
                String approvalId = payload.path("approvalId").asText();
                ObjectNode item = mapper.createObjectNode();
                item.put("approvalId", approvalId);
                item.put("requestedEventId", eventId);
                item.put("tool", payload.path("tool").path("name").asText(""));
                item.put("riskLevel", payload.path("riskLevel").asText(""));
                putNullable(item, "dangerReason", payload.path("dangerReason").asText(null));
                putNullable(item, "requestedAt", payload.path("requestedAt").asText(null));
                item.putNull("decision");
                item.putNull("resolvedAt");
                approvals.put(approvalId, item);
                continue;
            }
            if ("approval_resolved".equals(type)) {
                String approvalId = payload.path("approvalId").asText();
                ObjectNode item = approvals.computeIfAbsent(
                        approvalId,
                        ignored -> mapper.createObjectNode().put("approvalId", approvalId));
                item.put("resolvedEventId", eventId);
                putNullable(item, "decision", payload.path("decision").asText(null));
                putNullable(item, "resolvedAt", payload.path("resolvedAt").asText(null));
                evidence.add(new EvidenceRef(eventId, type));
                continue;
            }
            if ("run_finished".equals(type) || "run_result".equals(type)) {
                evidence.add(new EvidenceRef(eventId, type));
            }
        }

        ObjectNode changes = memory.putObject("changes");
        addArray(changes, "created", created);
        addArray(changes, "modified", modified);
        addArray(changes, "deleted", deleted);
        addArray(changes, "derived", derived);
        ArrayNode approvalArray = memory.putArray("approvals");
        approvals.values().forEach(approvalArray::add);
        ArrayNode evidenceIds = memory.putArray("evidenceIds");
        evidence.stream().distinct().forEach(item -> {
            ObjectNode ref = evidenceIds.addObject();
            ref.put("eventId", item.eventId());
            ref.put("type", item.type());
        });

        ObjectNode semantic = memory.putObject("semanticSummary");
        semantic.put("text", clip(text(run.outcome, "finalText"), 1_500));
        semantic.put("source", "AGENT_FINAL_TEXT");
        semantic.put("authoritative", false);
        return memory;
    }

    private static String commandStatus(ObjectNode payload) {
        String status = payload.path("commandStatus").asText("");
        if (!status.isBlank()) {
            return status;
        }
        return payload.path("ok").asBoolean(false) ? "succeeded" : "failed";
    }

    private static String text(ObjectNode node, String field) {
        if (node == null || !node.hasNonNull(field)) {
            return null;
        }
        return node.path(field).asText();
    }

    private static void putNullable(ObjectNode node, String field, String value) {
        if (value == null || value.isBlank()) {
            node.putNull(field);
        } else {
            node.put(field, value);
        }
    }

    private static String clip(String value, int limit) {
        if (value == null) {
            return "";
        }
        if (value.length() <= limit) {
            return value;
        }
        return value.substring(0, limit - 28) + "\n[run memory truncated]";
    }

    private static void putInteger(ObjectNode node, String field, JsonNode value) {
        if (value != null && value.isIntegralNumber()) {
            node.put(field, value.intValue());
        } else {
            node.putNull(field);
        }
    }

    private static boolean hasValues(JsonNode value) {
        return value != null && value.isArray() && !value.isEmpty();
    }

    private static void addStrings(Set<String> target, JsonNode values) {
        if (values == null || !values.isArray()) {
            return;
        }
        values.forEach(value -> {
            if (value.isTextual() && !value.asText().isBlank()) {
                target.add(value.asText());
            }
        });
    }

    private static void addArray(ObjectNode target, String field, Set<String> values) {
        ArrayNode array = target.putArray(field);
        values.forEach(array::add);
    }

    private record EvidenceRef(long eventId, String type) {}
}
