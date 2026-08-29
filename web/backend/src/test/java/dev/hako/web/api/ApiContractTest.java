package dev.hako.web.api;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class ApiContractTest {
    private static final Path REPOSITORY_ROOT = Path.of(System.getProperty("user.dir"))
            .resolve("../..")
            .normalize()
            .toAbsolutePath();

    @DynamicPropertySource
    static void configure(DynamicPropertyRegistry registry) {
        registry.add("hako.web.repository-root", REPOSITORY_ROOT::toString);
        registry.add("hako.web.allowed-roots", REPOSITORY_ROOT::toString);
        registry.add("hako.web.worker-entrypoint", () -> "web/worker/fake_worker.py");
    }

    @LocalServerPort
    int port;

    @Autowired
    ObjectMapper mapper;

    private final HttpClient client = HttpClient.newHttpClient();

    @Test
    void healthUsesVersionedEnvelope() throws Exception {
        HttpResponse<String> response = send("GET", "/api/v1/health", null);

        assertEquals(200, response.statusCode());
        assertEquals("1.0", mapper.readTree(response.body()).path("schemaVersion").asText());
        assertEquals("UP", mapper.readTree(response.body()).path("status").asText());
    }

    @Test
    void invalidUuidIsAClientError() throws Exception {
        HttpResponse<String> response = send("GET", "/api/v1/tasks/not-a-uuid", null);

        assertEquals(400, response.statusCode());
        assertEquals(
                "INVALID_REQUEST",
                mapper.readTree(response.body()).path("error").path("code").asText());
    }

    @Test
    void unknownJsonFieldIsRejected() throws Exception {
        ObjectNode body = mapper.createObjectNode();
        body.put("workspace", REPOSITORY_ROOT.toString());
        body.put("prompt", "contract test");
        body.put("unexpected", true);

        HttpResponse<String> response = send("POST", "/api/v1/tasks", body.toString());

        assertEquals(400, response.statusCode());
        assertEquals(
                "INVALID_REQUEST",
                mapper.readTree(response.body()).path("error").path("code").asText());
    }

    @Test
    void oversizedBodyIsRejectedBeforeJsonParsing() throws Exception {
        HttpResponse<String> response = send("POST", "/api/v1/tasks", "x".repeat(65_537));

        assertEquals(413, response.statusCode());
        assertEquals(
                "PAYLOAD_TOO_LARGE",
                mapper.readTree(response.body()).path("error").path("code").asText());
    }

    private HttpResponse<String> send(String method, String path, String body) throws Exception {
        HttpRequest.Builder request = HttpRequest.newBuilder()
                .uri(URI.create("http://127.0.0.1:" + port + path))
                .header("Accept", "application/json");
        if (body == null) {
            request.method(method, HttpRequest.BodyPublishers.noBody());
        } else {
            request.header("Content-Type", "application/json");
            request.method(method, HttpRequest.BodyPublishers.ofString(body));
        }
        HttpResponse<String> response = client.send(
                request.build(),
                HttpResponse.BodyHandlers.ofString());
        assertTrue(response.headers().firstValue("content-type").orElse("").contains("application/json"));
        return response;
    }
}
