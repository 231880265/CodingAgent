package dev.hako.web.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import dev.hako.web.domain.SessionState;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import javax.sql.DataSource;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;

class SessionHistoryRepositoryTest {
    @Test
    void repositoryMemorySpansSessionsButNeverCrossesWorkspaceBoundary() throws Exception {
        Path tempDir = Path.of("target", "test-history", UUID.randomUUID().toString())
                .toAbsolutePath()
                .normalize();
        Files.createDirectories(tempDir);
        ObjectMapper mapper = new ObjectMapper();
        SessionHistoryRepository repository = repository(tempDir.resolve("history.db"), mapper);
        Path workspace = tempDir.resolve("shop").toAbsolutePath().normalize();
        Path otherWorkspace = tempDir.resolve("other").toAbsolutePath().normalize();

        saveMemory(repository, mapper, workspace, "session-one");
        saveMemory(repository, mapper, workspace, "session-two");
        saveMemory(repository, mapper, otherWorkspace, "other-workspace");

        ArrayNode snapshot = repository.getRepositoryMemorySnapshot(workspace);

        assertEquals(2, snapshot.size());
        Set<String> markers = new HashSet<>();
        snapshot.forEach(memory -> markers.add(memory.path("marker").asText()));
        assertTrue(markers.containsAll(List.of("session-one", "session-two")));
    }

    private static void saveMemory(
            SessionHistoryRepository repository,
            ObjectMapper mapper,
            Path workspace,
            String marker) {
        SessionState session = new SessionState(workspace, marker, 8, List.of());
        session.currentRun.runMemory = mapper.createObjectNode().put("marker", marker);
        repository.saveSession(session);
        repository.saveRun(session, session.currentRun);
    }

    private static SessionHistoryRepository repository(Path database, ObjectMapper mapper) {
        DriverManagerDataSource source = new DriverManagerDataSource();
        source.setDriverClassName("org.sqlite.JDBC");
        source.setUrl("jdbc:sqlite:" + database);
        SessionHistoryRepository repository = new SessionHistoryRepository(
                new JdbcTemplate((DataSource) source), mapper);
        repository.initialize();
        return repository;
    }
}
