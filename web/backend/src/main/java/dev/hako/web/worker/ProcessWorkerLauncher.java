package dev.hako.web.worker;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import dev.hako.web.config.WebProperties;
import jakarta.annotation.PreDestroy;
import java.io.BufferedWriter;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStreamWriter;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import org.springframework.stereotype.Component;

@Component
public class ProcessWorkerLauncher implements WorkerLauncher {
    static final int MAX_LINE_BYTES = 1024 * 1024;
    static final int STDERR_TAIL_BYTES = 256 * 1024;

    private static final Set<String> PASSTHROUGH_ENV = Set.of(
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "PATHEXT",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "VIRTUAL_ENV",
            "PYTHONPATH",
            "SILICONFLOW_API_KEY",
            "DEEPSEEK_API_KEY",
            "DASHSCOPE_API_KEY",
            "ZHIPU_API_KEY");

    private final WebProperties properties;
    private final ObjectMapper mapper;
    private final ExecutorService ioExecutor = Executors.newVirtualThreadPerTaskExecutor();

    public ProcessWorkerLauncher(WebProperties properties, ObjectMapper mapper) {
        this.properties = properties;
        this.mapper = mapper;
    }

    @Override
    public WorkerSession launch(WorkerListener listener) throws IOException {
        Path repositoryRoot = properties.getRepositoryRoot().toAbsolutePath().normalize().toRealPath();
        Path configured = properties.getWorkerEntrypoint();
        Path entrypoint = configured.isAbsolute()
                ? configured.normalize()
                : repositoryRoot.resolve(configured).normalize();
        if (!Files.isRegularFile(entrypoint) || !Files.isReadable(entrypoint)) {
            throw new IOException("Worker 入口不存在或不可读：" + entrypoint);
        }

        List<String> command = List.of(
                properties.getPythonExecutable(),
                "-u",
                entrypoint.toString());
        ProcessBuilder builder = new ProcessBuilder(command)
                .directory(repositoryRoot.toFile())
                .redirectErrorStream(false);
        copyAllowedEnvironment(builder.environment(), System.getenv());
        Process process = builder.start();
        return new ProcessWorkerSession(process, mapper, listener, ioExecutor);
    }

    private static void copyAllowedEnvironment(
            Map<String, String> child,
            Map<String, String> parent) {
        child.clear();
        parent.forEach((key, value) -> {
            String normalized = key.toUpperCase(Locale.ROOT);
            if (PASSTHROUGH_ENV.contains(normalized) || normalized.startsWith("HAKO_")) {
                child.put(key, value);
            }
        });
        child.put("PYTHONUTF8", "1");
        child.put("PYTHONIOENCODING", "utf-8");
    }

    @PreDestroy
    public void shutdown() {
        ioExecutor.shutdownNow();
    }

    static final class ProcessWorkerSession implements WorkerSession {
        private final Process process;
        private final ObjectMapper mapper;
        private final WorkerListener listener;
        private final ExecutorService executor;
        private final BufferedWriter stdin;
        private final TailBuffer stderrTail = new TailBuffer(STDERR_TAIL_BYTES);
        private final Future<?> stdoutFuture;

        ProcessWorkerSession(
                Process process,
                ObjectMapper mapper,
                WorkerListener listener,
                ExecutorService executor) {
            this.process = process;
            this.mapper = mapper;
            this.listener = listener;
            this.executor = executor;
            this.stdin = new BufferedWriter(
                    new OutputStreamWriter(process.getOutputStream(), StandardCharsets.UTF_8));
            this.stdoutFuture = executor.submit(this::readStdout);
            executor.submit(this::readStderr);
            executor.submit(this::waitForExit);
        }

        @Override
        public synchronized void send(JsonNode message) throws IOException {
            if (!process.isAlive()) {
                throw new IOException("Worker 已退出，无法发送消息。") ;
            }
            String encoded = mapper.writeValueAsString(message);
            if (encoded.getBytes(StandardCharsets.UTF_8).length > MAX_LINE_BYTES) {
                throw new IOException("发送给 Worker 的消息超过 1 MiB。") ;
            }
            stdin.write(encoded);
            stdin.write('\n');
            stdin.flush();
        }

