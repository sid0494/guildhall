# Guildhall: Design Spec

Nov 7, 2026 · @sid0494

## Overview

Guildhall is a personal gaming companion that lives in Discord. It pulls data from the games and stores I use, turns it into reports, progress tracking and alerts, and answers slash commands. Each game or data source is a plug-in on a shared platform, so adding a new one never means rewriting the core.

| Plug-in | Game or source | What it does | Status |
| --- | --- | --- | --- |
| Gank Report | Dota 2 | Post-match skill reports and weekly trends on the skills I'm working on | First |
| Side Quest | Steam library and wishlist | Backlog tracking, what-to-play suggestions, sale alerts at real lows | Second |
| Rune Ledger | Elden Ring | Playthrough checklist: bosses, graces, questlines, key items | Later |

### Goals

- Track skill progress against my own history, not just rank.
- Keep the backlog honest: what I own, what I've started, what's worth buying and when.
- Make a new plug-in a self-contained piece of work behind one stable contract.
- Run reliably with no babysitting: retries, deduplication and upstream rate limits are handled by the platform.
- Run on containers locally and on AWS with infrastructure as code, cheaply.

### Non-goals

- Call of Duty: no official public stats API; the available wrappers need Activision login credentials and private endpoints.
- A web frontend. Discord is the interface.
- Real-time in-match data, overlays, or anything that touches the game client.
- Supporting many users. A few friends may link accounts later; the design shouldn't prevent it, but it isn't built for scale.

## Architecture

One container image runs as two Deployments: `api` handles Discord's signed requests and nothing slow; `worker` runs the platform loop that hosts the plug-ins. All slow work goes through SQS, so the api always answers Discord within 3 seconds.

```mermaid
flowchart TB
    discord["Discord<br/>my server"]

    subgraph cluster["Kubernetes cluster (kind locally, EKS on AWS)"]
        api["api<br/>verifies signature, replies in 3 s"]
        subgraph worker["Guildhall worker pods (platform)<br/>schedules · dedupe · retries · rate limits · outbox"]
            gank["Gank Report<br/>Dota 2 match metrics"]
            quest["Side Quest<br/>library, price alerts"]
        end
        redis[("Redis<br/>rate-limit buckets")]
    end

    sqs[["SQS job queue<br/>+ dead-letter queue"]]
    pg[("Postgres<br/>RDS on AWS")]
    s3[("S3<br/>raw payload archive")]

    subgraph upstream["Upstream APIs"]
        opendota["OpenDota"]
        steam["Steam Web API"]
        itad["IsThereAnyDeal"]
    end

    discord -- "slash commands (signed HTTPS)" --> api
    api -- "slow commands" --> sqs
    sqs -- "jobs" --> worker
    worker -- "reports, alerts (outbox)" --> discord
    api --> pg
    worker --> pg
    worker --> s3
    worker --> redis
    gank -- "rate-limited" --> opendota
    quest -- "rate-limited" --> steam
    quest -- "rate-limited" --> itad

    classDef plugin fill:#e7f1fb,stroke:#2f6fdb,stroke-width:2px
    class gank,quest plugin
```

The highlighted plug-ins are the only game-specific code; everything else is platform.

### Repository layout

```
guildhall/
  src/guildhall/
    bootstrap.py          composition root: wires the plug-in catalogue into the platform
    contract/             the plug-in contract: interface, job and event types, errors
    platform/             worker loop, dedupe, rate limiter, outbox, plug-in registry, logging
    plugins/gank_report/  poll_matches, fetch_match, analyze_match, metrics, commands, render
    plugins/side_quest/   library and wishlist sync, prices, alerts, commands
    plugins/synthetic/    payload-driven plug-in for end-to-end tests, drills and load checks
    api/                  FastAPI app and the Discord interactions endpoint
  migrations/           Alembic
  compose.yaml          local infrastructure: Postgres, Redis, moto
  tests/                unit, contract and fixture tests; mock upstream server
  scripts/              development tools, such as the fixture recorder
  k8s/base/             Deployments, Services, Ingress, probes
  k8s/overlays/         local (kind) and aws (EKS)
  infra/                Terraform: VPC, EKS, ECR, RDS, SQS, S3
  docs/adr/             decision records
  .github/workflows/
  Makefile              up, down, test, drill
```

All Python code is one `guildhall` package in a src layout, so `guildhall.platform` never shadows the standard library's `platform` module ([ADR 0003](adr/0003-single-package-src-layout.md)).

**Stack:** Python 3.14 managed with uv ([ADR 0002](adr/0002-python-3-14-and-uv.md)), FastAPI, PostgreSQL through SQLAlchemy Core and psycopg 3 with Alembic migrations ([ADR 0013](adr/0013-database-access.md)), Redis, AWS SQS and S3, Docker, Kubernetes, Terraform, GitHub Actions.

## Plug-in contract

