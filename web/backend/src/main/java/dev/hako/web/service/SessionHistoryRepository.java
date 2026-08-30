package dev.hako.web.service;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;
import dev.hako.web.domain.BufferedEvent;
import dev.hako.web.domain.RunState;
import dev.hako.web.domain.SessionState;
import jakarta.annotation.PostConstruct;
import java.time.Instant;
import java.util.UUID;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
public class SessionHistoryRepository {
    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;

    public SessionHistoryRepository(JdbcTemplate jdbc, ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    @PostConstruct
    void initialize() {
        jdbc.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    workspace TEXT NOT NULL,
                    status TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    worker_pid INTEGER,
                    run_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    closed_at TEXT
                )
                """);
        jdbc.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    resource_json TEXT NOT NULL,
                    summary_json TEXT,
                    UNIQUE(session_id, ordinal)
                )
                """);
        jdbc.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    session_id TEXT NOT NULL,
                    event_id INTEGER NOT NULL,
                    run_id TEXT,
                    type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    PRIMARY KEY(session_id, event_id)
                )
                """);
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, ordinal)");
        jdbc.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, event_id)");
    }

    public void saveSession(SessionState session) {
        jdbc.update("""
                INSERT INTO sessions(
                    session_id, workspace, status, worker_id, worker_pid,
                    run_count, created_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    workspace=excluded.workspace,
                    status=excluded.status,
                    worker_id=excluded.worker_id,
                    worker_pid=excluded.worker_pid,
                    run_count=excluded.run_count,
                    closed_at=excluded.closed_at
                """,
                session.sessionId.toString(),
                session.workspace.toString(),
                session.status.name(),
                session.workerId.toString(),
                session.workerPid,
                session.runs.size(),
                session.createdAt.toString(),
                text(session.closedAt));
    }

    public void saveRun(SessionState session, RunState run) {
        int ordinal = 1;
        for (UUID id : session.runs.keySet()) {
            if (id.equals(run.runId)) {
                break;
            }
            ordinal += 1;
        }
        jdbc.update("""
                INSERT INTO runs(
                    run_id, session_id, ordinal, status, prompt, created_at,
                    finished_at, resource_json, summary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    finished_at=excluded.finished_at,
                    resource_json=excluded.resource_json,
                    summary_json=excluded.summary_json
                """,
                run.runId.toString(),
                session.sessionId.toString(),
                ordinal,
                run.status.name(),
                run.prompt,
                run.createdAt.toString(),
                text(run.finishedAt),
                run.resource(mapper).toString(),
                run.summary == null ? null : run.summary.toString());
    }

    public void saveEvent(BufferedEvent event) {
        ObjectNode envelope = event.envelope();
        jdbc.update("""
                INSERT OR IGNORE INTO events(
                    session_id, event_id, run_id, type, source, occurred_at, envelope_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                envelope.path("sessionId").asText(),
                event.eventId(),
                envelope.hasNonNull("runId") ? envelope.path("runId").asText() : null,
                event.type(),
                envelope.path("source").asText(),
                envelope.path("occurredAt").asText(),
                envelope.toString());
    }

    public ObjectNode listSessions() {
        ObjectNode root = mapper.createObjectNode();
        root.put("schemaVersion", "1.0");
        ArrayNode sessions = root.putArray("sessions");
        jdbc.query("""
                        SELECT s.*,
                               (SELECT prompt FROM runs r
                                WHERE r.session_id=s.session_id
                                ORDER BY ordinal DESC LIMIT 1) AS last_prompt
                        FROM sessions s
                        ORDER BY created_at DESC
                        """,
                result -> {
                    ObjectNode item = sessions.addObject();
                    item.put("sessionId", result.getString("session_id"));
                    item.put("workspace", result.getString("workspace"));
                    item.put("status", result.getString("status"));
                    item.put("runCount", result.getInt("run_count"));
                    item.put("createdAt", result.getString("created_at"));
                    putNullable(item, "closedAt", result.getString("closed_at"));
                    putNullable(item, "lastPrompt", result.getString("last_prompt"));
                });
        return root;
    }

    public ObjectNode getHistory(UUID sessionId) {
        final ObjectNode root;
        try {
            root = jdbc.queryForObject(
                    "SELECT * FROM sessions WHERE session_id=?",
                    (result, row) -> {
                        ObjectNode value = mapper.createObjectNode();
                        value.put("schemaVersion", "1.0");
                        value.put("sessionId", result.getString("session_id"));
                        value.put("workspace", result.getString("workspace"));
                        value.put("status", result.getString("status"));
                        value.put("workerId", result.getString("worker_id"));
                        value.put("runCount", result.getInt("run_count"));
                        value.put("createdAt", result.getString("created_at"));
                        putNullable(value, "closedAt", result.getString("closed_at"));
                        return value;
                    },
                    sessionId.toString());
        } catch (EmptyResultDataAccessException exc) {
            return null;
        }

        ArrayNode runs = root.putArray("runs");
        jdbc.query(
                "SELECT resource_json, summary_json FROM runs WHERE session_id=? ORDER BY ordinal",
                result -> {
                    ObjectNode run = parseObject(result.getString("resource_json"));
                    String summary = result.getString("summary_json");
                    if (summary == null) {
                        run.putNull("summary");
                    } else {
                        run.set("summary", parseObject(summary));
                    }
                    runs.add(run);
                },
                sessionId.toString());

        ArrayNode events = root.putArray("events");
        jdbc.query(
                "SELECT envelope_json FROM events WHERE session_id=? ORDER BY event_id",
                result -> {
                    events.add(parseObject(result.getString("envelope_json")));
                },
                sessionId.toString());
        return root;
    }

    private ObjectNode parseObject(String raw) {
        try {
            JsonNode parsed = mapper.readTree(raw);
            return parsed instanceof ObjectNode object ? object : mapper.createObjectNode();
        } catch (Exception exc) {
            throw new IllegalStateException("历史数据库包含无效 JSON。", exc);
        }
    }

    private static String text(Instant instant) {
        return instant == null ? null : instant.toString();
    }

    private static void putNullable(ObjectNode node, String field, String value) {
        if (value == null) {
            node.putNull(field);
        } else {
            node.put(field, value);
        }
    }
}
