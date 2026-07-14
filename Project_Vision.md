# RemnantMCP — Persistent Cross-Tool Project Memory System
### Project Architecture & Design Vision

---

## Table of Contents

1. [Problem Context & Motivation](#1-problem-context--motivation)
2. [System Goals & Success Criteria](#2-system-goals--success-criteria)
3. [Technology Stack & Design Rationale](#3-technology-stack--design-rationale)
4. [High-Level Architecture Overview](#4-high-level-architecture-overview)
5. [Layer 1 — Ingestion Pipeline](#5-layer-1--ingestion-pipeline)
6. [Layer 2 — Knowledge Extraction Agent](#6-layer-2--knowledge-extraction-agent)
7. [Layer 3 — Unified Memory Store](#7-layer-3--unified-memory-store)
8. [Layer 4 — Retrieval & Ranking Engine](#8-layer-4--retrieval--ranking-engine)
9. [Layer 5 — MCP Server (Tool Interface)](#9-layer-5--mcp-server-tool-interface)
10. [Data Flow Diagram](#10-data-flow-diagram)
11. [Memory Schema Design](#11-memory-schema-design)
12. [LangGraph Agent Architecture](#12-langgraph-agent-architecture)
13. [Cross-Tool Compatibility Strategy](#13-cross-tool-compatibility-strategy)
14. [Security & Privacy Design](#14-security--privacy-design)
15. [Scalability & Operational Considerations](#15-scalability--operational-considerations)
16. [Implementation Roadmap](#16-implementation-roadmap)

---

## 1. Problem Context & Motivation

AI coding assistants are fundamentally **amnesiac** — every session begins from zero. This creates a compounding set of problems across a project's lifecycle:

| Problem | Manifestation |
|---|---|
| **Lost Reasoning** | Why an architecture was chosen is never stored; only *what* was built survives in code |
| **Repeated Failures** | Agents re-suggest approaches that were previously attempted and explicitly rejected |
| **Context Tax** | Developers spend significant session time re-explaining constraints already communicated before |
| **Tool Fragmentation** | Switching from Cursor to Claude to Windsurf resets all accumulated understanding |
| **Institutional Drift** | Long-running projects lose coherence as team members and tools change |

Git preserves *what changed*. Documentation explains *what exists*. **Neither captures *why*.**

RemnantMCP introduces a persistent, tool-agnostic memory layer that continuously distills engineering sessions into structured, reusable knowledge — making every AI coding session smarter than the last.

---

## 2. System Goals & Success Criteria

### Primary Goals

- **Continuity**: A developer should never have to re-explain a decision that was already made in a previous session.
- **Tool Agnosticism**: The memory layer must be accessible by any AI coding assistant that supports the Model Context Protocol.
- **Signal over Noise**: Retrieved memories must be tightly scoped to the current task — irrelevant history must not pollute context windows.
- **Zero Friction**: Memory capture should require no manual effort from the developer.

### Success Metrics

- Reduction in "context re-explanation" events per session
- Reduction in suggestions for previously-rejected implementation approaches
- Memory retrieval precision — fraction of retrieved memories that are rated relevant by the developer
- Cross-tool session continuity — ability to resume reasoning context after switching assistants

---

## 3. Technology Stack & Design Rationale

Each technology was chosen for a specific architectural purpose, not interchangeability.

### Python
The orchestration language. Python's ecosystem (LangChain, LangGraph, asyncio, psycopg2, neo4j driver) offers the deepest integration with every other tool in this stack. All agent logic, pipeline stages, and the MCP server are implemented in Python.

### Model Context Protocol (MCP)
MCP is the **interface contract** between RemnantMCP and AI coding tools. Rather than building tool-specific plugins, the system exposes a single MCP server that any compliant client (Claude Desktop, Cursor, Windsurf, etc.) can connect to. This is the single most important architectural decision for cross-tool compatibility.

### LangGraph
The **agent execution engine**. LangGraph models the knowledge extraction process as a stateful directed graph where each node performs a specific reasoning task (e.g., classify artifact type → extract decisions → resolve entity references → store). Its state management and conditional branching support the complex multi-step reasoning required to convert raw session artifacts into structured memories.

### LangChain
The **foundation layer** for LLM integration. LangChain provides prompt templates, output parsers, chain composition, and document loaders used across the ingestion and retrieval pipelines.

### PostgreSQL
The **source-of-truth relational store**. PostgreSQL holds the canonical memory records with full structured metadata, versioning history, project configurations, and session logs. It is the authoritative registry that all other stores are derived from. Using PostgreSQL ensures ACID compliance for memory writes and enables complex relational queries.

### Qdrant Cloud
The **semantic search engine**. Qdrant stores vector embeddings of all memory objects and enables similarity-based retrieval at query time. It is chosen over alternatives because of its native support for payload-based filtering, allowing hybrid retrieval (semantic + metadata filters like project ID, component, or memory type) without compromising retrieval speed.

### Neo4j Aura Free
The **knowledge graph**. Neo4j stores the relationships *between* memory objects: which decisions influenced which components, which failed approaches led to which architectural choices, which bugs triggered which design changes. Graph traversal enables the system to reason about chains of causality that vector similarity alone cannot capture.

### Voyage Code 3 Embeddings
The **semantic representation model**. Voyage Code 3 is a code-specialized embedding model, making it significantly more accurate than general-purpose embeddings for queries involving function names, class hierarchies, API patterns, and implementation strategies. This directly improves retrieval precision for engineering memories.

### Voyage rerank-2.5-lite
The **re-ranking model**. A purpose-built cross-encoder model from Voyage AI that scores `(query, document)` pairs directly, producing deterministic relevance scores with low latency (~100–300ms) and minimal cost. Because it shares the same code-specialized semantic space as Voyage Code 3 embeddings, ranking scores are highly consistent with the initial retrieval stage. It is used after Qdrant semantic search to reorder the candidate memory set before context packing.

### Gemini Flash Lite
The **reasoning model** for knowledge extraction. Flash Lite provides a strong combination of speed, cost-efficiency, and sufficient reasoning capability for structured extraction tasks. It is used for: classifying artifacts, extracting decisions, and resolving entities.

### Git
The **primary source artifact**. Git diffs, commit messages, branch histories, and file change patterns are the richest structured signal available at the end of a coding session. Git provides the ground truth for *what* changed, which the system then reasons about to infer *why*.

---

## 4. High-Level Architecture Overview

RemnantMCP is composed of five sequential layers, each with a distinct responsibility:

```
+-------------------------------------------------------------------------+
|                        DEVELOPER ENVIRONMENT                            |
|                                                                         |
|   AI Coding Tool (Cursor / Claude / Windsurf / etc.)                   |
|   Git Repository   .   Chat Logs   .   Terminal Output                 |
+----------------------------+--------------------------------------------+
                             |  Session Artifacts
                             v
+-------------------------------------------------------------------------+
|  LAYER 1 -- INGESTION PIPELINE                                          |
|  Git diff parser . Chat log reader . Commit analyzer . File watcher    |
+----------------------------+--------------------------------------------+
                             |  Normalized Artifact Objects
                             v
+-------------------------------------------------------------------------+
|  LAYER 2 -- KNOWLEDGE EXTRACTION AGENT  (LangGraph + Gemini Flash Lite)|
|  Classify -> Extract -> Resolve -> Validate -> Structure               |
+----------------------------+--------------------------------------------+
                             |  Structured Memory Objects
                             v
+-------------------------------------------------------------------------+
|  LAYER 3 -- UNIFIED MEMORY STORE                                        |
|  PostgreSQL (canonical)  .  Qdrant Cloud (vectors)  .  Neo4j (graph)   |
+----------------------------+--------------------------------------------+
                             |  Indexed Knowledge
                             v
+-------------------------------------------------------------------------+
|  LAYER 4 -- RETRIEVAL & RANKING ENGINE  (LangChain + Qdrant + Neo4j)   |
|  Semantic search . Graph traversal . Hybrid re-ranking . Context packing|
+----------------------------+--------------------------------------------+
                             |  Ranked Memory Context
                             v
+-------------------------------------------------------------------------+
|  LAYER 5 -- MCP SERVER  (FastMCP)                                       |
|  Tool: remember_session . Tool: recall_context . Tool: list_decisions   |
+-------------------------------------------------------------------------+
```

---

## 5. Layer 1 — Ingestion Pipeline

**Responsibility**: Collect raw artifacts from a completed coding session and normalize them into a consistent intermediate format before reasoning begins.

### Input Sources

| Source | Collection Method | Trigger |
|---|---|---|
| **Git Diff** | `git diff HEAD~1` or staged diff | Post-commit hook / manual call |
| **Commit Message** | `git log --format` | Post-commit hook |
| **File Change List** | Git status + diff stat | Post-commit hook |
| **Chat Transcript** | MCP tool call from assistant | End of session |
| **Terminal Output** | Piped stderr/stdout capture | Optional integration |
| **Execution Errors** | Structured error log | Optional integration |

### Normalization Schema

Every input source is converted into an `ArtifactObject`:

```python
ArtifactObject {
    source_type: Enum[GIT_DIFF, COMMIT, CHAT, ERROR_LOG, FILE_CHANGE]
    project_id: str          # Unique project identifier
    session_id: str          # Groups artifacts from the same work session
    timestamp: datetime
    raw_content: str         # Verbatim source content
    file_paths: List[str]    # Affected file paths, if applicable
    metadata: Dict           # Source-specific additional fields
}
```

### Design Decisions

- **Session Grouping**: Artifacts within a configurable time window (default: 4 hours) are grouped into a single session to provide the extraction agent with co-temporal context.
- **Deduplication**: Content hashing prevents the same artifact from being processed twice across runs.
- **Incremental Processing**: Only new commits since the last processed SHA are ingested, tracked via the `session_log` table in PostgreSQL.

---

## 6. Layer 2 — Knowledge Extraction Agent

**Responsibility**: Transform normalized `ArtifactObject`s into structured `MemoryObject`s using a LangGraph agent with multi-step reasoning.

### Agent Graph Topology

```
  [START]
     |
     v
+------------------+
|  artifact_router |  -- Classifies artifact type and routes to appropriate extractor
+--------+---------+
         |
    +----+-----------------------------+
    |                                 |
    v                                 v
+--------------+               +---------------+
| code_extract |               | chat_extractor|
| (git diffs)  |               | (transcripts) |
+------+-------+               +-------+-------+
       |                               |
       +---------------+---------------+
                       |
                       v
            +---------------------+
            |  entity_resolver    |  -- Resolves file paths, function names, component refs
            +----------+----------+
                       |
                       v
            +---------------------+
            |  relationship_mapper|  -- Builds edges: decision->component, failure->decision
            +----------+----------+
                       |
                       v
            +---------------------+
            |  validator          |  -- Checks completeness; re-prompts if fields missing
            +----------+----------+
                       |
                       v
            +---------------------+
            |  memory_writer      |  -- Fans out to PostgreSQL, Qdrant, Neo4j
            +----------+----------+
                       |
                    [END]
```

### Memory Types Extracted

| Memory Type | Description | Example |
|---|---|---|
| `ARCHITECTURAL_DECISION` | A deliberate structural choice with alternatives considered | "Chose event-sourcing over CRUD for audit log requirements" |
| `IMPLEMENTATION_RATIONALE` | Why a specific code pattern was used | "Used retry decorator instead of manual try/except for consistent backoff" |
| `FAILED_APPROACH` | An attempt that was tried and abandoned, with the reason | "Attempted WebSocket; abandoned due to load balancer timeout constraints" |
| `BUG_RESOLUTION` | A bug fix and its root cause explanation | "Race condition in cache invalidation fixed by adding distributed lock" |
| `DESIGN_TRADEOFF` | Explicitly weighed options | "Prioritized read latency over write consistency — acceptable given read-heavy workload" |
| `COMPONENT_RELATIONSHIP` | Dependencies and interactions between modules | "Auth service depends on UserRepository for session validation" |
| `CONSTRAINT` | Known limitations or requirements that bounded decisions | "Must remain compatible with Python 3.9 — cannot use match statements" |

---

## 7. Layer 3 — Unified Memory Store

**Responsibility**: Persist structured memories across three complementary stores, each optimized for a different retrieval pattern.

### Storage Architecture

```
                +------------------------------+
                |     Structured MemoryObject  |
                +------+-------------+---------+
                       |             |           |
          +------------+             |           +-----------+
          v                          v                       v
+------------------+    +----------------------+  +--------------------+
|   PostgreSQL     |    |    Qdrant Cloud      |  |      Neo4j Aura    |
|  (Source of Truth|    |  (Vector Index)      |  |  (Knowledge Graph) |
|                  |    |                      |  |                    |
|  memories table  |    |  memory_vectors      |  |  Nodes: Memory,    |
|  sessions table  |    |  collection          |  |  Component, File,  |
|  projects table  |    |                      |  |  Decision, Session |
|  relationships   |    |  payload: memory_id, |  |                    |
|  table           |    |  project_id, type,   |  |  Edges: INFLUENCED,|
|                  |    |  component, timestamp |  |  REJECTED_IN_FAVOR,|
+------------------+    +----------------------+  |  FIXES, DEPENDS_ON |
                                                   +--------------------+
```

### PostgreSQL Schema (Core Tables)

**`projects`**
```
id (uuid PK), name, repo_path, created_at, config (jsonb)
```

**`sessions`**
```
id (uuid PK), project_id (FK), started_at, ended_at, artifact_count, status
```

**`memories`**
```
id (uuid PK), project_id (FK), session_id (FK), memory_type (enum),
title (text), content (text), rationale (text), components (text[]),
file_paths (text[]), tags (text[]), confidence_score (float),
created_at, updated_at, is_superseded (bool), superseded_by (uuid FK)
```

**`memory_relationships`**
```
id (uuid PK), source_memory_id (FK), target_memory_id (FK),
relationship_type (enum), created_at
```

### Qdrant Collection Design

Each memory's `content` + `rationale` fields are concatenated and embedded using Voyage Code 3, then stored in Qdrant with the following payload:

```json
{
  "memory_id": "uuid",
  "project_id": "uuid",
  "memory_type": "ARCHITECTURAL_DECISION",
  "component": "auth-service",
  "file_paths": ["src/auth/handler.py"],
  "session_id": "uuid",
  "timestamp": "iso8601",
  "confidence_score": 0.91
}
```

Qdrant's payload filtering allows retrieval to be scoped to a specific project, component, file, or memory type before vector similarity is computed — preventing cross-project memory contamination.

### Neo4j Graph Schema

**Node Types**: `Memory`, `Component`, `File`, `Session`, `Project`

**Relationship Types**:
- `(Memory)-[:INFLUENCED]->(Memory)` — Decision A shaped Decision B
- `(Memory)-[:REJECTED_IN_FAVOR_OF]->(Memory)` — Failed approach replaced by accepted approach
- `(Memory)-[:FIXES]->(Memory)` — Bug resolution addresses a known constraint
- `(Memory)-[:APPLIES_TO]->(Component)` — Decision scoped to a component
- `(Memory)-[:TOUCHES]->(File)` — Memory references a specific file
- `(Session)-[:PRODUCED]->(Memory)` — Session provenance

---

## 8. Layer 4 — Retrieval & Ranking Engine

**Responsibility**: Given a developer's current task context, retrieve the most relevant memories and pack them efficiently into a context block.

### Retrieval Strategy: Hybrid Three-Phase Retrieval

**Phase 1 — Semantic Search (Qdrant)**
- The current task description / active file / recent code is embedded using Voyage Code 3
- Qdrant returns top-K candidates (default: 20) filtered by `project_id`
- Optional secondary filters: `component`, `file_path`, `memory_type`

**Phase 2 — Graph Expansion (Neo4j)**
- For each candidate memory from Phase 1, traverse the knowledge graph 1–2 hops
- Fetch memories that are causally related (e.g., failed approaches that led to selected candidates, constraints that bounded them)
- This surfaces contextually critical memories that may not be semantically similar but are causally relevant

**Phase 3 — Re-Ranking (Voyage rerank-2.5-lite)**
- The candidate set is passed as `(query, memory_content)` pairs to Voyage rerank-2.5-lite
- The cross-encoder model scores each pair directly, producing deterministic relevance scores
- Scores are combined with a recency and confidence weight to produce a final rank
- Final context is assembled from the top-N memories, respecting a configurable token budget

### Context Packing Format

```
=== PROJECT MEMORY CONTEXT ===

[ARCHITECTURAL DECISION -- Auth Service -- 2025-06-12]
Decision: JWT-based stateless authentication with Redis session blacklist
Rationale: Needed horizontal scalability without sticky sessions; Redis blacklist
           enables logout without storing full session state in DB.
Related Files: src/auth/jwt_handler.py, src/middleware/auth.py

[FAILED APPROACH -- Auth Service -- 2025-06-10]
Attempted: Server-side session storage in PostgreSQL
Abandoned Because: Session table became a write bottleneck under load testing
                   at 500 req/s; connection pool saturation observed.
This failure directly influenced the above decision.

[CONSTRAINT -- Global -- 2025-05-01]
Constraint: Service must maintain <100ms P99 latency for auth checks.
Impact: Rules out any synchronous external verification calls during request path.

=== END MEMORY CONTEXT ===
```

---

## 9. Layer 5 — MCP Server (Tool Interface)

**Responsibility**: Expose the memory system as a set of MCP tools that any compliant AI coding assistant can call.

### MCP Tools Exposed

#### `remember_session`
Called at the end of a session to trigger ingestion and knowledge extraction.

```
Input:
  - project_id: str
  - chat_transcript: str (optional)
  - commit_sha: str (optional -- defaults to HEAD)
  - session_notes: str (optional)

Output:
  - memories_extracted: int
  - session_id: str
  - summary: str
```

#### `recall_context`
Called at the start of a session or when the agent needs historical context.

```
Input:
  - project_id: str
  - query: str (current task description or active code snippet)
  - component: str (optional)
  - file_path: str (optional)
  - memory_types: List[str] (optional -- filter by type)
  - max_tokens: int (default: 2000)

Output:
  - context_block: str (formatted memory context, token-budget-aware)
  - memory_ids: List[str]
  - retrieved_count: int
```

#### `list_decisions`
Returns a structured list of all architectural decisions for a project.

```
Input:
  - project_id: str
  - component: str (optional)

Output:
  - decisions: List[{ title, rationale, date, files }]
```

#### `mark_superseded`
Allows the developer or agent to explicitly deprecate an outdated memory.

```
Input:
  - memory_id: str
  - reason: str
  - new_memory_id: str (optional)

Output:
  - success: bool
```

#### `get_failed_approaches`
Specifically retrieves failed approaches to prevent repetition.

```
Input:
  - project_id: str
  - query: str

Output:
  - failed_approaches: List[{ title, what_was_tried, why_abandoned, date }]
```

### MCP Server Implementation

The server is built using **FastMCP**, a high-level Python framework that wraps the MCP specification with a clean, decorator-based API. FastMCP handles transport negotiation automatically — `stdio` for local single-user usage and `SSE` for remote/multi-user deployments — while dramatically reducing boilerplate compared to the raw Python MCP SDK. Each tool invocation is logged to PostgreSQL for audit and debugging purposes.

---

## 10. Data Flow Diagram

```
+===================================================================================+
|                                                                                   |
|                        REMNANTMCP -- DATA FLOW DIAGRAM                           |
|                                                                                   |
+===================================================================================+

  +-----------------------------------------------------------------------------+
  |                          SESSION END (Write Path)                           |
  +-----------------------------------------------------------------------------+

  Developer finishes a coding session
         |
         +--- Git Commits ---------------------------------------------------+
         +--- Chat Transcript (via MCP tool call)                           |
         +--- Session Notes / Error Logs (optional)                         |
                                                                             |
                         +---------------------------------------------------v-----+
                         |          INGESTION PIPELINE                             |
                         |                                                         |
                         |  +--------------+    +------------------------+         |
                         |  |  Git Parser  |    |  Chat / Log Normalizer |         |
                         |  |              |    |                         |         |
                         |  | . diff parser|    | . transcript chunker   |         |
                         |  | . commit msg |    | . error classifier     |         |
                         |  | . stat parser|    | . timestamp alignment  |         |
                         |  +------+-------+    +-----------+-------------+         |
                         |         |                         |                      |
                         |         +-----------+-------------+                      |
                         |                     v                                    |
                         |          ArtifactObject [ ]                              |
                         |          (session-grouped, deduplicated)                 |
                         +---------------------+-----------------------------------+
                                               |
                                               v
                         +-----------------------------------------------------+
                         |     KNOWLEDGE EXTRACTION AGENT  (LangGraph)         |
                         |                                                      |
                         |  +------------------------------------------+       |
                         |  |  artifact_router                          |       |
                         |  |  Classifies: CODE / CHAT / ERROR / COMMIT |       |
                         |  +--------------------+----------------------+       |
                         |                       |                              |
                         |        +--------------+-------------+                |
                         |        v                v           v                |
                         |  +----------+  +----------+  +-----------+           |
                         |  |code      |  |chat      |  |err        |           |
                         |  |extract   |  |extract   |  |extract    |           |
                         |  |          |  |          |  |           |           |
                         |  |Gemini    |  |Gemini    |  |Gemini     |           |
                         |  |Flash Lite|  |Flash Lite|  |Flash Lite |           |
                         |  +-----+----+  +-----+----+  +-----+-----+           |
                         |        +-------------+-------------+                 |
                         |                      |                               |
                         |                      v                               |
                         |         +---------------------+                      |
                         |         |   entity_resolver   |                      |
                         |         | Resolves: filenames,|                      |
                         |         | function names,     |                      |
                         |         | component labels    |                      |
                         |         +----------+----------+                      |
                         |                    |                                 |
                         |                    v                                 |
                         |         +---------------------+                      |
                         |         | relationship_mapper |                      |
                         |         | Builds causal edges |                      |
                         |         | between memories    |                      |
                         |         +----------+----------+                      |
                         |                    |                                 |
                         |                    v                                 |
                         |         +---------------------+                      |
                         |         |     validator       |                      |
                         |         | Ensures completeness|                      |
                         |         | Re-prompts if needed|                      |
                         |         +----------+----------+                      |
                         |                    |                                 |
                         |                    v                                 |
                         |         Structured MemoryObject [ ]                  |
                         +--------------------+--------------------------------+
                                              |
                         +--------------------v-------------------------------+
                         |              UNIFIED MEMORY STORE                  |
                         |                                                    |
                         |  +-------------+  +------------+  +-----------+   |
                         |  | PostgreSQL  |  |Qdrant Cloud|  |  Neo4j    |   |
                         |  |             |  |            |  |  Aura     |   |
                         |  | Canonical   |  |Voyage Code3|  |           |   |
                         |  | memory      |  |embeddings  |  | Graph     |   |
                         |  | record +    |  |+ payload   |  | edges +   |   |
                         |  | metadata +  |  |filters     |  | nodes     |   |
                         |  | versioning  |  |            |  |           |   |
                         |  +-------------+  +------------+  +-----------+   |
                         +---------------------------------------------------+


  +-----------------------------------------------------------------------------+
  |                         SESSION START (Read Path)                           |
  +-----------------------------------------------------------------------------+

  Developer starts a new session -- AI coding tool calls recall_context
         |
         v
  +----------------------------------------------------------------------+
  |                        MCP SERVER                                     |
  |                                                                       |
  |   Tool: recall_context                                                |
  |   Input: { project_id, query, component?, file_path?, max_tokens }   |
  +----------------------------------+------------------------------------+
                                     |
                                     v
  +----------------------------------------------------------------------+
  |                   RETRIEVAL & RANKING ENGINE                          |
  |                                                                       |
  |   PHASE 1 -- Semantic Search                                          |
  |   +------------------------------------------------------------------+|
  |   |  query text                                                       ||
  |   |     |                                                             ||
  |   |     v                                                             ||
  |   |  Voyage Code 3 Embedding Model                                    ||
  |   |     |                                                             ||
  |   |     v                                                             ||
  |   |  Qdrant: vector similarity search                                 ||
  |   |  + payload filter: project_id == X                               ||
  |   |     |                                                             ||
  |   |     v                                                             ||
  |   |  Top-20 Candidate Memory IDs                                      ||
  |   +--------------------------------+---------------------------------+|
  |                                   |                                   |
  |   PHASE 2 -- Graph Expansion                                          |
  |   +--------------------------------v---------------------------------+|
  |   |  Neo4j: traverse 1-2 hops from candidate nodes                   ||
  |   |  Fetch: INFLUENCED_BY, REJECTED_IN_FAVOR_OF, FIXES edges        ||
  |   |     |                                                             ||
  |   |     v                                                             ||
  |   |  Expanded candidate set (semantic + causally related)            ||
  |   +--------------------------------+---------------------------------+|
  |                                   |                                   |
  |   PHASE 3 -- Re-Ranking                                               |
  |   +--------------------------------v---------------------------------+|
  |   |  Voyage rerank-2.5-lite: cross-encoder (query, memory) scoring   ||
  |   |  Score factors: relevance (reranker) + recency + confidence      ||
  |   |     |                                                             ||
  |   |     v                                                             ||
  |   |  Top-N memories ranked, token budget enforced                    ||
  |   +--------------------------------+---------------------------------+|
  |                                   |                                   |
  +-----------------------------------+-----------------------------------+
                                      |
                                      v
                            Formatted Context Block
                                      |
                                      v
                    +---------------------------------+
                    |     AI Coding Assistant         |
                    |  (Cursor / Claude / Windsurf)   |
                    |                                 |
                    |  Receives memory context        |
                    |  before generating any code     |
                    +---------------------------------+
```

---

## 11. Memory Schema Design

### MemoryObject (Internal Canonical Format)

```python
@dataclass
class MemoryObject:
    id: UUID
    project_id: UUID
    session_id: UUID
    memory_type: MemoryType          # Enum of 7 types defined above
    title: str                        # Short, human-readable summary
    content: str                      # Full description of the memory
    rationale: str                    # The "why" -- the reasoning preserved
    alternatives_considered: List[str]  # Other options that were evaluated
    outcome: str                      # What ultimately happened
    components: List[str]             # Affected logical components
    file_paths: List[str]             # Affected file paths
    tags: List[str]                   # Free-form searchable tags
    related_memory_ids: List[UUID]    # Explicit relationships
    confidence_score: float           # 0.0-1.0 extraction confidence
    source_artifact_ids: List[UUID]   # Provenance: which artifacts produced this
    created_at: datetime
    updated_at: datetime
    is_superseded: bool
    superseded_by: Optional[UUID]
```

### Memory Lifecycle

```
  [ DRAFT ]  --------------------------------------------------------->  [ ACTIVE ]
     |                                                                        |
     |  (extraction confidence < 0.6: flagged for review)                    |
     |                                                                        |
     |                                                           (new session provides
     |                                                            contradicting evidence)
     |                                                                        |
     |                                                                        v
     +----------------------------------------------------------------------[ SUPERSEDED ]
                                                                             |
                                                               superseded_by -> new UUID
```

---

## 12. LangGraph Agent Architecture

The Knowledge Extraction Agent is the most complex component. It is implemented as a LangGraph `StateGraph` with the following state definition:

### Agent State

```python
class ExtractionState(TypedDict):
    session_id: str
    project_id: str
    artifacts: List[ArtifactObject]       # Input artifacts
    classified_artifacts: List[Dict]      # After routing
    raw_memories: List[Dict]              # After extraction nodes
    resolved_memories: List[MemoryObject] # After entity resolution
    relationships: List[Tuple]            # (source_id, rel_type, target_id)
    validation_errors: List[str]          # Detected issues
    retry_count: int                      # Guards against infinite retry
    final_memories: List[MemoryObject]    # Ready for storage
    storage_results: Dict                 # Write confirmations
```

### Conditional Edges

- **After `validator`**: If `validation_errors` is non-empty AND `retry_count < 3` — route back to extraction node; otherwise accept with lower confidence score.
- **After `artifact_router`**: Route to `code_extractor`, `chat_extractor`, or `error_extractor` based on classified artifact type; fan out in parallel using LangGraph's `Send` API for concurrent processing.

---

## 13. Cross-Tool Compatibility Strategy

RemnantMCP achieves tool agnosticism through strict adherence to MCP:

- **No tool-specific SDKs** are used in the memory layer itself
- **FastMCP** is the MCP server framework — its decorator-based API (`@mcp.tool`) keeps tool definitions concise and testable
- **stdio transport** (FastMCP default) is used for local single-user deployment (configured in each tool's MCP settings file)
- **SSE transport** is used for team/multi-user deployment scenarios, enabled by FastMCP's built-in transport switching
- **Project identity** is determined by the Git remote URL (normalized), ensuring that the same project is recognized regardless of which tool is accessing the memory

### Tool Configuration Pattern

Each AI coding assistant that supports MCP adds the following to its configuration:

```json
{
  "mcpServers": {
    "remnant": {
      "command": "python",
      "args": ["-m", "remnant.mcp_server"],
      "env": {
        "REMNANT_DB_URL": "postgresql://...",
        "REMNANT_QDRANT_URL": "...",
        "REMNANT_NEO4J_URL": "...",
        "REMNANT_PROJECT_ROOT": "${workspaceFolder}"
      }
    }
  }
}
```

The FastMCP server entrypoint uses `mcp.run()` which auto-selects `stdio` transport when launched as a subprocess by the tool, or `SSE` when running as a standalone service.

The server auto-detects the project by calling `git remote get-url origin` from `REMNANT_PROJECT_ROOT`.

---

## 14. Security & Privacy Design

| Concern | Mitigation |
|---|---|
| **Source code in memories** | Code snippets are stored only as file paths + line ranges; full source is never persisted in the memory store |
| **Secret leakage in transcripts** | Ingestion pipeline runs a regex-based redaction pass (API keys, tokens, passwords) before LLM processing |
| **Cross-project isolation** | Every Qdrant query mandates a `project_id` filter; Neo4j uses project-scoped subgraph labels |
| **LLM data exposure** | Gemini Flash Lite is used with API calls (not fine-tuning); no training data retention per Google API policy |
| **PostgreSQL credentials** | Managed via environment variables; no credentials in source code |
| **Multi-user isolation** | Each developer's project scope is isolated by project_id; no shared memory across projects without explicit configuration |

---

## 15. Scalability & Operational Considerations

### Current Scope (Single Developer, Single Project)
- PostgreSQL: local or managed single-instance
- Qdrant: Qdrant Cloud free tier (1GB vector storage)
- Neo4j: Aura Free (50k nodes, 175k relationships)
- MCP Server: FastMCP with stdio transport, local process

### Growth Path (Team / Multiple Projects)
- PostgreSQL: connection pooling via PgBouncer; read replicas for retrieval
- Qdrant: Cloud paid tier with horizontal sharding per project
- Neo4j: Aura Professional for larger graph budgets
- MCP Server: FastMCP with SSE transport behind a lightweight API gateway
- Extraction Agent: Celery task queue for async, non-blocking ingestion

### Failure Modes & Resilience

| Failure | Behavior |
|---|---|
| Qdrant unavailable | Retrieval falls back to PostgreSQL full-text search |
| Neo4j unavailable | Graph expansion phase skipped; semantic-only retrieval |
| Gemini API error | Retry with exponential backoff (max 3 attempts); artifact saved as DRAFT |
| Git not available | Ingestion skips git sources; chat/log sources still processed |
| Storage write failure | Full transaction rollback; artifact preserved in queue for retry |

---

*RemnantMCP — Because good engineering decisions deserve to be remembered.*
