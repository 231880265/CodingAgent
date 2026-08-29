package dev.hako.web.config;

import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.util.unit.DataSize;

@ConfigurationProperties(prefix = "hako.web")
public class WebProperties {
    private Path repositoryRoot = Path.of(".");
    private List<Path> allowedRoots = new ArrayList<>(List.of(Path.of(".")));
    private String pythonExecutable = "python";
    private Path workerEntrypoint = Path.of("web/worker/main.py");
    private Duration startTimeout = Duration.ofSeconds(10);
    private Duration killGracePeriod = Duration.ofSeconds(5);
    private int eventMaxCount = 2000;
    private DataSize eventMaxBytes = DataSize.ofMegabytes(10);
    private String devAllowedOrigin = "http://127.0.0.1:5173";

    public Path getRepositoryRoot() {
        return repositoryRoot;
    }

    public void setRepositoryRoot(Path repositoryRoot) {
        this.repositoryRoot = repositoryRoot;
    }

    public List<Path> getAllowedRoots() {
        return allowedRoots;
    }

    public void setAllowedRoots(List<Path> allowedRoots) {
        this.allowedRoots = allowedRoots;
    }

    public String getPythonExecutable() {
        return pythonExecutable;
    }

    public void setPythonExecutable(String pythonExecutable) {
        this.pythonExecutable = pythonExecutable;
    }

    public Path getWorkerEntrypoint() {
        return workerEntrypoint;
    }

    public void setWorkerEntrypoint(Path workerEntrypoint) {
        this.workerEntrypoint = workerEntrypoint;
    }

    public Duration getStartTimeout() {
        return startTimeout;
    }

    public void setStartTimeout(Duration startTimeout) {
        this.startTimeout = startTimeout;
    }

    public Duration getKillGracePeriod() {
        return killGracePeriod;
    }

    public void setKillGracePeriod(Duration killGracePeriod) {
        this.killGracePeriod = killGracePeriod;
    }

    public int getEventMaxCount() {
        return eventMaxCount;
    }

    public void setEventMaxCount(int eventMaxCount) {
        this.eventMaxCount = eventMaxCount;
    }

    public DataSize getEventMaxBytes() {
        return eventMaxBytes;
    }

    public void setEventMaxBytes(DataSize eventMaxBytes) {
        this.eventMaxBytes = eventMaxBytes;
    }

    public String getDevAllowedOrigin() {
        return devAllowedOrigin;
    }

    public void setDevAllowedOrigin(String devAllowedOrigin) {
        this.devAllowedOrigin = devAllowedOrigin;
    }
}
