/**
 * Data shaping for the local dev inspector (/dev). Pure functions, unit tested.
 * The page renders only outside production builds and only when the backend
 * answers /api/dev/status (DEV_TOOLS_ENABLED=true, localhost, signed in).
 */

export function inspectorAllowed(nodeEnv, status) {
  return nodeEnv !== "production" && Boolean(status && status.enabled === true);
}

export function formatMs(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "–";
  if (value >= 10_000) return `${(value / 1000).toFixed(1)} s`;
  return `${Math.round(value).toLocaleString("en-IN")} ms`;
}

function toolsOf(turn) {
  return (turn.rounds ?? []).flatMap((round) => (round.tools ?? []).map((tool) => ({ ...tool, round: round.round })));
}

/** One row per turn, newest first as the API returns them. */
export function shapeTurns(turns) {
  return (turns ?? []).map((turn) => {
    const tools = toolsOf(turn);
    const slowest = tools.reduce((best, tool) => (best && best.ms >= (tool.ms ?? 0) ? best : tool), null);
    const totals = turn.totals ?? {};
    const failed = tools.filter((tool) => ["error", "exception", "provider_error"].includes(tool.status) || String(tool.status).startsWith("refused"));
    return {
      id: turn.turn_id,
      at: turn.at,
      channel: turn.channel,
      outcome: turn.outcome,
      model: turn.model,
      promptVersion: turn.prompt_version,
      total: formatMs(turn.total_ms),
      totalMs: turn.total_ms ?? 0,
      rounds: totals.rounds ?? (turn.rounds ?? []).length,
      modelShare: turn.total_ms ? Math.round(((totals.model_ms ?? 0) / turn.total_ms) * 100) : 0,
      toolNames: tools.map((tool) => tool.name),
      slowestTool: slowest ? `${slowest.name} · ${formatMs(slowest.ms)}` : null,
      problems: failed.map((tool) => `${tool.name}: ${tool.status}`),
      tokens: totals.tokens ?? null,
      detail: (turn.rounds ?? []).map((round) => ({
        round: round.round,
        model: formatMs(round.model_ms),
        tokens: round.tokens ?? null,
        tools: (round.tools ?? []).map((tool) => ({
          name: tool.name,
          args: Object.entries(tool.args ?? {}).map(([key, value]) => `${key}=${typeof value === "string" ? value : JSON.stringify(value)}`).join(", "),
          status: tool.status,
          time: formatMs(tool.ms),
          counts: tool.counts ?? null,
          stages: Object.entries(tool.stages ?? {}).map(([key, value]) => `${key} ${formatMs(value)}`),
        })),
      })),
    };
  });
}

export function shapeBaskets(baskets) {
  return (baskets ?? []).map((basket) => ({
    provider: basket.provider,
    label: basket.provider === "zepto" ? "Zepto" : "Instamart",
    summary: `${basket.item_count} items · ${basket.item_total} · rev ${basket.revision}`,
    status: basket.status,
    locked: Boolean(basket.locked),
    canClear: !basket.locked && (basket.lines ?? []).length > 0,
    lines: basket.lines ?? [],
  }));
}

export function doctorCounts(checks) {
  const counts = { green: 0, amber: 0, red: 0 };
  for (const check of checks ?? []) if (check.status in counts) counts[check.status] += 1;
  return counts;
}

/** Memory items grouped by status, confirmed first. */
export function groupMemory(items) {
  const order = ["confirmed", "proposed"];
  const groups = new Map();
  for (const item of items ?? []) {
    if (!groups.has(item.status)) groups.set(item.status, []);
    groups.get(item.status).push(item);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (order.indexOf(a) === -1 ? 9 : order.indexOf(a)) - (order.indexOf(b) === -1 ? 9 : order.indexOf(b)))
    .map(([status, entries]) => ({ status, items: entries }));
}