The platform owns everything cross-cutting: scheduling, queueing, retries, rate limiting, deduplication, storage and Discord delivery. A plug-in declares the kinds of jobs it handles; each handler does one piece of work and describes the result.

The contract lives in its own package, `guildhall.contract`. Plug-ins import from it and never from `guildhall.platform`, so dependencies point one way: plug-ins → contract ← platform ([ADR 0008](adr/0008-plugin-contract-shape.md)). Contract values are frozen pydantic models that ignore unknown fields ([ADR 0009](adr/0009-pydantic-contract-values.md)). In outline (the package is the source of truth):

```python
class SourcePlugin(ABC):
    contract_version: ClassVar[int] = 1  # bumped only on breaking changes
    name: str  # "gank_report", "side_quest"
    rate_limit: RateLimitPolicy  # per-minute and per-day budget for the upstream API
    tables: MetaData  # own tables, from plugin_metadata(); none by default

    @abstractmethod
    def job_types(self) -> list[JobType[Any]]:
        """Every kind of job, e.g. JobType("fetch_match", FetchMatch, self.fetch_match)."""

    def schedules(self) -> list[Schedule]:
        """Recurring triggers, e.g. Schedule(job_type="poll_matches", cron="*/15 * * * *").
        A scheduled job type takes ScheduledRun(scheduled_for, account) as its payload."""
        return []


# Every handler has the same shape:
async def fetch_match(self, job: Job[FetchMatch], ctx: Context) -> Outcome:
    """job.payload is already validated against FetchMatch.
    Call the upstream with `await ctx.http.get(url)`. Read own tables with
    `await ctx.db.fetch(select(...))` and archived responses with
    `await ctx.archive.get(kind, id)`. Return
    Outcome(writes=..., archives=..., events=..., jobs=...) when done, or
    Outcome(retry_in=...) when the work isn't ready yet. Raise RetryableError or
    PermanentError on failure."""
```

- `JobSpec` is work a handler asks for: type, payload, `dedupe_key` (e.g. `gank_report:match:8123456789`) and an optional delay. Any handler can return follow-up jobs, so polling is just a scheduled job type whose outcome is new jobs.
- `Outcome(retry_in=...)` is an expected wait, such as a match that isn't parsed yet. The job runs again with `attempt + 1` and it doesn't count as a failure; it emits nothing until it finishes.
- A plug-in's writes are SQLAlchemy statements in `Outcome.writes`; the platform runs them in the job's transaction, after its dedupe claim and with its outbox rows, so all of it commits or none does ([ADR 0028](adr/0028-plugin-writes-declared-in-the-outcome.md)). Reads go through `ctx.db.fetch`, one read-only transaction per call ([ADR 0030](adr/0030-plugin-reads-through-a-checked-fetch.md)). Tables are declared on the plug-in and migrated in the one Alembic history ([ADR 0029](adr/0029-plugin-tables-and-migrations.md)).
- Raw upstream responses are kept by returning `Archive(kind, id, body)` in `Outcome.archives`; the platform uploads them to S3 before the job commits, so a committed job's archives always exist, and `ctx.archive.get` reads them back ([ADR 0031](adr/0031-raw-archive-declared-in-the-outcome.md), [ADR 0032](adr/0032-archive-keys-bucket-and-aws-settings.md)).
- Schedules fire from every worker: the first to claim a tick in Postgres queues its jobs through the outbox, so each tick fires once. Ticks missed while no worker ran coalesce into one run; a new schedule first fires at its next tick. A `per_account` schedule creates one job per linked account, keyed by the link's row ID (`LinkedAccount.id`), never the account's external ID ([ADR 0036](adr/0036-scheduling-in-workers-with-a-postgres-claim.md), [ADR 0037](adr/0037-missed-ticks-fire-once.md), [ADR 0038](adr/0038-scheduled-runs-and-linked-accounts.md)).
- Upstream calls go through `ctx.http.get` and `.post`, which return the contract's own `HttpResponse`. Each call takes from the plug-in's shared budget; a short wait happens in place, while a longer one, a `429` or an unreachable limiter defers the whole job without counting it as an attempt. `5xx`, timeouts and connection failures raise `RetryableError`; every other status is the plug-in's to judge. Handlers must not catch broad exceptions around `ctx.http` ([ADR 0033](adr/0033-throttled-jobs-are-deferred.md), [ADR 0035](adr/0035-plugin-http-contract.md)).
- Discord commands (`commands()`) and rendering (`render()`, no I/O) join the contract in M2 as members with defaults, so existing plug-ins are unaffected.

### Rules

