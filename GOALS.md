# Pilot — product goals

## Mission

Make a smart home that does not require being a programmer. The home learns
from the owner instead of the owner programming the home.

## Goals

1. **Proactivity.** The agent notices, thinks, and proposes first — this is the
   core differentiator from reactive assistants (Assist, conversation agents,
   chatbots).
2. **Long-term goals.** A closed loop "goal → metric → deviation → hypothesis →
   adjustment → metric again", living for months and seasons (energy savings,
   comfort).
3. **Derived states.** The agent operates in human-level concepts — "awake",
   "away", "guests", "insomnia" — not raw sensor streams.
4. **Affordable operation.** Deterministic layers cost zero tokens; the LLM
   runs rarely, in short runs, on inexpensive models. A hard daily budget
   guards the loop.
5. **Privacy.** All memory and inference are local. The owner's facts never
   leave the home.
6. **Trust as a product feature, not an option.** Narrow auto-apply whitelist,
   explicit approvals for irreversible actions, append-only audit log,
   one-click rollback, visible per-action cost.
7. **Native Home Assistant citizenship.** Not a sidecar: a custom integration,
   an add-on, devices/entities, sidebar panels, the Assist pipeline, services,
   repairs, diagnostics — everything a HA user already knows.
8. **Soft degradation.** The deterministic layers keep working with the agent
   dead; the home is never worse than a plain HA installation.

## Non-goals (first versions)

- No real-time LLM control (never, not just "not yet").
- No own automation engine (HA automations remain the execution layer).
- No hardware repair, cameras, media, torrents.
- No complex multi-step YAML automations without confirmation.
- No multi-person household modeling (one owner per home initially).

## Roadmap in one line

MVP in the owner's own home (goal supervisor + pattern log + first derived
states) → a season of real operation → packaging as an HA add-on (+ HACS
panel) → publication. Rules from accepted proposals come after trust has
accumulated, not before.

## Success criteria

- Measurable savings ("saved X ₽/month"), visible on a tariff-aware display.
- Number of accepted proposals; low decline rate (no notification spam).
- Audit answers "what did you change this week?" in one command.
