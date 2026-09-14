# Pilot — proactive AI agent for Home Assistant

Pilot is not an "assistant on demand". It is a **proactive home manager**: it
watches the household, derives human-level states ("awake", "away", "guests"),
keeps the owner's long-term goals, and proposes — and, as trust grows, applies —
adjustments to the home's policy. The home learns from the owner, not the other
way around.

## Why

- Trigger-based automations catch events, not intentions ("TV at 23:00" is a
  movie night, background noise, or insomnia).
- A hundred-device home can never be hand-tuned to completion; policy goes stale
  with seasons, habits, and device degradation.
- The value of a smart home is under-used because owners don't know what it
  "can do".
- Notification streams from the home turn into noise.

## Core principles

1. **The LLM is never in the real-time loop.** Frequent work is deterministic
   code (zero tokens); the AI runs rarely, over prepared data.
2. **Home Assistant is the single source of truth.** Real-time execution belongs
   to HA automations; the agent changes policy, it does not flip switches.
3. **Soft degradation.** If the agent dies, the home keeps working as a plain
   HA installation.
4. **Trust is earned, then grown.** A narrow auto-apply whitelist, a "yes/no"
   approval queue, an append-only audit log, a hard daily budget, and one-click
   rollback. Destructive/irreversible actions always require confirmation.

## What it does

- **Supervisor of goals**: deterministic checkers notice deviations (dead
  sensors, climate without effect, energy spikes, stuck lights or gates); the
  agent runs once a day over the flagged facts and adjusts setpoints within an
  explicit whitelist.
- **Pattern log**: repeated manual actions become candidates for automations —
  rules grow out of *accepted proposals*, never out of guessed patterns.
- **Butler**: contextual suggestions ("Good morning — coffee in two minutes,
  as usual?") with a persona the owner tunes (butler ↔ quiet observer,
  polite ↔ decides alone, dry ↔ chatty, conservative ↔ experimenter).
- **Derived states**: the agent reasons in human terms ("awake", "sleeps",
  "away-but-back-soon", "guests") instead of raw sensors.
- **Local memory**: raw logs, daily digests, vector search over digests,
  structured facts about the owner, self-maintained skills — all on-device.

## Architecture (target)

Pilot ships as two artifacts of one repository, following the ESPHome /
Music Assistant pattern:

- **`custom_components/pilot`** — a native Home Assistant custom integration:
  one "Pilot" device with entities (status, daily cost, suggestion queue,
  persona sliders, presets, reset buttons), a native sidebar panel for daily
  use, a conversation agent in the Assist pipeline with deterministic handling
  of simple intents, services for automations, repairs/diagnostics/system
  health.
- **A Home Assistant add-on** — the OpenClaw runtime: deterministic layers
  (detectors, derived states, goal metrics, pattern log, trust loop, state
  mirror), daily LLM runs, local memory, and a full-featured admin UI
  ("workshop") served via ingress.

MVP scope: local add-on mode only. A "remote instance" connection mode is
architecturally reserved for power users who run the runtime on their own
machine.

## Status

Concept and architecture design are done; a standalone prototype of the state
mirror runs in the owner's home. The integration and the add-on are under
development.

See [GOALS.md](GOALS.md) for product goals and [LICENSES.md](LICENSES.md) for
licensing.

## Author

Pilot is developed and maintained by
[Andrey Lipanov](https://www.linkedin.com/in/andrey-lipanov-5a3481122/).
Ideas, questions, and collaboration offers — via
[Issues](https://github.com/Glukmann/HA_Pilot/issues) or LinkedIn.