- Plug-ins never call Discord, SQS or each other, and import only `guildhall.contract`. They return jobs and events; the platform delivers them. A broken plug-in can't take down the bot.
- Every job and event has a `dedupe_key`, namespaced by plug-in name. The platform guarantees each key takes effect once.
- Errors are typed. `RetryableError` goes back on the queue with exponential backoff and jitter; `PermanentError` goes to the dead-letter queue with its reason. A payload that fails validation is dead-lettered before plug-in code runs. Handlers run under a timeout ([ADR 0018](adr/0018-job-runner.md)).
- Handlers share one event loop with the other jobs in their worker: they await I/O and move CPU-heavy work to `asyncio.to_thread`, or every job in the process stalls ([ADR 0021](adr/0021-worker-concurrency.md)).
- Each plug-in has its own rate-limit budget, so Side Quest checking prices can't eat Gank Report's OpenDota budget. Errors and logs about upstream calls never include query strings or headers, where API keys go.
- Each plug-in owns its tables, named `<plug-in>_...`, and never reads or writes another's. Every statement it reads or writes with is checked: tables it doesn't declare and raw SQL text are refused and dead-lettered as contract violations. Likewise it reaches only its own archived responses: the platform adds the `raw/<plug-in>/` prefix, and a kind or id that could leave it is a contract violation.
- Events carry `type` and `schema_version`. Within a version, changes are additive only; consumers ignore unknown fields.
- Available plug-ins are listed in one catalogue (`guildhall.plugins.CATALOGUE`); the enabled ones are named in `GUILDHALL_PLUGINS`. Disabling one is a config change, not a code change, and the platform refuses to start if a name is unknown ([ADR 0011](adr/0011-settings-and-plugin-catalogue.md)).
- The platform checks every plug-in's declarations at startup and every outcome at runtime (`guildhall.platform.checks`), with the same functions the contract suite uses ([ADR 0010](adr/0010-contract-enforcement.md)).

**Contract health check:** when a new plug-in needs a platform change, record why. Repeated changes in the same area mean the contract is missing a concept.

## Gank Report (Dota 2)

After each match, Gank Report posts a short report on the 3–4 skills I've chosen to work on. Every Sunday it posts the trend. The question it answers is "am I getting better at the things I'm practicing?", measured against my own history per hero.

**Data source:** the OpenDota API. Free, no key required: 60 requests a minute and 3,000 a day per IP address on the free tier (from its `x-rate-limit-remaining-*` headers, 2026-11-10). Gank Report budgets 50 a minute and 2,000 a day, leaving headroom for calls made by hand ([ADR 0048](adr/0048-gank-report-rate-budget.md)).

### Metrics

Some metrics need a replay-parsed match; the rest work from the basic match record.

| Metric | What it shows | Needs parsed match |
| --- | --- | --- |
| Last hits at 10 min | Laning and farming | Yes |
| GPM vs. my average on that hero | Farm efficiency | No |
| Deaths per 10 minutes | Positioning and risk | No |
| Hero damage per minute | Fight impact | No |
| Observer wards placed | Vision work as support | Yes |

Metrics live in a registry of small functions (`match_json → value or missing`), so adding one is a single function and no schema change. Comparisons use a rolling window (default: my last 20 games on that hero).

### Match pipeline

A match is fetched once, whoever played in it; each linked account in it is then analysed from the archived response, so friends in one match cost one fetch and a metric change is recomputed without calling OpenDota ([ADR 0043](adr/0043-fetch-per-match-analyse-per-account.md)).

1. Every 15 minutes, a scheduled `poll_matches` job fetches each linked account's recent matches and, per new match, returns a `fetch_match` job keyed `gank_report:match:<id>` and an `analyze_match` job keyed `gank_report:analyze:<id>:<link id>`.
2. `fetch_match` gets the full match. While it is unparsed, it requests a parse and waits 5, then 10 minutes; the third attempt finishes either way, so parsed-only metrics may be missing. Parsing usually takes 1–5 minutes, and some matches never parse ([ADR 0044](adr/0044-parse-retry-policy.md)).
3. When it finishes, it archives the raw JSON to S3 and records the match row. Responses are read through models of just the fields used; an unexpected one fails permanently, naming fields but never values ([ADR 0047](adr/0047-opendota-response-models.md)).
4. `analyze_match` waits, checking every 5 minutes for up to about two hours, until the match row exists; then it reads the archived match and records the account's part in it ([ADR 0046](adr/0046-analysis-waits-for-the-fetch.md)).
5. Metrics are written to Postgres and a `gank_report.match_analyzed` event goes to the outbox (from B3).

### Reports

- **Post-match:** hero, result, each focus metric with an arrow against my rolling average on that hero.
- **Weekly (Sunday):** per focus metric, this week vs. the previous four, plus the best and worst game of the week.
- **On demand:** `/match last` and `/trend [hero] [weeks]`.

### Backfill

Linking an account backfills the last 100 matches so trends work from day one. That burst is the main reason the rate limiter exists: steady use is only about 30 calls a day.

## Side Quest (Steam backlog and deals)

Side Quest turns my Steam library and wishlist into a managed backlog, suggests what to play next, and alerts me only when a wishlisted game is genuinely cheap.

**Data sources:** the Steam Web API for library, playtime and wishlist (needs a public profile and an API key); the IsThereAnyDeal API for prices across stores and historical lows (free key).

