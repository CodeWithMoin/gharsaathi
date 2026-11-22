export type TraceTool = {
  name: string;
  args?: Record<string, unknown>;
  status: string;
  ms: number;
  counts?: Record<string, number>;
  stages?: Record<string, number>;
};
export type TraceRound = { round: number; model_ms: number; tokens?: Record<string, number>; tools: TraceTool[] };
export type TraceTurn = {
  turn_id: string;
  at: string;
  channel: string;
  outcome: string;
  model: string;
  prompt_version: string;
  total_ms: number;
  rounds: TraceRound[];
  totals?: { rounds?: number; tools?: number; model_ms?: number; tool_ms?: number; tokens?: Record<string, number> };
};
export type ShapedTurn = {
  id: string;
  at: string;
  channel: string;
  outcome: string;
  model: string;
  promptVersion: string;
  total: string;
  totalMs: number;
  rounds: number;
  modelShare: number;
  toolNames: string[];
  slowestTool: string | null;
  problems: string[];
  tokens: Record<string, number> | null;
  detail: {
    round: number;
    model: string;
    tokens: Record<string, number> | null;
    tools: { name: string; args: string; status: string; time: string; counts: Record<string, number> | null; stages: string[] }[];
  }[];
};
export type DevBasketLine = { name: string; pack_size?: string | null; quantity: number; unit_price: string; line_total: string };
export type DevBasket = {
  provider: string;
  status: string;
  revision: number;
  locked: boolean;
  item_count: number;
  item_total: string;
  lines: DevBasketLine[];
};
export type ShapedBasket = {
  provider: string;
  label: string;
  summary: string;
  status: string;
  locked: boolean;
  canClear: boolean;
  lines: DevBasketLine[];
};
export type DoctorCheck = { name: string; status: "green" | "amber" | "red"; detail: string; fix?: string | null };
export type DevMemory = { id: string; status: string; kind?: string | null; origin?: string; text: string; created_at?: string | null };

export function inspectorAllowed(nodeEnv: string | undefined, status: { enabled?: boolean } | null): boolean;
export function formatMs(value: unknown): string;
export function shapeTurns(turns: TraceTurn[] | null | undefined): ShapedTurn[];
export function shapeBaskets(baskets: DevBasket[] | null | undefined): ShapedBasket[];
export function doctorCounts(checks: DoctorCheck[] | null | undefined): { green: number; amber: number; red: number };
export function groupMemory(items: DevMemory[] | null | undefined): { status: string; items: DevMemory[] }[];
