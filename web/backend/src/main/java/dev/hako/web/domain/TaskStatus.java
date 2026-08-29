package dev.hako.web.domain;

public enum TaskStatus {
    CREATED,
    STARTING,
    RUNNING,
    WAITING_APPROVAL,
    CANCELLING,
    COMPLETED,
    FAILED,
    CANCELLED;

    public boolean isActive() {
        return this == CREATED
                || this == STARTING
                || this == RUNNING
                || this == WAITING_APPROVAL
                || this == CANCELLING;
    }

    public boolean isTerminal() {
        return this == COMPLETED || this == FAILED || this == CANCELLED;
    }
}