### Features

| Feature | How it works | Frequency |
| --- | --- | --- |
| Library sync | `GetOwnedGames` with total playtime per game | Daily |
| Wishlist sync | `IWishlistService/GetWishlist`; poorly documented, so pin the response shape with a recorded fixture | Daily |
| Backlog status | Inferred from playtime (under 30 min = untouched), overridden by `/status` | On sync |
| Price checks | Steam app ID → IsThereAnyDeal game ID (cached forever), then batched price and historical-low lookups | Twice daily |
| Sale alerts | Posted when a rule matches; see below | On price check |
| What to play | `/next [hours]` picks from untouched and started games | On demand |

### Backlog states

`untouched` → `started` → `finished`, with `dropped` and `on_hold` reachable from `started`. Inferred states never overwrite a state I set by hand (`status_source = manual`).

### Alert rules

An alert fires when the best current price for a wishlisted game is:

- at or below its historical low, or
- at or below my `/watch` target price.

Alerts are idempotent. The dedupe key is `side_quest:deal:<game>:<shop>:<price in cents>:<deal expiry date>`:

- The same deal seen on later checks produces the same key, so no repeat alert.
- A deeper cut is a new price, so a new key and exactly one new alert.
- The key is inserted under a unique constraint in the same transaction as the outbox event, so two workers seeing the deal at once still produce one alert.

Known gap: the same price returning months later with a different expiry date alerts again. That's acceptable; it is a new sale.

### What-to-play suggestions

Start simple: filter by state, rank untouched games by how long they've been owned, and use the hours argument to skip long games when a time estimate is available. Tuning this is optional, not core scope.

## Rune Ledger (Elden Ring, later)

A playthrough checklist the game doesn't have: bosses, Sites of Grace, NPC questlines, legendary items and key collectibles, tracked per playthrough so NG+ and separate PlayStation and Steam runs stay apart. This is a sketch to test the contract against, not a commitment.

**Data:** there is no API for player save data. Game reference data (bosses, items, locations) comes from the community Elden Ring Fan API, which was scraped from wikis and needs cleanup; its DLC coverage is unverified. Progress is logged by me through Discord, with Steam achievements auto-completing bosses and endings for the Steam run. Nothing is available for the PlayStation run beyond manual logging.

**What it would add to the platform:**

- A one-off `ingest` job for reference data, not a recurring poll. The contract's `schedules()` can return nothing; check that the platform handles that cleanly.
- Questline steps that depend on earlier steps: a small dependency graph per questline.
- Commands such as `/done Margit`, `/left Limgrave`, `/quest Ranni`.

If Rune Ledger fits without platform changes, the contract is in good shape.

## Discord interface

Guildhall uses Discord's HTTP interactions endpoint, not a persistent gateway connection. Slash commands arrive as signed HTTPS requests to the api, so the bot is a stateless endpoint behind the load balancer and scales like any other web service. A gateway bot would need one long-lived websocket with reconnects and session state.

### Commands

| Command | Plug-in | What it does |
| --- | --- | --- |
| `/link dota <account id>` | Gank Report | Links my Dota account and starts the backfill |
| `/link steam <steam id>` | Side Quest | Links Steam and runs the first sync |
| `/match last` | Gank Report | Report for the latest analyzed match |
| `/trend [hero] [weeks]` | Gank Report | Focus metrics over time |
| `/focus add\|remove <metric>` | Gank Report | Choose the skills to track |
| `/backlog [state]` | Side Quest | Lists games by state |
| `/next [hours]` | Side Quest | Suggests what to play |
| `/status <game> <state>` | Side Quest | Sets a game's state |
| `/watch <game> [max price]` | Side Quest | Adds a price target |
| `/guildhall status` | Platform | Queue depth, last run per plug-in, dead-letter count |

### Endpoint rules

- **Verify every request.** Check `X-Signature-Ed25519` against the timestamp plus the raw body with the app's public key; reject with `401` on failure. Answer `PING` with `PONG`; Discord uses it to validate the URL.
- **Respond within 3 seconds.** Slow commands return a deferred response at once and enqueue a job; the worker sends the real reply within 15 minutes. `/link` and `/trend` always take this path.
- **Deduplicate by interaction ID.** Discord can retry on network errors, so record the ID under a unique constraint before any side effect.
- **Proactive messages** (match reports, weekly trends, sale alerts) are not interactions. The outbox dispatcher posts them to a configured channel through the Discord API with the bot token.

Local development uses a tunnel (cloudflared or ngrok) so Discord can reach docker-compose.

## Data model

Platform tables are shared; each plug-in owns tables prefixed with its name. Raw upstream responses go to S3, exactly as received, under `raw/<plugin>/<kind>/<id>.json` in one bucket (`GUILDHALL_ARCHIVE_BUCKET`), so a metric change means recomputing from the archive, not re-calling the APIs. Archiving the same kind and id again replaces the body.

