package dev.hako.web.domain;

public enum RunStatus {
    PENDING,
    RUNNING,
    WAITING_APPROVAL,
    CANCELLING,
    COMPLETED,
    FAILED,
    CANCELLED;

    public boolean isActive() {
        return this == PENDING
                || this == RUNNING
                || this == WAITING_APPROVAL
                || this == CANCELLING;
    }

    public boolean isTerminal() {
        return this == COMPLETED || this == FAILED || this == CANCELLED;
    }

    public boolean canTransitionTo(RunStatus next) {
        return switch (this) {
            case PENDING -> next == RUNNING || next == CANCELLING || next == FAILED;
            case RUNNING -> next == WAITING_APPROVAL
                    || next == COMPLETED
                    || next == FAILED
                    || next == CANCELLING;
            case WAITING_APPROVAL -> next == RUNNING
                    || next == FAILED
                    || next == CANCELLING;
            case CANCELLING -> next == CANCELLED;
            case COMPLETED, FAILED, CANCELLED -> false;
        };
    }
}
