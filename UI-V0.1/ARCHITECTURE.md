# Architecture V0.2.1

```text
React / Vite Workbench
        |
        | JSON API + SSE
        v
FastAPI Routes
        |
        +---- Repository ---- Database ---- SQLite
        |                       |             conversations
        |                       |             messages(parent_id)
        |                       |             branches(head pointer)
        |                       +---- migrations / backup / integrity
        |
        +---- Context Compiler
        |      Branch strategy
        |      Linear strategy
        |      Token budget pruning
        |      Context Diff
        |
        +---- Chat Provider ---- Ollama :11434
               chat + stream_chat
```

## Backend Modules

```text
backend/app/
├── application.py       App factory, migration/bootstrap, error mapping
├── config.py            Environment-backed settings
├── database.py          Connections, transactions, migrations, backup
├── repository.py        Conversation, branch and message persistence
├── context_compiler.py  Strategies, budget pruning and Context Diff
├── providers/           Provider protocol and Ollama adapter
└── routes/              Conversation, branch, context, chat and SSE API
```

## Data Model

Messages remain a tree:

```text
messages(id, conversation_id, parent_id, role, content, tokens, created_at)
```

Named branches are durable pointers into that tree:

```text
branches(
  id,
  conversation_id,
  name,
  forked_from_message_id,
  root_message_id,
  head_message_id,
  is_main
)
```

The active UI state is persisted on each Conversation:

```text
conversations(active_branch_id, active_message_id, token_budget)
```

Shared ancestor messages are not duplicated and do not need a single `branch_id`. A branch path is reconstructed from its head through `messages.parent_id`.

## Chat Transaction

Normal and streaming chat avoid holding a SQLite write lock during model generation:

```text
read + validate branch head + compile budgeted context
        |
        v
call provider / consume full stream
        |
        v
BEGIN IMMEDIATE
revalidate branch head
insert user
insert assistant
advance branch head + active state
COMMIT
```

If the provider fails, the client disconnects, or branch state changes concurrently, no partial exchange is committed.

## Context Strategies

Branch strategy:

```text
ancestor_path(selected_message)
```

Linear strategy:

```text
all conversation messages ordered by id
```

Both preserve the system prompt and new user message. When the estimated input exceeds `token_budget`, the compiler removes the oldest history and keeps a contiguous recent suffix. It returns included and truncated message IDs for inspection.

## Branch Deletion

Deleting a non-Main branch removes its `root_message_id` subtree through foreign-key cascade. Descendant named branches are removed in the same transaction. Main is protected, and deletion is rejected if corrupted pointers would make the target subtree overlap Main.

## Compatibility

The original `POST /api/chat` remains available. Requests without `branch_id` resolve a branch at `parent_id` or create an automatic named branch when continuing from a historical node. V0.2 frontend uses explicit branch APIs and `POST /api/chat/stream`.

## Summary, Merge and DAG

Summaries are read-only derived records. A summary has an immutable ordered source list,
message citations such as `[m:42]`, and optional structured claims. The API returns the
source message payload so a client can navigate back to the original evidence. If any cited
message is removed by an explicit branch deletion, the summary becomes `orphaned` and
is no longer presented as citable.

Merge is deliberately a two-step operation:

```text
POST /api/conversations/{id}/merges/preview
        |
        | preview_token + explicit conflict resolutions
        v
POST /api/conversations/{id}/merges
        |
        v
new derived branch + merge snapshot summary
```

The preview is read-only and is recomputed during execution. A changed branch head makes
the token stale. Unresolved conflicts block execution. A successful merge never rewrites
or deletes raw messages; rollback marks the derived operation rolled back and restores the
active state captured before the merge. The result branch is not automatically activated.

`GET /api/conversations/{id}/dag` derives an acyclic graph from message parent edges,
branch inputs, cited summaries, merge operations and result branches. Merge operations
and rollback events remain available as version history even if a named branch is later
deleted.