| Table | Owner | Key columns | Purpose |
| --- | --- | --- | --- |
| `linked_accounts` | Platform | `id`, `plugin`, `discord_user_id` (unique per plug-in), `external_id`, `linked_at` | Who I am in each source; `per_account` schedules fan out over it |
| `schedule_runs` | Platform | `plugin`, `job_type` (primary key), `fired_for` | The tick each schedule last fired for, claimed with a compare-and-set ([ADR 0036](adr/0036-scheduling-in-workers-with-a-postgres-claim.md)) |
| `processed_jobs` | Platform | `dedupe_key` (primary key), `completed_at` | A job key takes effect once |
| `outbox_events` | Platform | `id`, `plugin`, `dedupe_key` (unique), `type`, `schema_version`, `payload`; from M2 also `status`, `attempts`, `discord_message_id` | Events waiting for Discord delivery |
| `outbox_jobs` | Platform | `id`, `dedupe_key`, `body` (job envelope), `delay_seconds`, `sent_at` | Follow-up jobs waiting for the relay to send them to SQS ([ADR 0017](adr/0017-queue-client-and-outbox-tables.md)) |
| `interactions_seen` | Platform | `interaction_id` (primary key), `received_at` | Discord retry dedupe |
| `synthetic_records` | Synthetic | `work_id`, `attempt`, `recorded_at` | Written by synthetic jobs that ask to be recorded; exercises plug-in storage in tests and drills |
| `gank_report_matches` | Gank Report | `match_id`, `played_at`, `duration_seconds`, `game_mode`, `lobby_type`, `parsed`, `fetched_at` | One row per match, written when its fetch is final ([ADR 0045](adr/0045-gank-report-tables.md)) |
| `gank_report_player_matches` | Gank Report | `match_id`, `account_id` (Dota account; together the primary key), `hero_id`, `player_slot`, `won`, `analyzed_at` | One linked account's part in a match ([ADR 0045](adr/0045-gank-report-tables.md)) |
| `gank_report_metrics` | Gank Report | `match_id`, `account_id`, `metric`, `value` | Long format, so new metrics need no migration |
| `gank_report_focus` | Gank Report | `discord_user_id`, `metric` | Chosen skills |
| `side_quest_games` | Side Quest | `appid`, `name`, `playtime_min`, `state`, `status_source` | Library and backlog state |
| `side_quest_wishlist` | Side Quest | `appid`, `itad_id`, `target_price_cents` | What to watch |
| `side_quest_prices` | Side Quest | `itad_id`, `shop`, `price_cents`, `history_low_cents`, `deal_expiry`, `captured_at` | Self-collected price history |

Money is integer cents plus a currency code; the price region is United States (USD). Timestamps are UTC; Discord renders them in local time.

## Platform guarantees

These are the promises the platform makes to every plug-in. Each one has a test.

| Guarantee | Mechanism |
| --- | --- |
| A job takes effect once, even if delivered twice | SQS is at-least-once; the worker records the job's `dedupe_key` in `processed_jobs` in the same transaction as the plug-in's declared writes and the outbox rows ([ADR 0028](adr/0028-plugin-writes-declared-in-the-outcome.md)) |
| A committed job's raw responses are archived | The runner uploads the outcome's archives to S3 before it commits; a failed or slow upload commits nothing and the job is retried ([ADR 0031](adr/0031-raw-archive-declared-in-the-outcome.md)) |
| A schedule tick fires once, however many workers run | Each worker claims due ticks with a compare-and-set on `schedule_runs` and queues the tick's jobs in the same transaction; missed ticks coalesce into one run ([ADR 0036](adr/0036-scheduling-in-workers-with-a-postgres-claim.md), [ADR 0037](adr/0037-missed-ticks-fire-once.md)) |
| A crashed worker loses no work | SQS visibility timeout redelivers unacknowledged messages; jobs must be safe to re-run |
| Stopping a worker wastes no work | On SIGTERM a worker starts no new receive, lets the one in progress finish and hands back what it returns, drains running jobs up to a deadline above the job timeout, then flushes the outbox; the grace period (compose, Kubernetes) is set above drain plus flush ([ADR 0023](adr/0023-graceful-shutdown.md)) |
| A received job starts at once | Each worker runs up to `GUILDHALL_WORKER_CONCURRENCY` jobs and receives only as many messages as it has free slots, so no message waits locally while its visibility timeout runs ([ADR 0021](adr/0021-worker-concurrency.md)) |
| A follow-up job is never lost | Follow-up jobs go to `outbox_jobs` in the same transaction as the job's writes; a relay in each worker sends them to SQS, locking batches with `FOR UPDATE SKIP LOCKED` so replicas take different rows; a failed relay pass is logged and retried, never ending the worker ([ADR 0016](adr/0016-worker-job-lifecycle.md), [ADR 0019](adr/0019-outbox-relay.md), [ADR 0022](adr/0022-worker-loops.md)) |
| Bad jobs don't block good ones | After 5 receives, a message moves to the dead-letter queue; permanent failures, invalid payloads, contract violations and disabled plug-ins go there at once with a reason; redrive after a fix |
| Upstream limits are respected across all replicas | Two moving windows per plug-in (per minute, per day) in Redis, through the `limits` library; a `429` sets a cooldown every replica sees ([ADR 0034](adr/0034-shared-rate-limits-in-redis.md)) |
| Throttling never fails a job | A job that can't call upstream soon is re-sent later as a new message with the same attempt number, so it is never dead-lettered for it ([ADR 0033](adr/0033-throttled-jobs-are-deferred.md)) |
| One event, one Discord message (almost) | Events go to `outbox_events` in the same transaction as the data change; the dispatcher posts and records `discord_message_id` |
| Discord retries don't double-act | `interactions_seen` with a unique constraint on the interaction ID |

