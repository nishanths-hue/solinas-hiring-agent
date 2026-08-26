# Solinas Hiring Management System — Agent Layer (v0)

## What this is
A working slice of the operating design doc, not the whole thing. It implements
the two most AI-native workflows end to end against a real schema:

1. **Hiring request → JD + hiring asset generation** (Section 12)
2. **Resume → fit score / risk flags / priority bucket** (Section 18)

Plus the one piece of "AI plumbing" every other workflow in the doc depends on:
3. **SLA clock + escalation ladder** (Sections 27–29) — deliberately *not* an
   LLM call. It's timestamp arithmetic. Running it through a model would add
   cost and hallucination risk for zero benefit.

## What this deliberately does NOT include yet
Being upfront about scope, because the source doc covers 38 sections and building
all of it "from scratch" in one pass produces something that looks complete and
isn't — that's a worse outcome than a narrow thing that actually works.

Not built:
- Interviewer evaluation workflow (Section 20) — coverage/confidence/assessment capture
- Assignment repository matching + scoring rollup (Section 21)
- Reference check AI question generation + summarization (Section 22)
- Duplicate candidate detection (Section 17)
- Dashboards (Sections 30–36) — these are read views over the schema below;
  cheap to build once real data exists, expensive to build against fake data
- Permissions layer (Section 38) — needs a real auth system decision first
- Posting workflow / channel status tracking (Section 14)
- Recruiter screening notes UI (schema exists, no agent writes to it yet —
  intentionally, since Section 19 frames this as a *human* workflow the AI
  output feeds into, not one the AI drives)

This is a build-order decision, not an oversight: screening + JD generation
are the only two workflows in the doc where "AI does the first draft, human
approves" is unambiguous. Everything else (interview scoring, reference
summarization) touches human judgment calls closely enough that I'd want your
sign-off on where the AI/human line sits before writing an agent that crosses it.

## Architecture

```
hiring_agent/
├── schema.sql              # reference schema, matches Section 6 (used by Mode 1)
├── db.py                   # raw sqlite access layer — Mode 1 only
├── orchestrator.py         # Mode 1: standalone script demo
├── Dockerfile               # builds the app/ service for any container host
├── render.yaml              # one-file Render deploy (web service + managed Postgres)
├── agents/
│   ├── jd_agent.py               # Section 12 — used by both modes
│   ├── resume_screening_agent.py # Section 18 — used by both modes
│   └── sla_agent.py              # Sections 27-29, no LLM — Mode 1 version
├── scripts/
│   └── create_first_admin.py     # one-time bootstrap: first leadership login
└── app/                     # Mode 2: the hosted, multi-user service
    ├── main.py                   # FastAPI entrypoint
    ├── models.py                  # SQLAlchemy models (Postgres in prod, sqlite in dev)
    ├── auth.py                    # JWT auth, 4 roles from Section 38
    ├── permissions.py             # field-level filtering, Sections 10 & 38
    ├── sla.py                     # Mode 2 version of the SLA ladder
    └── routers/
        ├── auth.py, roles.py, candidates.py, dashboard.py
```

Design choices worth flagging explicitly:

- **Every AI write is explainable by construction.** `resume_screening_results`
  has a mandatory `score_explanation` column — the schema itself won't accept
  an unexplained score. This directly enforces the doc's Section 18 requirement,
  rather than relying on a prompt instruction that could silently drift.
- **The AI never advances a candidate's stage.** `resume_screening_agent.py`
  only writes to `resume_screening_results`; `orchestrator.py` is the one that
  moves `candidates.stage`, and it does so as an explicit, auditable step. This
  is the schema-level enforcement of Section 2 ("AI assists, humans decide").
- **`activity_timeline` is written on every state change**, including who/what
  triggered it — this is what Section 15 calls "the operational source of truth,"
  and it's the table your dashboards will eventually read from.
- **Curated interview questions can't be silently overwritten.** The
  `evaluation_question_repository` table has a `curated_locked` flag — Section
  20 explicitly says AI may add/suggest but "may not remove preferred questions
  or overwrite curated questions." That's a constraint worth enforcing at the
  data layer, not just in a prompt.

## Running it — two modes

