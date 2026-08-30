import type {
  HakoEvent,
  RunStatus,
  StopReason,
  VerificationEvidence,
} from "../types/api";
import { readPayload } from "./presentation";

export type RunPresentationKind =
  | "conversation"
  | "analysis"
  | "verified_change"
  | "unverified"
  | "cancelled"
  | "denied"
  | "error"
  | "incomplete";

export interface ToolActivityPair {
  key: string;
  started: HakoEvent | null;
  finished: HakoEvent | null;
  notes: HakoEvent[];
}

export interface RunPresentation {
  kind: RunPresentationKind;
  stopReason: StopReason | null;
  terminalStatus: RunStatus | null;
  finalText: string;
  changedPaths: string[];
  verification: VerificationEvidence[];
  investigatedPaths: string[];
  toolActivities: ToolActivityPair[];
  hasWorkspaceToolActivity: boolean;
  steps: number;
  totalTokens: number;
}

const SCOPE_TOOLS = new Set([
  "list_dir",
  "read_file",
  "search_code",
  "search_files",
  "delegate_readonly",
]);

export function deriveRunPresentation(
  resultEvent: HakoEvent,
  runEvents: HakoEvent[],
): RunPresentation {
  const stopReason = stopReasonValue(readPayload(resultEvent.payload, "stopReason"));
  const terminalStatus = findTerminalStatus(runEvents);
  const changedPaths = stringList(readPayload(resultEvent.payload, "changedPaths"));
  const verification = verificationList(readPayload(resultEvent.payload, "verification"));
  const toolActivities = pairToolActivities(runEvents);
  const hasWorkspaceToolActivity = toolActivities.length > 0;
  const hasRealVerification = verification.some(
    (item) => Boolean(item.kind.trim() && item.command.trim() && item.summary.trim()),
  );

  let kind: RunPresentationKind;
  if (stopReason === "done_verified") {
    kind = changedPaths.length > 0 && hasRealVerification
      ? "verified_change"
      : "unverified";
  } else if (stopReason === "done_read_only") {
    kind = changedPaths.length > 0
      ? "unverified"
      : hasWorkspaceToolActivity
        ? "analysis"
        : "conversation";
  } else if (stopReason === "done_unverified") {
    kind = "unverified";
  } else if (stopReason === "cancelled" || terminalStatus === "CANCELLED") {
    kind = "cancelled";
  } else if (stopReason === "denied") {
    kind = "denied";
  } else if (stopReason === "error" || terminalStatus === "FAILED" || hasError(runEvents)) {
    kind = "error";
  } else {
    kind = "incomplete";
  }

  return {
    kind,
    stopReason,
    terminalStatus,
    finalText: stringValue(readPayload(resultEvent.payload, "finalText")),
    changedPaths,
    verification,
    investigatedPaths: collectInvestigatedPaths(toolActivities),
    toolActivities,
    hasWorkspaceToolActivity,
    steps: numberValue(readPayload(resultEvent.payload, "steps")),
    totalTokens: numberValue(readPayload(resultEvent.payload, "totalTokens")),
  };
}

export function pairToolActivities(events: HakoEvent[]): ToolActivityPair[] {
  const result: ToolActivityPair[] = [];
  const pending = new Map<string, ToolActivityPair>();
  let notes: HakoEvent[] = [];

  for (const event of events) {
    if (event.type === "assistant_text") {
      notes.push(event);
      continue;
    }
    if (event.type === "tool_call_started") {
      const callId = stringValue(readPayload(event.payload, "callId")) || `event-${event.eventId}`;
      const activity: ToolActivityPair = {
        key: `${callId}-${event.eventId}`,
        started: event,
        finished: null,
        notes,
      };
      notes = [];
      pending.set(callId, activity);
      result.push(activity);
      continue;
    }
    if (event.type === "tool_call_finished") {
      const callId = stringValue(readPayload(event.payload, "callId"));
      const activity = pending.get(callId);
      if (activity) {
        activity.finished = event;
        pending.delete(callId);
      } else {
        result.push({
          key: `finished-${callId || event.eventId}-${event.eventId}`,
          started: null,
          finished: event,
          notes,
        });
        notes = [];
      }
    }
  }
  return result;
}

function collectInvestigatedPaths(activities: ToolActivityPair[]): string[] {
  const paths = new Set<string>();
  for (const activity of activities) {
    const name = stringValue(readPayload(
      activity.started?.payload ?? activity.finished?.payload,
      "name",
    ));
    if (!SCOPE_TOOLS.has(name)) continue;

    const args = readPayload(activity.started?.payload, "args");
    if (args && typeof args === "object") {
      for (const key of ["path", "file", "filePath"]) {
        addPath(paths, readPayload(args, key));
      }
    }
    for (const key of ["touchedPaths", "modifiedPaths", "createdPaths", "deletedPaths"]) {
      for (const path of stringList(readPayload(activity.finished?.payload, key))) {
        addPath(paths, path);
      }
    }
  }
  return [...paths];
}

function addPath(paths: Set<string>, value: unknown): void {
  if (typeof value !== "string") return;
  const path = value.trim();
  if (path) paths.add(path);
}

function findTerminalStatus(events: HakoEvent[]): RunStatus | null {
  for (const event of [...events].reverse()) {
    if (event.type !== "run_status") continue;
    const current = readPayload(event.payload, "current");
    if (typeof current === "string") return current as RunStatus;
  }
  return null;
}

function hasError(events: HakoEvent[]): boolean {
  return events.some((event) => ["agent_error", "worker_error"].includes(event.type));
}

function verificationList(value: unknown): VerificationEvidence[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is VerificationEvidence => {
    if (!item || typeof item !== "object") return false;
    return typeof readPayload(item, "kind") === "string"
      && typeof readPayload(item, "command") === "string"
      && typeof readPayload(item, "summary") === "string"
      && typeof readPayload(item, "step") === "number";
  });
}

function stopReasonValue(value: unknown): StopReason | null {
  return typeof value === "string" ? value as StopReason : null;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function numberValue(value: unknown): number {
  return typeof value === "number" ? value : 0;
}