### Two deliberate edge cases

- **Discord accepted the message but the dispatcher crashed before marking it sent.** The retry posts a duplicate. Avoiding it would need a delivery check against Discord on every retry; a rare duplicate message is the cheaper trade.
- **Redis is down.** The outbound limiter fails closed: jobs that call upstream APIs are deferred every 30–60 s until it returns, rather than risk exceeding someone else's limits. Other jobs, and commands that read only from Postgres, keep working ([ADR 0033](adr/0033-throttled-jobs-are-deferred.md)).

### Observability

- Structured JSON logs with `plugin`, `job_type`, `dedupe_key` and `attempt` on every line written while a job runs, plug-in and library lines included. structlog and standard `logging` share one processor chain; the job fields travel in context variables, so concurrent jobs never mix them up ([ADR 0020](adr/0020-structured-logging.md)).
- Metrics: jobs processed, failed and dead-lettered per plug-in; queue depth; upstream calls and `429`s per plug-in; outbox lag.
- `/guildhall status` in Discord surfaces the important ones without opening a dashboard.

## Infrastructure and deployment

One container image, three run targets. Everyday use runs locally; AWS runs the same image for cloud sessions.

The image is built in two stages: uv installs the locked runtime dependencies and the project into a virtual environment, and only that environment is copied onto `python:3.14-slim-trixie`. It runs as an unprivileged numeric user, and starts its command directly as PID 1 so SIGTERM reaches it ([ADR 0024](adr/0024-container-image.md)).

| Environment | Compute | Queue and storage | Discord reaches it via |
| --- | --- | --- | --- |
| Dev | docker-compose | moto (SQS, S3), Postgres and Redis containers | Tunnel |
| Local cluster | kind with Kustomize `overlays/local` | Same, in-cluster | Tunnel |
| AWS | EKS with `overlays/aws` | SQS, S3, RDS Postgres; Redis in-cluster | ALB with HTTPS (own domain, ACM certificate) |

Locally, Docker Engine runs the services from `compose.yaml`: Postgres 18, Redis 8, and moto as an open-source emulator for SQS and S3. Under the `app` profile, compose also runs the bot's image: one-shot `migrate` and `aws-setup` services prepare the schema, the queues and the archive bucket, then the worker starts ([ADR 0026](adr/0026-app-services-in-compose.md), [ADR 0032](adr/0032-archive-keys-bucket-and-aws-settings.md)). LocalStack was the first choice but now requires an account token, which CI and Dependabot pull requests cannot use ([ADR 0012](adr/0012-local-infrastructure.md)).

### AWS resources (Terraform)

- VPC with one NAT gateway, EKS with two small nodes, ECR repository
- RDS Postgres, smallest instance, no Multi-AZ
- SQS job queue plus dead-letter queue; S3 bucket for raw payloads
- IAM: pods get SQS and S3 access through EKS Pod Identity, no access keys in pods
- Terraform state in S3; an AWS Budgets alert

Redis stays in-cluster rather than ElastiCache: it only holds rate-limit buckets, which can be rebuilt, and it saves a managed service.

### Secrets

Discord public key and bot token, Steam API key, IsThereAnyDeal key. Configuration and secrets are environment variables prefixed `GUILDHALL_`, loaded with pydantic-settings and documented in `.env.example` ([ADR 0011](adr/0011-settings-and-plugin-catalogue.md)). Locally they come from a `.env` file that is never committed; in the cluster as a Kubernetes Secret created by `make up`. Moving them to AWS Secrets Manager is a later improvement.

### CI/CD (GitHub Actions)

- Every PR: the pre-commit hooks (lint, format, type check, a check that production code imports only runtime dependencies ([ADR 0025](adr/0025-dependency-check-with-deptry.md)), unit tests, contract tests against the mock upstream), integration tests against the compose services, end-to-end tests against the containerised stack ([ADR 0027](adr/0027-end-to-end-tests.md)), and a gitleaks scan of the full history; actions are pinned to commit SHAs ([ADR 0006](adr/0006-ci-workflow.md)).
- Dependabot: security fix PRs as advisories appear, and monthly grouped version updates for Python packages and actions after a 7-day cooldown ([ADR 0007](adr/0007-dependabot.md)).
- Merge to `main`: build the image, push to ECR tagged with the git SHA. AWS access through GitHub OIDC; no AWS keys stored in GitHub.
- Deploy: manually triggered workflow that sets the image tag and applies the AWS overlay.

