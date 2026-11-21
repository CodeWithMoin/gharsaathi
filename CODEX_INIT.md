# Codex Init — Start Here

You are taking over implementation of the Household Shopping Agent.

## Context

Read `AGENTS.md` completely before changing code. It contains the product decisions, architecture, non-goals, security constraints, and acceptance criteria.

The product is a voice-first, Hinglish-native household shopping agent. The selected stack is Next.js + TypeScript, FastAPI, OpenAI Realtime, and Swiggy Instamart MCP as the first commerce integration.

## Your first assignment

Do not jump into Realtime voice or checkout yet. First complete the Swiggy product-search milestone.

1. Inspect the repository and current git status.
2. Review the existing frontend/backend scaffold and identify correctness issues.
3. Verify the current official Swiggy Instamart MCP documentation and inspect the real tool catalogue/schema. The scaffold's guessed tool names/arguments may be wrong.
4. Implement the documented OAuth 2.1 + PKCE flow for a local single-user development setup. Keep credentials server-side, validate state, and never log tokens.
5. Implement address listing and address-specific product search using verified MCP schemas.
6. Wire the UI for account connection, address selection, product search, loading/error/empty states.
7. Add tests using mocked MCP responses. Do not mutate carts or place orders.
8. Run tests and frontend/backend checks. Fix issues you find.
9. Update README with exact local setup, OAuth callback configuration, environment variables, and verified limitations.

## Important constraints

- Do not implement cart, checkout, or order placement in this first milestone.
- Do not add predictive replenishment, household inventory, or unrelated assistant features.
- Do not invent Swiggy API paths, OAuth details, MCP tool names, schemas, or response fields. Verify them from official docs/live schemas.
- Do not ask the user to paste passwords or OTPs into chat.
- Never expose long-lived OpenAI or Swiggy secrets in the browser.
- Require explicit user approval for all future purchases; that work is out of scope now.
- Keep changes focused and explain tradeoffs.

## First response expected from you

After inspecting the repo, give a short implementation plan and list any required Swiggy developer-console values or user actions. Then proceed with the first milestone. Ask questions only when blocked by a genuinely required detail.