        @Override
        public void terminate(Duration gracePeriod, Consumer<Boolean> completion) {
            executor.submit(() -> {
                boolean forced = false;
                destroyTree(false);
                try {
                    if (!process.waitFor(gracePeriod.toMillis(), TimeUnit.MILLISECONDS)) {
                        forced = true;
                        destroyTree(true);
                        process.waitFor(Math.max(1000L, gracePeriod.toMillis()), TimeUnit.MILLISECONDS);
                    }
                } catch (InterruptedException exc) {
                    Thread.currentThread().interrupt();
                    forced = true;
                    destroyTree(true);
                } finally {
                    completion.accept(forced);
                }
            });
        }

        @Override
        public boolean isAlive() {
            return process.isAlive();
        }

        @Override
        public synchronized void close() {
            try {
                stdin.close();
            } catch (IOException ignored) {
                // 进程回收优先；关闭已断开的管道不应覆盖任务结果。
            }
            if (process.isAlive()) {
                destroyTree(true);
            }
        }

        private void readStdout() {
            try (InputStream stdout = process.getInputStream()) {
                while (true) {
                    String line = readLimitedLine(stdout, MAX_LINE_BYTES);
                    if (line == null) {
                        return;
                    }
                    if (line.isBlank()) {
                        throw new IOException("Worker stdout 出现空行。") ;
                    }
                    JsonNode message = mapper.readTree(line);
                    if (message == null || !message.isObject()) {
                        throw new IOException("Worker 顶层消息必须是 JSON 对象。") ;
                    }
                    listener.onMessage(message);
                }
            } catch (Exception exc) {
                listener.onProtocolError(SecretRedactor.redact(exc.getMessage()));
                destroyTree(true);
            }
        }

        private void readStderr() {
            try (InputStream stderr = process.getErrorStream()) {
                byte[] buffer = new byte[4096];
                int count;
                while ((count = stderr.read(buffer)) != -1) {
                    stderrTail.append(buffer, count);
                }
            } catch (IOException ignored) {
                // stderr 只用于诊断；读取失败不能改变协议状态。
            }
        }

        private void waitForExit() {
            try {
                int exitCode = process.waitFor();
                try {
                    stdoutFuture.get(2, TimeUnit.SECONDS);
                } catch (Exception ignored) {
                    // onExit 仍必须发生，协议读取故障已由 onProtocolError 报告。
                }
                listener.onExit(exitCode, SecretRedactor.redact(stderrTail.text()));
            } catch (InterruptedException exc) {
                Thread.currentThread().interrupt();
            }
        }

        private void destroyTree(boolean forcibly) {
            List<ProcessHandle> descendants = new ArrayList<>(process.descendants().toList());
            for (int index = descendants.size() - 1; index >= 0; index--) {
                destroy(descendants.get(index), forcibly);
            }
            destroy(process.toHandle(), forcibly);
        }

        private static void destroy(ProcessHandle handle, boolean forcibly) {
            if (!handle.isAlive()) {
                return;
            }
            if (forcibly) {
                handle.destroyForcibly();
            } else {
                handle.destroy();
            }
        }
    }

    static String readLimitedLine(InputStream stream, int maxBytes) throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        while (true) {
            int next = stream.read();
            if (next == -1) {
                if (buffer.size() == 0) {
                    return null;
                }
                throw new IOException("Worker stdout 消息必须以 LF 结束。");
            }
            if (next == '\n') {
                byte[] bytes = buffer.toByteArray();
                int length = bytes.length;
                if (length > 0 && bytes[length - 1] == '\r') {
                    length -= 1;
                }
                byte[] trimmed = length == bytes.length ? bytes : java.util.Arrays.copyOf(bytes, length);
                return decode(trimmed);
            }
            buffer.write(next);
            if (buffer.size() > maxBytes) {
                throw new IOException("Worker stdout 单行超过允许上限。");
            }
        }
    }

    private static String decode(byte[] bytes) throws CharacterCodingException {
        return StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes))
                .toString();
    }

    static final class TailBuffer {
        private final int limit;
        private byte[] value = new byte[0];

        TailBuffer(int limit) {
            this.limit = limit;
        }

        synchronized void append(byte[] bytes, int count) {
            int keepExisting = Math.min(value.length, Math.max(0, limit - count));
            int keepIncoming = Math.min(count, limit);
            byte[] next = new byte[keepExisting + keepIncoming];
            if (keepExisting > 0) {
                System.arraycopy(value, value.length - keepExisting, next, 0, keepExisting);
            }
            System.arraycopy(bytes, count - keepIncoming, next, keepExisting, keepIncoming);
            value = next;
        }

        synchronized String text() {
            return new String(value, StandardCharsets.UTF_8);
        }
    }
}