### Cost guardrails

- EKS bills hourly even when idle (control plane $0.10 per cluster-hour on standard support; $0.60 once the Kubernetes version leaves standard support). Create and destroy per session with `make up` / `make down`, and pin a current version.
- Nodes, NAT gateway, load balancer and RDS also bill hourly; SQS and S3 are pay-per-use and negligible here.
- Destroy order: delete the Ingress first so the load balancer goes, then `terraform destroy`, then check the console. Keep the S3 bucket and domain.
- After an AWS session, point Discord's interactions URL back at the tunnel.

## Testing and failure drills

### Test layers

- **Unit:** metric functions, alert rules, state transitions, signature verification.
- **Integration:** code that depends on Postgres behaviour (constraints, transactions, locking) runs against the compose services, in a separate `guildhall_test` database, marked `integration`. Pre-commit skips them; CI runs them in their own job ([ADR 0015](adr/0015-integration-tests-on-compose.md)).
- **End-to-end:** the containerised stack, run as a separate throwaway compose project, driven from outside through the queue and Postgres; `make test-e2e` ([ADR 0027](adr/0027-end-to-end-tests.md)). The first test is M1's acceptance scenario.
- **Contract tests:** a shared suite every plug-in must pass, run against a fake platform: declarations are valid, every job type has examples, payloads survive a JSON round trip, outcomes are namespaced, errors are typed, and (from M2) `render` does no I/O. A guard test fails if a plug-in has no examples. Each example brings the services its handler gets, from shared fakes in `tests/support/fakes.py` that are type-checked against the contract's protocols ([ADR 0049](adr/0049-shared-test-fakes.md)).
- **Recorded fixtures:** real upstream responses (a parsed match, an unparsed match, a wishlist, a price batch) saved once and replayed, so tests never call live APIs. A script run by hand records them as plain response bodies ([ADR 0039](adr/0039-recorded-fixtures-are-response-bodies-from-a-script.md)); Gank Report's are the owner's own latest match, before and after its parse ([ADR 0040](adr/0040-fixtures-record-the-owners-own-match.md)). Each is trimmed to an allowlist of the fields the plug-in uses, and every match and account ID is replaced with a fake ([ADR 0041](adr/0041-fixtures-are-trimmed-to-an-allowlist-with-fake-ids.md)). A structural test fails if a fixture holds any other field or a real-looking ID ([ADR 0042](adr/0042-a-structural-test-checks-fixtures.md)).
- **Mock upstream server:** a small service in the cluster that can return `429`s, slow responses, never-parsed matches and malformed payloads on demand. All drills point at it.
- **Synthetic plug-in:** a plug-in whose job payload says what to do (sleep, wait, fail, crash, fan out, emit events). Disabled by default; enabled for end-to-end tests, drills and the load check.

### Failure drills

Run on the kind cluster. For each, record what was expected, what happened, and what changed.

| Drill | How to run it | Expected result |
| --- | --- | --- |
| Backfill burst | Link an account with 200 past matches | Outbound calls stay under 60 a minute across all worker replicas |
| Upstream 429 storm | Mock returns `429` for 2 minutes | Jobs back off and resume; none dead-lettered |
| Parse never finishes | Mock match stays unparsed | Basic metrics after 3 attempts; parsed-only metrics marked missing |
| Worker crash mid-job | Kill a worker pod holding jobs | Jobs redelivered after visibility timeout; no duplicate posts |
| Poison message | Enqueue a malformed job | Dead-lettered after 5 receives; redrive works after the fix |
| Same deal seen twice | Two price checks see one sale | Exactly one alert |
| Discord retry | Replay an interaction with the same ID | Handled once |
| Forged request | Interaction with a bad signature | `401`, nothing processed |
| Slow command | Make `/trend` take 5 seconds | Deferred reply arrives; no Discord timeout |
| Broken plug-in | Every Side Quest job throws | Gank Report unaffected; Side Quest jobs dead-lettered |
| Redis down | Scale Redis to 0 | Upstream-calling jobs pause; read-only commands still answer |
| Postgres down | Scale Postgres to 0 for 60 seconds | Readiness probes pull pods; recovery without manual steps |

### Load check

Enqueue 1,000 synthetic plug-in jobs on kind and record throughput per worker replica, while confirming no plug-in exceeds its upstream budget.

## Milestones

Milestones are defined by what works, not by dates. Each ends with a usable system.

### M0: Setup

- [x] Steam profile and game details set to public; Steam Web API key
- [x] IsThereAnyDeal API key
- [x] Discord application and a private test server
- [x] Repo with the layout above, pre-commit hooks, CI skeleton

