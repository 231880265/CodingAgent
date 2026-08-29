import { ApiGateway } from "./apiGateway";
import { MockGateway } from "./mockGateway";
import type { HakoGateway } from "../types/api";

export const gatewayMode = import.meta.env.VITE_HAKO_MODE === "api" ? "api" : "mock";

export const gateway: HakoGateway =
  gatewayMode === "api" ? new ApiGateway() : new MockGateway();