### Mode 1: standalone script (no server, no accounts)
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...
python orchestrator.py              # full demo: role -> JD -> candidate -> screening -> SLA
python orchestrator.py --no-api     # schema/DB smoke test only, no API key needed
```
Uses `db.py` (raw sqlite3), no auth. Good for testing agent logic in isolation.

### Mode 2: the actual hosted app (`app/`)
This is what "host it in the cloud with HR/Leadership access" means in practice —
a FastAPI server with the 4 roles from Section 38, JWT auth, and field-level
permission enforcement (tested, see below).

```bash
pip install -r requirements.txt
export JWT_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
export ANTHROPIC_API_KEY=sk-...
python -m scripts.create_first_admin     # one-time: creates the first leadership account
uvicorn app.main:app --reload
```
Visit `http://localhost:8000/docs` for the interactive API (Swagger UI) —
log in via `/auth/login`, use the returned token as a Bearer token on
everything else.

**Verified, not assumed:** I ran this end to end — created a leadership account,
created a role with `suggested_compensation_range` set, fetched it as leadership
(compensation present), fetched the identical role as a hiring_manager (compensation,
offer strategy notes, and internal risk notes are absent from the JSON entirely,
not just hidden), and confirmed a hiring_manager gets a 403 attempting to edit
compensation. That's Section 10 + Section 38 enforced at the data layer, not just
described in a comment.

## Deploying to the cloud (Render, as discussed)

1. Push this directory to a GitHub repo.
2. In Render: New → Blueprint → point at the repo. `render.yaml` provisions
   a web service + a managed Postgres instance in one step.
3. Set `ANTHROPIC_API_KEY` in the Render dashboard (marked `sync: false` in
   `render.yaml` so it's never committed to the repo).
4. `JWT_SECRET_KEY` and `DATABASE_URL` are generated/wired automatically by
   the blueprint.
5. Once deployed, run `python -m scripts.create_first_admin` **once**, pointed
   at the production `DATABASE_URL`, to create the first leadership login.
   Every other account gets created afterward through `/auth/users` by someone
   already holding a leadership token.

If you're on AWS/GCP/Azure instead of Render: the `Dockerfile` is the only
thing that matters — it builds identically anywhere that runs containers
(ECS, Cloud Run, Azure Container Apps). Swap `render.yaml` for that platform's
equivalent (task definition, Cloud Run service config, etc.) and point
`DATABASE_URL` at a managed Postgres instance there instead.

Each agent module is also independently runnable for testing in isolation:
```bash
python agents/jd_agent.py
python agents/resume_screening_agent.py
```

## What changed since v0 (this update)

Closed 3 of the 4 gaps flagged previously:
- ✅ Auth + permissions layer — `app/auth.py`, `app/permissions.py`, tested live
- ✅ Postgres-ready — `app/models.py` runs on `DATABASE_URL`, sqlite locally / Postgres via `render.yaml`
- ✅ Deployment target chosen — Dockerfile + Render blueprint, portable to any container host

## Honest gaps still open

1. **Resume parsing is still a stub.** Same as before — Affinda/RChilli/HireEZ
   wiring is an account decision I can't make for you.
2. **Only 2 of the 4 endpoint groups exist.** `roles` and `candidates` are live.
   Interviews, assignments, reference checks, and the full dashboard set
   (Sections 20-22, 30-36) don't have routes yet — the DB models exist in
   `app/models.py` for `Interview`, but there's no router exposing them.
3. **No frontend.** Everything above is API-only, tested via curl/Swagger UI.
   HR and Leadership hitting `/docs` and reading raw JSON is not what you
   want people using day to day — this needs an actual UI layer next.
4. **`hiring_manager` and `interviewer` accounts can currently only be
   created by a leadership user through `/auth/users`** — there's no
   self-signup or invite-link flow. Fine for a first rollout with <20 people,
   not fine at scale.

Given gap #3, my honest read is that the next real decision isn't another
backend section — it's whether HR/Leadership interact with this through a
built UI, through Swagger UI as a stopgap, or through something like a
Retool/internal-tools layer on top of this API. That choice changes what
"next" means more than picking interviews vs. assignments vs. dashboards does.