### M1: Platform skeleton

- [x] Plug-in contract, registry and contract test suite
- [x] Worker loop with dedupe, typed errors and dead-letter handling
- [x] Container image, and docker-compose with worker, Postgres, Redis, moto

**Done when:** a fake plug-in's jobs flow end to end and a duplicate delivery is a no-op.

### M2: Gank Report, local

- [ ] `discover`, `fetch_match`, parse-retry, S3 archive, metric registry
- [x] Per-plug-in rate limiter with backoff
- [ ] Discord endpoint, `/link`, `/match last`, outbox dispatcher
- [ ] api service in docker-compose

**Done when:** I play a match and the report shows up in Discord on its own.

### M3: Kubernetes and AWS

- [ ] kind manifests with probes and limits
- [ ] Terraform for the AWS resources; `make up` / `make down`
- [ ] Discord pointed at the cluster over HTTPS

**Done when:** Gank Report runs end to end on EKS, and a destroy leaves nothing billing.

### M4: Delivery pipeline and Gank Report complete

- [ ] GitHub Actions build, push and manual deploy with OIDC
- [ ] `/trend`, `/focus`, weekly report, 100-match backfill

**Done when:** a merge ships a new image and the Sunday report posts.

### M5: Side Quest

- [ ] Library and wishlist sync, backlog states
- [ ] Price checks, alert rules with dedupe keys
- [ ] `/backlog`, `/next`, `/status`, `/watch`

**Done when:** Side Quest ships with no platform changes, or each change it needed is recorded with its reason.

### M6: Hardening

- [ ] All failure drills run and recorded
- [ ] Load check
- [ ] README with quickstart and architecture
- [ ] Rune Ledger fit check against the contract

### Later

- Rune Ledger
- Autoscale workers on queue depth (KEDA)
- Friends linking accounts, scoped per Discord server
- Secrets in AWS Secrets Manager; CloudWatch dashboards and alarms

## Decisions and open questions

Each decision gets a short record in `docs/adr/` (context, options, choice, consequences) when it is made. Small design calls count too; they're the ones most easily forgotten. Records are numbered in the order they are written; see [ADR 0001](adr/0001-record-architecture-decisions.md).

### Decisions to record

Pending decisions have no number until their record is written; the ADR column then links to it.

| Decision | Current leaning | ADR |
| --- | --- | --- |
| Plug-in contract shape and what the platform owns | ABC with declared job types, own package | [0008](adr/0008-plugin-contract-shape.md) |
| HTTP interactions endpoint vs. gateway bot | HTTP endpoint | Pending |
| Job dedupe in Postgres vs. SQS FIFO deduplication | Postgres `processed_jobs` (works with standard queues and outlives FIFO's dedupe window) | [0014](adr/0014-job-dedupe-in-postgres.md) |
| Side Quest alert dedupe key | Game, shop, price, deal expiry | Pending |
| Outbound limiter fails closed when Redis is down | Fail closed | [0034](adr/0034-shared-rate-limits-in-redis.md) |
| Redis in-cluster vs. ElastiCache | In-cluster | Pending |
| Accept rare duplicate Discord posts on dispatcher crash | Accept | Pending |
| Metrics stored long format vs. one column per metric | Long format | Pending |

### Open questions

- [ ] Which 3–4 Gank Report metrics matter most for how I actually play? Try for two weeks, then prune.
- [ ] Is the rolling window of 20 games per hero right, given how often I repeat heroes?
- [ ] Does `GetWishlist` return enough to skip a separate store lookup? Check against a recorded response.
- [ ] Where do time-to-beat estimates for `/next [hours]` come from, if anywhere?
- [ ] Should weekly reports be skipped in weeks with fewer than 3 matches?

## References

Rate limits and prices change; confirm against the official pages before relying on a number.

- [OpenDota API overview and rate limits](https://jentic.com/apis/opendota.com/opendota)
- [OpenDota parse requests](https://glama.ai/mcp/servers/hkaanengin/opendota-mcp-server/tools/request_parse_match)
- [Steam Web API (Valve Developer Community)](https://developer.valvesoftware.com/wiki/Steam_Web_API)
- [Steam GetOwnedGames and GetWishlist usage notes](https://github.com/mdeguzis/proton-pulse-web/wiki/Steam-Library-and-Drafts)
- [IsThereAnyDeal API documentation](https://docs.isthereanydeal.com/)
- [Discord HTTP interactions: signatures and the 3-second rule](https://www.codehooks.io/docs/examples/webhooks/discord)
- [HTTP interactions vs. gateway tradeoffs](https://playcode.io/blog/how-to-create-a-discord-bot)
- [Elden Ring Fan API](https://docs-eldenring-api-git-fork-julesferreira-fix-sc-773c82-deliton.vercel.app/docs)
- [Amazon EKS pricing summary](https://geekchamp.com/?p=1072184)
