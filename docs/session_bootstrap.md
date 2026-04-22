# Session Bootstrap

For fresh Claude Code (or Claude chat) sessions starting work on this project.

---

## On session start

### 1. Read in order
1. `README.md` — what simkit is
2. `PROJECT_STATE.md` — where we are right now
3. `DECISIONS.md` — why we decided the big things the way we did
4. `TODO.md` — unchecked items are the current-phase work
5. `PHASE_PLAN.md` — for context on where the project is heading beyond the current phase

### 2. Check memory
The host has persistent memory at `~/.claude/projects/-home-yusheng-cadence-work-Test-workarea/memory/`. Read `MEMORY.md` (the index) and any files relevant to the current task. Especially:
- `user_role.md` — who the user is, work/home env split, offline-deploy constraint
- `project_sim_db.md` — the stable architectural context for this project
- `project_standard_tb_generator.md` — a parked Pillar-1 idea
- `project_skill_docs.md` — where the 44 Cadence reference PDFs live

### 3. Check the code
- `git log --oneline -20` for recent changes
- Scan `skill/` and `python/` trees to see what's actually been built

### 4. Report back in 3–5 sentences
Tell the user:
- Current phase and how far into it
- What was last worked on
- What the obvious next task is (from `TODO.md`)
- Any open questions blocking progress

**Then wait for the user's direction. Do not start coding unprompted.**

---

## On ending a work session (or finishing a chunk)

Do all of these — they are cheap and they are what keep the project alive across sessions:

1. Update `PROJECT_STATE.md` with what changed, what's now in progress, what's next, any new open questions.
2. Append to `DECISIONS.md` if a new architectural decision was made (use the existing format).
3. Check off completed items in `TODO.md`.
4. Drop Phase-2-worthy ideas into `PHASE_PLAN.md` (don't leave them floating in `TODO.md`).
5. Update memory **only** if your understanding of the user or project fundamentals shifted — not for progress.
6. Commit with a descriptive message.

---

## Conventions

- Write in concise, direct English (or Chinese if that matches the user's message).
- No emoji in code/docs unless the user explicitly asks.
- Dates: ISO format `YYYY-MM-DD`.
- In `DECISIONS.md`, never delete an entry. If a decision is reversed, append a new one that supersedes it and cross-reference.
- Write code comments only for the WHY — never the WHAT. Most code needs no comments.

---

## Where to put what (persistence map)

| Lives in | What goes there | Lifecycle |
|---|---|---|
| Host memory (`memory/`) | User identity, project philosophy, stable architectural decisions' "why", cross-session conventions | Stable, across sessions |
| `DECISIONS.md` (repo) | Architectural decisions with date and rationale | Permanent, append-mostly |
| `PROJECT_STATE.md` (repo) | Current phase, recent work, next steps, open questions | Mutable, updated every session |
| `TODO.md` (repo) | Current-phase tasks with checkboxes | Mutable, scoped to phase |
| `PHASE_PLAN.md` (repo) | Future phases, parked ideas | Grows slowly |
| In-session `TaskCreate` | Breaking down an active item into sub-steps during implementation | Ephemeral, session-scoped |

If you catch yourself putting something in the wrong layer (e.g., "next task" in memory, or "user's role" in TODO.md), move it.
