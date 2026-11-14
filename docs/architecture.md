# Guildhall in diagrams

A visual tour of how Guildhall fits together, for anyone getting to know the
code. The [design spec](design-spec.md) is the source of truth and the
[decision records](adr/) say why; this page shows the shape of things and
links to both. Where the two disagree, the spec wins and this page is a bug.

Read it top to bottom: from the outside world, to where things run, to the
code, to how one piece of work flows through it.

1. [System context](#1-system-context)
2. [Where it runs](#2-where-it-runs)
3. [Inside a worker](#3-inside-a-worker)
4. [Code structure](#4-code-structure)
5. [Data model](#5-data-model)
6. [Workflows](#6-workflows)
7. [Lifecycles](#7-lifecycles)

**Legend**, used throughout:

```mermaid
flowchart LR
    built["Built and running"]
    planned["Planned, not built yet"]
    plugin["Plug-in code (game-specific)"]
    store[("Data store")]
    queue[["Queue"]]
    built ~~~ planned ~~~ plugin ~~~ store ~~~ queue

    classDef plannedStyle stroke-dasharray: 5 5
    classDef pluginStyle fill:#e7f1fb,stroke:#2f6fdb,stroke-width:2px,color:#0b2a5b
    class planned plannedStyle
    class plugin pluginStyle
```

## 1. System context

Guildhall sits between a Discord server and the public game APIs. People only
ever talk to Discord; Guildhall answers their slash commands and posts reports
on its own.

```mermaid
flowchart LR
    people(["👤 Me and friends<br/>in a private Discord server"])
    discord["Discord"]
    guildhall["🏰 Guildhall"]
    opendota["OpenDota API<br/>Dota 2 matches"]
    steam["Steam Web API<br/>library, wishlist"]
    itad["IsThereAnyDeal API<br/>prices"]

    people -- "slash commands" --> discord
    discord -- "messages" --> people
    discord -- "signed HTTPS interactions" --> guildhall
    guildhall -- "replies, match reports, alerts" --> discord
    guildhall -- "rate-limited calls" --> opendota
    guildhall -. "rate-limited calls" .-> steam
    guildhall -. "rate-limited calls" .-> itad

    classDef plannedStyle stroke-dasharray: 5 5
    class steam,itad plannedStyle
```

Steam and IsThereAnyDeal arrive with Side Quest (M5).

## 2. Where it runs

One container image runs everywhere. It has three entry points:
`guildhall-api` (the Discord endpoint), `guildhall-worker` (everything else)
and `guildhall-register-commands` (run by hand). Only the infrastructure
around it changes ([ADR 0024](adr/0024-container-image.md)).

| Piece | Local today (docker compose) | Local cluster (kind, M3) | AWS (EKS, M3) |
| --- | --- | --- | --- |
| api, worker | Containers | Deployments | Deployments |
| Job queue + dead-letter queue | moto (SQS emulator) | moto, in-cluster | SQS |
| Raw archive | moto (S3 emulator) | moto, in-cluster | S3 |
| Postgres | Container | In-cluster | RDS |
| Redis | Container | In-cluster | In-cluster |
| Schema and queue setup | One-shot `migrate` and `aws-setup` containers | Jobs | `migrate` Job; queues and bucket from Terraform |
| How Discord reaches the api | ngrok tunnel | ngrok tunnel | Load balancer with HTTPS |
| Secrets | `.env` (git-ignored) | Kubernetes Secret | Kubernetes Secret |

The kind and AWS columns are the current plan; the details are decided in M3
and recorded as they are.

### Local, today: docker compose

Everything runs on one machine. The `app` profile adds the bot's own
containers to the always-on infrastructure, and the `tunnel` profile adds
ngrok so Discord can reach the api
([ADR 0026](adr/0026-app-services-in-compose.md),
[ADR 0060](adr/0060-ngrok-tunnel.md)).

```mermaid
flowchart TB
    discordin["Discord<br/>sends interactions"]
    edge["ngrok edge<br/>static dev domain"]

    subgraph host["Your machine"]
        ngrok["ngrok agent<br/>tunnel profile"]
        api["api<br/>port 8000"]
        worker["worker"]
        migrate["migrate<br/>one-shot"]
        setup["aws-setup<br/>one-shot"]
        postgres[("Postgres 18")]
        redis[("Redis 8")]
        moto[["moto<br/>SQS + S3"]]
    end

    discordapi["Discord API<br/>replies, posts"]
    opendota["OpenDota<br/>match data"]

    discordin --> edge --> ngrok --> api
    ngrok ~~~ worker
    api --> postgres
    worker --> postgres
    worker -- "rate limits" --> redis
    worker -- "jobs, archive" --> moto
    worker --> discordapi
    worker --> opendota
    migrate -. "schema" .-> postgres
    setup -. "queues, bucket" .-> moto
```

The ngrok agent dials out to ngrok's edge, so nothing on your machine is
opened to the internet; Discord's requests come back down that connection.
Posts, replies and OpenDota calls are outbound calls from the worker and need
no tunnel.
### On AWS (planned, M3)

The same image on EKS, with managed AWS services in place of the emulators.
Everything that bills by the hour is created and destroyed per session with
`make up` / `make down`
([spec: infrastructure](design-spec.md#infrastructure-and-deployment)).

```mermaid
flowchart TB
    discordin["Discord<br/>sends interactions"]
    github["GitHub Actions<br/>build and deploy, M4"]

    subgraph aws["AWS"]
        alb["Load balancer<br/>HTTPS"]
        ecr["ECR<br/>images"]
        subgraph eks["EKS cluster"]
            api["api pods"]
            worker["worker pods"]
            redis[("Redis")]
        end
        rds[("RDS Postgres")]
        sqs[["SQS + DLQ"]]
        s3[("S3 archive")]
        nat["NAT gateway"]
    end

    discordapi["Discord API<br/>replies, posts"]
    opendota["OpenDota<br/>match data"]

    discordin --> alb --> api
    github -. "push (OIDC)" .-> ecr
    ecr -. "pull" .-> api & worker
    alb ~~~ worker
    api --> rds
    worker --> rds
    worker -- "rate limits" --> redis
    worker -- "jobs" --> sqs
    worker -- "archive" --> s3
    worker --> nat
    nat --> discordapi
    nat --> opendota

    classDef plannedStyle stroke-dasharray: 5 5
    class alb,nat,api,worker,redis,rds,sqs,s3,ecr,github plannedStyle
```

Everything sits in one VPC. Pods reach SQS and S3 through EKS Pod Identity,
so no AWS keys live in the cluster, and they reach the internet through the
NAT gateway. Kept between sessions: the S3 bucket, the Terraform state
bucket, the domain and an AWS Budgets alert, none of which bill by the hour.

## 3. Inside a worker

The worker is one Python process running several loops on one asyncio event
loop. The key idea is the **transactional outbox**: nothing talks to SQS or
Discord in the middle of a database transaction. Work that must happen later
is written to an `outbox_*` table in the same transaction as the change that
caused it, and a separate loop sends it afterwards. So a crash can delay a
message but never lose it or send it for a change that was rolled back
([ADR 0016](adr/0016-worker-job-lifecycle.md),
[ADR 0019](adr/0019-outbox-relay.md),
[ADR 0022](adr/0022-worker-loops.md)).

The two halves of a worker meet only in Postgres.

**Running a job.** A message comes in from SQS; the runner hands it to the
plug-in's handler and commits what the handler asked for.

```mermaid
flowchart LR
    sqs[["SQS job queue"]]
    consumer["JobConsumer<br/>receives as many messages<br/>as it has free slots"]
    runner["JobRunner"]
    handler["Plug-in handler"]
    upstream["Upstream API"]
    redis[("Redis<br/>rate-limit budget")]
    s3[("S3 archive")]
    pg[("Postgres<br/>processed_jobs, plug-in tables,<br/>outbox_jobs, outbox_events,<br/>outbox_replies")]
    dlq[["Dead-letter queue"]]

    sqs --> consumer --> runner
    runner -- "Job, Context" --> handler
    handler -- "Outcome" --> runner
    handler -- "ctx.http" --> upstream
    handler -. "takes from" .-> redis
    runner -- "1: upload archives" --> s3
    runner -- "2: one transaction" --> pg
    runner -- "bad jobs" --> dlq

    classDef pluginStyle fill:#e7f1fb,stroke:#2f6fdb,stroke-width:2px,color:#0b2a5b
    class handler pluginStyle
```

**Getting things out.** Separate loops read the outbox tables and deliver
what's there, then mark it sent.

```mermaid
flowchart LR
    scheduler["Scheduler loop<br/>claims due cron ticks"]

    subgraph pg["Postgres"]
        outboxjobs[("outbox_jobs")]
        outboxreplies[("outbox_replies")]
        outboxevents[("outbox_events")]
    end

    relay["OutboxRelay loop"]
    replies["ReplyDispatcher loop"]
    events["EventDispatcher loop"]
    sqs[["SQS job queue"]]
    discord["Discord"]

    scheduler -- "queue jobs" --> outboxjobs
    outboxjobs --> relay -- "send" --> sqs
    outboxreplies --> replies -- "edit the deferred reply" --> discord
    outboxevents --> events -- "render and post to the channel" --> discord
```

A prune loop also deletes settled outbox rows and old interaction IDs.

| Loop | What it does | Records |
| --- | --- | --- |
| Consumer + runner | Receive jobs, run the plug-in's handler, commit its outcome | [0018](adr/0018-job-runner.md), [0021](adr/0021-worker-concurrency.md) |
| Scheduler | Each interval, claim due cron ticks and queue their jobs | [0036](adr/0036-scheduling-in-workers-with-a-postgres-claim.md), [0037](adr/0037-missed-ticks-fire-once.md) |
| Relay | Send `outbox_jobs` rows to SQS | [0019](adr/0019-outbox-relay.md) |
| Reply dispatcher | Edit command replies into Discord | [0064](adr/0064-replies-through-an-outbox.md) |
| Event dispatcher | Render events and post them to the channel | [0072](adr/0072-posting-events-to-a-channel.md) |
| Prune | Delete sent outbox rows and old interaction IDs | [0061](adr/0061-interactions-endpoint-flow.md) |

Every loop that reads an outbox locks its batch with `FOR UPDATE SKIP
LOCKED`, so any number of worker replicas can run side by side without
handling the same row twice.

## 4. Code structure

### Packages

Dependencies point one way. Plug-ins see only the contract; the platform
implements it; `bootstrap` is the one place that wires concrete pieces
together ([ADR 0008](adr/0008-plugin-contract-shape.md)).

```mermaid
flowchart TB
    bootstrap["guildhall.bootstrap<br/>entry points: worker, api, register-commands"]
    api["guildhall.api<br/>FastAPI app, signature check"]
    platform["guildhall.platform<br/>worker, runner, outbox, scheduler,<br/>dispatchers, limiter, registry"]
    plugins["guildhall.plugins<br/>CATALOGUE: gank_report, synthetic"]
    contract["guildhall.contract<br/>SourcePlugin, Job, Outcome, Command, Message"]

    bootstrap --> api
    bootstrap --> platform
    bootstrap --> plugins
    api --> platform
    platform --> contract
    plugins --> contract

    classDef pluginStyle fill:#e7f1fb,stroke:#2f6fdb,stroke-width:2px,color:#0b2a5b
    class plugins pluginStyle
```

### The plug-in contract: jobs

A plug-in is a `SourcePlugin`. It declares job types, each a payload model
plus a handler. A handler gets a `Job` and a `Context` (its only doors to the
outside) and returns an `Outcome` that describes what should happen; the
platform makes it happen ([spec: plug-in contract](design-spec.md#plug-in-contract)).

```mermaid
classDiagram
    direction LR
    class SourcePlugin {
        <<abstract>>
        +contract_version int
        +name str*
        +rate_limit RateLimitPolicy*
        +tables MetaData
        +job_types() list~JobType~*
        +schedules() list~Schedule~
        +commands() list~Command~
        +account_link() AccountLink
        +event_types() list~Event~
        +render(event) Message
    }
    class JobType~P~ {
        +name str
        +payload_model type~P~
        +handler(job, ctx) Outcome
    }
    class Schedule {
        +job_type str
        +cron str
        +per_account bool
    }
    class RateLimitPolicy {
        +per_minute int
        +per_day int
    }
    class Job~P~ {
        +type str
        +payload P
        +dedupe_key str
        +attempt int
    }
    class Context {
        +db Database
        +archive RawArchive
        +http Http
    }
    class Outcome {
        +writes tuple~UpdateBase~
        +archives tuple~Archive~
        +events tuple~Event~
        +jobs tuple~JobSpec~
        +retry_in timedelta
    }
    class JobSpec {
        +type str
        +payload BaseModel
        +dedupe_key str
        +delay timedelta
    }
    class Event {
        +type str
        +schema_version int
        +dedupe_key str
    }
    class Archive {
        +kind str
        +id str
        +body bytes
    }
    class Database {
        <<protocol>>
        +fetch(select) rows
    }
    class RawArchive {
        <<protocol>>
        +get(kind, id) bytes
    }
    class Http {
        <<protocol>>
        +get(url, params) HttpResponse
        +post(url, params, json) HttpResponse
    }

    SourcePlugin *-- "1..*" JobType
    SourcePlugin *-- "0..*" Schedule
    SourcePlugin *-- RateLimitPolicy
    JobType ..> Job : handles
    JobType ..> Context : uses
    JobType ..> Outcome : returns
    Context *-- Database
    Context *-- RawArchive
    Context *-- Http
    Outcome *-- "0..*" JobSpec
    Outcome *-- "0..*" Event
    Outcome *-- "0..*" Archive
```

Members ending in `*` are abstract. `account_link()` and `render()` may
return `None`, and `retry_in` is set only when the work isn't ready yet, in
which case the outcome carries nothing else
([ADR 0028](adr/0028-plugin-writes-declared-in-the-outcome.md),
[ADR 0031](adr/0031-raw-archive-declared-in-the-outcome.md)).

### The plug-in contract: commands, links and messages

Slash commands and account links are declared the same way: a model for the
input and a handler. Replies and posts share one `Message` type
([ADR 0062](adr/0062-commands-in-the-contract.md),
[ADR 0066](adr/0066-the-platform-runs-link.md),
[ADR 0068](adr/0068-messages-with-embeds-and-mentions.md)).

```mermaid
classDiagram
    direction LR
    class Command~O~ {
        +path tuple~str~
        +description str
        +options type~O~
        +private bool
        +handler(invocation, ctx) CommandOutcome
    }
    class Invocation~O~ {
        +discord_user_id str
        +account LinkedAccount
        +options O
    }
    class CommandOutcome {
        +reply Message
        +writes tuple~UpdateBase~
        +jobs tuple~JobSpec~
        +events tuple~Event~
    }
    class AccountLink {
        +kind str
        +description str
        +on_link str
        +verify(request, ctx) Accepted or Rejected
    }
    class LinkRequest {
        +discord_user_id str
        +external_id str
        +previous LinkedAccount
    }
    class Accepted {
        +external_id str
    }
    class Rejected {
        +reason str
    }
    class LinkedAccount {
        +id int
        +discord_user_id str
        +external_id str
    }
    class Message {
        +content str
        +embeds tuple~Embed~
        +mentions tuple~str~
    }
    class Embed {
        +author EmbedAuthor
        +title str
        +description str
        +colour int
        +fields tuple~EmbedField~
        +footer str
        +timestamp datetime
    }
    class EmbedAuthor {
        +name str
        +icon_url str
    }
    class EmbedField {
        +name str
        +value str
        +inline bool
    }

    Command ..> Invocation : receives
    Command ..> CommandOutcome : returns
    Invocation --> LinkedAccount
    CommandOutcome *-- Message
    AccountLink ..> LinkRequest : receives
    AccountLink ..> Accepted : returns
    AccountLink ..> Rejected : returns
    LinkRequest --> LinkedAccount : previous
    Message *-- "0..10" Embed
    Embed *-- EmbedAuthor
    Embed *-- "0..25" EmbedField
```

### The platform's main classes

How `bootstrap.run_worker` assembles a worker. Each class depends on small
protocols rather than on each other's concrete types, which is what lets the
unit tests swap in fakes.

```mermaid
classDiagram
    direction TB
    class Worker {
        +run(stop)
    }
    class JobConsumer {
        +consume(stop)
    }
    class JobRunner {
        +run(message) RunResult
    }
    class OutboxRelay {
        +relay_once() int
        +prune() int
    }
    class Scheduler {
        +fire_due(now) int
    }
    class ReplyDispatcher {
        +dispatch_once() int
    }
    class EventDispatcher {
        +dispatch_once() int
    }
    class JobQueue {
        +send(body, delay)
        +receive(count, wait)
        +delete(message)
        +retry_later(message, delay)
        +dead_letter(message, reason)
    }
    class S3ArchiveStore {
        +put(key, body)
        +get(key) bytes
    }
    class UpstreamHttp {
        +for_plugin(plugin) PluginHttp
    }
    class PluginHttp {
        +get(url, params) HttpResponse
        +post(url, params, json) HttpResponse
    }
    class RedisRateLimiter {
        +acquire(plugin, policy) float
        +cool_down(plugin, seconds)
    }
    class PluginDatabase {
        +fetch(select) rows
    }
    class PluginArchive {
        +get(kind, id) bytes
    }

    Worker *-- JobConsumer
    Worker *-- OutboxRelay
    Worker *-- Scheduler
    Worker *-- "0..*" ReplyDispatcher
    Worker *-- "0..*" EventDispatcher
    JobConsumer --> JobRunner
    JobRunner --> JobQueue
    JobRunner --> S3ArchiveStore
    JobRunner --> UpstreamHttp
    OutboxRelay --> JobQueue
    UpstreamHttp ..> PluginHttp : creates per plug-in
    PluginHttp --> RedisRateLimiter
    JobRunner ..> PluginDatabase : builds per job
    JobRunner ..> PluginArchive : builds per job
    PluginArchive --> S3ArchiveStore
```

`PluginDatabase`, `PluginArchive` and `PluginHttp` are what a handler's
`Context` holds. Each is scoped to one plug-in: the database refuses tables
the plug-in doesn't own, the archive adds its `raw/<plug-in>/` prefix, and
the HTTP client takes from its own rate-limit budget
([ADR 0030](adr/0030-plugin-reads-through-a-checked-fetch.md),
[ADR 0034](adr/0034-shared-rate-limits-in-redis.md)).

### Gank Report

The first real plug-in. Its job types form the match pipeline; metrics are a
registry of small functions, so adding one needs no schema change
([ADR 0043](adr/0043-fetch-per-match-analyse-per-account.md),
[ADR 0053](adr/0053-metric-registry-and-missing-values.md)).

```mermaid
classDiagram
    direction LR
    class SourcePlugin {
        <<abstract>>
    }
    class GankReportPlugin {
        +name = "gank_report"
        +rate_limit 50 per min, 2000 per day
        +poll_matches(Job~ScheduledRun~) Outcome
        +backfill(Job~LinkedAccount~) Outcome
        +fetch_match(Job~FetchMatch~) Outcome
        +analyze_match(Job~AnalyzeMatch~) Outcome
        +match_last(Invocation) CommandOutcome
        +verify_account(LinkRequest) Accepted or Rejected
        +render(MatchAnalyzed) Message
    }
    class FetchMatch {
        +match_id int
    }
    class AnalyzeMatch {
        +match_id int
        +account LinkedAccount
    }
    class MatchAnalyzed {
        +type = "gank_report.match_analyzed"
        +match_id int
        +discord_user_id str
        +hero_id int
        +won bool
        +mode str
        +parsed bool
        +played_at datetime
        +duration_seconds int
        +metrics tuple~MetricResult~
    }
    class MetricResult {
        +name str
        +value float
        +baseline float
        +baseline_games int
    }
    class Metric {
        +name str
        +label str
        +higher_is_better bool
        +needs_parse bool
        +compute(match, player) float
    }

    SourcePlugin <|-- GankReportPlugin
    GankReportPlugin ..> FetchMatch
    GankReportPlugin ..> AnalyzeMatch
    GankReportPlugin ..> MatchAnalyzed : emits and renders
    GankReportPlugin --> "5" Metric : METRICS registry
    MatchAnalyzed *-- "1..*" MetricResult
```

## 5. Data model

Platform tables are shared; each plug-in owns tables prefixed with its name
and can't touch anyone else's. In the diagrams, solid lines are foreign keys
and dotted lines match by value, with no foreign key
([spec: data model](design-spec.md#data-model)).

**Platform tables.** None of them join to another: each is a log or a queue,
keyed by its own ID or dedupe key. The first group records accounts and what
has already happened; the second holds work waiting to leave.

```mermaid
erDiagram
    linked_accounts {
        bigint id PK
        text plugin UK "unique with discord_user_id"
        text discord_user_id UK
        text external_id "the account in the game or store"
        timestamptz linked_at
    }
    schedule_runs {
        text plugin PK
        text job_type PK
        timestamptz fired_for "last tick claimed"
    }
    processed_jobs {
        text dedupe_key PK
        timestamptz completed_at
    }
    interactions_seen {
        text interaction_id PK
        timestamptz received_at
    }
```

```mermaid
erDiagram
    outbox_jobs {
        bigint id PK
        text dedupe_key
        jsonb body "job envelope"
        int delay_seconds
        timestamptz created_at
        timestamptz sent_at "null while pending"
    }
    outbox_events {
        bigint id PK
        text plugin
        text dedupe_key UK
        text type
        int schema_version
        jsonb payload
        timestamptz created_at
        int attempts
        timestamptz sent_at
        timestamptz abandoned_at
        text discord_message_id
    }
    outbox_replies {
        bigint id PK
        text application_id
        text interaction_token "15-minute credential"
        jsonb message
        timestamptz created_at
        int attempts
        timestamptz sent_at
        timestamptz abandoned_at
    }
```

**Gank Report's tables.** One row per match, one per linked player in it, and
one per metric for that player, in long format so a new metric needs no
migration ([ADR 0045](adr/0045-gank-report-tables.md),
[ADR 0054](adr/0054-metrics-in-long-format.md)).

```mermaid
erDiagram
    linked_accounts {
        bigint id PK
        text plugin "gank_report"
        text external_id "Dota account ID"
    }
    gank_report_polled_matches {
        bigint account_id PK
        bigint match_id PK
        timestamptz polled_at
    }
    gank_report_matches {
        bigint match_id PK
        timestamptz played_at
        int duration_seconds
        int game_mode
        int lobby_type
        text mode "normal or turbo"
        boolean parsed
        timestamptz fetched_at
    }
    gank_report_player_matches {
        bigint match_id PK, FK
        bigint account_id PK
        int hero_id
        int player_slot
        boolean won
        timestamptz analyzed_at
    }
    gank_report_metrics {
        bigint match_id PK, FK
        bigint account_id PK, FK
        text metric PK
        double value "null: no value"
    }

    linked_accounts ||..o{ gank_report_polled_matches : "external_id = account_id"
    gank_report_polled_matches }o..|| gank_report_matches : "match_id"
    gank_report_matches ||--o{ gank_report_player_matches : "one per linked player"
    gank_report_player_matches ||--o{ gank_report_metrics : "one per metric"
```

Planned tables (Gank Report's focus list; Side Quest's games, wishlist and
prices) are in the spec.

## 6. Workflows

Grey boxes in these diagrams are **database transactions**: everything inside
one commits together or not at all.

### A slash command, end to end

Discord gives a bot 3 seconds to answer. So the api answers "thinking..."
straight away and turns the command into a job; the worker runs the handler
and edits the real reply in afterwards
([ADR 0061](adr/0061-interactions-endpoint-flow.md),
[ADR 0063](adr/0063-commands-run-as-jobs.md),
[ADR 0064](adr/0064-replies-through-an-outbox.md)).

```mermaid
sequenceDiagram
    autonumber
    actor user as User
    participant discord as Discord
    participant api
    participant pg as Postgres
    participant relay as Outbox relay
    participant sqs as SQS
    participant runner as Job runner
    participant plugin as Plug-in handler
    participant replies as Reply dispatcher

    user->>discord: /match last
    discord->>api: POST /interactions (signed)
    api->>api: verify Ed25519 signature, else 401
    rect rgba(128, 128, 128, 0.15)
        api->>pg: insert interactions_seen (a retry stops here)
        api->>pg: insert outbox_jobs: command job
    end
    api-->>discord: deferred: "thinking..."
    discord-->>user: Guildhall is thinking...

    relay->>pg: lock unsent outbox_jobs
    relay->>sqs: send job
    relay->>pg: mark sent
    sqs->>runner: receive
    runner->>pg: already processed? (skip if so)
    runner->>plugin: handler(invocation, ctx)
    plugin->>pg: ctx.db.fetch(...) reads
    plugin-->>runner: CommandOutcome(reply, writes, jobs, events)
    rect rgba(128, 128, 128, 0.15)
        runner->>pg: claim dedupe key in processed_jobs
        runner->>pg: plug-in writes, outbox jobs and events
        runner->>pg: insert outbox_replies
    end
    runner->>sqs: delete message

    replies->>pg: lock unsent outbox_replies
    replies->>discord: PATCH webhook .../messages/@original
    replies->>pg: mark sent
    discord-->>user: the report
```

If the handler fails for good, the runner queues a generic "Something went
wrong" reply instead, so nobody is left looking at "thinking...". The api
never holds the bot token: the reply is edited in through the interaction's
own 15-minute token.

### From a played match to a post in Discord

No command involved: a schedule notices the match, and the pipeline fetches,
waits for the parse, archives, analyses and posts
([spec: match pipeline](design-spec.md#match-pipeline)). Each arrow into
SQS goes through `outbox_jobs` and the relay, as in the previous diagram;
it's drawn direct here to keep it readable.

```mermaid
sequenceDiagram
    autonumber
    participant scheduler as Scheduler
    participant pg as Postgres
    participant sqs as SQS
    participant gank as Gank Report handlers
    participant od as OpenDota
    participant s3 as S3
    participant events as Event dispatcher
    participant discord as Discord

    Note over scheduler: every 15 minutes, in every worker
    rect rgba(128, 128, 128, 0.15)
        scheduler->>pg: claim tick in schedule_runs (one worker wins)
        scheduler->>pg: one poll_matches job per linked account
    end
    sqs->>gank: poll_matches
    gank->>od: GET /players/{id}/matches
    gank->>pg: which are already polled?
    rect rgba(128, 128, 128, 0.15)
        gank->>pg: mark new matches polled
        gank->>pg: queue fetch_match + analyze_match (delayed 5 min)
    end

    sqs->>gank: fetch_match (attempt 1)
    gank->>od: GET /matches/{id}
    alt not parsed yet (attempts 1 and 2)
        gank->>od: POST /request/{id} (ask for a parse)
        gank-->>sqs: retry_in 5 min, then 10 min
    else parsed, or attempt 3
        gank->>s3: archive raw JSON (before commit)
        gank->>pg: insert gank_report_matches
    end

    sqs->>gank: analyze_match
    gank->>pg: match row there yet? (else retry in 5 min)
    gank->>s3: read archived match
    gank->>gank: compute every metric
    gank->>pg: baselines: last 20 games, same hero and kind
    rect rgba(128, 128, 128, 0.15)
        gank->>pg: insert player_matches + metrics
        opt the match ended in the last 12 hours
            gank->>pg: insert outbox_events: match_analyzed
        end
    end

    events->>pg: lock pending outbox_events
    events->>events: render(event) to Message, no I/O
    events->>discord: POST /channels/{id}/messages (nonce)
    events->>pg: record discord_message_id
```

The handlers never call S3, Postgres writes or SQS themselves: they return
an `Outcome`, and the runner uploads its archives, then commits its writes,
jobs and events in one transaction. The arrows from "Gank Report handlers"
are drawn to where the effect lands.

### Linking an account

`/link` belongs to the platform; the plug-in only says whether an account is
valid. Accepting it stores the link, queues the backfill and queues the
reply in one transaction
([ADR 0066](adr/0066-the-platform-runs-link.md),
[ADR 0067](adr/0067-dota-links-and-the-backfill.md)).

```mermaid
sequenceDiagram
    autonumber
    actor user as User
    participant runner as Job runner
    participant gank as Gank Report
    participant od as OpenDota
    participant pg as Postgres

    user->>runner: /link dota account:123 (via api and SQS, as above)
    runner->>pg: current link, if any
    runner->>gank: verify(LinkRequest)
    gank->>gank: Steam ID or account ID, to account ID
    gank->>od: GET /players/{id}/matches?limit=1
    alt OpenDota sees a match
        gank-->>runner: Accepted(account ID)
        rect rgba(128, 128, 128, 0.15)
            runner->>pg: claim dedupe key
            runner->>pg: upsert linked_accounts (keeps the row ID)
            runner->>pg: queue backfill job
            runner->>pg: queue reply "Linked your dota account"
        end
        Note over runner,pg: backfill = poll_matches with the 100 newest matches
    else not an account, or no visible matches
        gank-->>runner: Rejected(reason)
        rect rgba(128, 128, 128, 0.15)
            runner->>pg: claim dedupe key
            runner->>pg: queue reply with the reason
        end
    end
```

### What happens to a job message

Every message the runner receives ends in exactly one of these ways. The
plug-in signals which by what it returns or raises
([ADR 0018](adr/0018-job-runner.md),
[ADR 0033](adr/0033-throttled-jobs-are-deferred.md)).

```mermaid
flowchart TB
    received(["Message received from SQS"])
    envelope{"Valid envelope,<br/>plug-in enabled,<br/>known job type?"}
    duplicate{"Key already in<br/>processed_jobs?"}
    payload{"Payload valid?"}
    handler["Run handler under a timeout"]
    result{"What came back?"}
    upload["Upload archives to S3"]
    commit["One transaction:<br/>claim key, writes,<br/>outbox jobs, events, replies"]

    done(["Done: delete message"])
    dup(["Duplicate: delete message"])
    notready(["Not ready: send again as attempt + 1<br/>after retry_in, delete this one"])
    deferred(["Deferred: send again after the wait,<br/>same attempt, delete this one"])
    retrying(["Retrying: hide the message for a<br/>backoff; SQS delivers it again"])
    dead(["Dead-lettered: copy to the DLQ<br/>with the reason, delete this one"])

    received --> envelope
    envelope -- no --> dead
    envelope -- yes --> duplicate
    duplicate -- yes --> dup
    duplicate -- no --> payload
    payload -- no --> dead
    payload -- yes --> handler
    handler --> result
    result -- "PermanentError, contract violation" --> dead
    result -- "Outcome with retry_in" --> notready
    result -- "ThrottledError: no budget, 429" --> deferred
    result -- "RetryableError, timeout, crash" --> retrying
    result -- "Outcome" --> upload
    upload -- "S3 failed" --> retrying
    upload --> commit
    commit -- "key was claimed meanwhile" --> dup
    commit -- "database error" --> retrying
    commit --> done
```

A retried message that SQS has delivered 5 times moves to the dead-letter
queue on its own, by the queue's redrive policy. Throttling and "not ready"
send a fresh message instead of retrying, so neither ever counts towards
that limit.

## 7. Lifecycles

### A match, per linked account

```mermaid
stateDiagram-v2
    [*] --> Polled: poll or backfill lists it
    Polled --> Fetching: fetch_match
    Fetching --> WaitingForParse: unparsed, asked for a parse
    WaitingForParse --> Fetching: after 5, then 10 minutes
    Fetching --> Fetched: parsed, or third attempt, or older than 7 days
    Fetched --> Analysed: analyze_match records metrics
    Analysed --> Posted: ended in the last 12 hours
    Analysed --> [*]: ended earlier, recorded without a post
    Posted --> [*]
```

`analyze_match` is queued with the fetch and simply waits (checking every 5
minutes, for about two hours) until the match row exists
([ADR 0046](adr/0046-analysis-waits-for-the-fetch.md)).

### An outbox row (events and replies)

```mermaid
stateDiagram-v2
    [*] --> Pending: committed with the change that caused it
    Pending --> Pending: 5xx or network error, attempts + 1, backoff
    Pending --> Pending: 429, wait retry_after
    Pending --> Sent: Discord accepted it
    Pending --> Abandoned: other 4xx, too many attempts, or too old
    Sent --> [*]: pruned later
    Abandoned --> [*]: pruned later
```

"Too old" is 14 minutes for a reply, since the interaction token expires at
15, and a day for an event, since a match report posted late is worse than
none ([ADR 0064](adr/0064-replies-through-an-outbox.md),
[ADR 0072](adr/0072-posting-events-to-a-channel.md)).
