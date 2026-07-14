# RemnantMCP — Comprehensive Implementation Plan & Checklist

This document details the step-by-step implementation plan for **RemnantMCP**, a persistent, cross-tool project memory system. It expands upon the architectural outline in [Project_Vision.md](file:///Users/riddhipratim/Projects/RemnantMCP/Project_Vision.md) into a granular, actionable development checklist.

For each of the five system layers, we specify the responsibilities, concrete tasks, inputs, outputs, and the internal data flows/interconnections that bind the sub-phases together.

---

## System Overview & Architecture Diagram

```
                                  [Developer Workspace]
                         (Git, Chat Transcripts, Terminal Logs)
                                           |
                                           v
                          +----------------------------------+
                          |   Layer 1: Ingestion Pipeline    |
                          +-----------------+----------------+
                                            |
                                            | ArtifactObject[] (Redacted, Grouped)
                                            v
                          +----------------------------------+
                          | Layer 2: Knowledge Agent (Graph) |
                          +-----------------+----------------+
                                            |
                                            | MemoryObject[] + Relationships
                                            v
                          +----------------------------------+
                          |  Layer 3: Unified Memory Store   |
                          |   (Postgres, Qdrant, Neo4j)      |
                          +-----------------+----------------+
                                            |
                                            | Indexed Memories & Traversed Paths
                                            v
                          +----------------------------------+
                          | Layer 4: Retrieval & Ranking     |
                          +-----------------+----------------+
                                            |
                                            | Ranked Context Block
                                            v
                          +----------------------------------+
                          |      Layer 5: MCP Interface      |
                          |    (FastMCP Server, Tools)       |
                          +----------------------------------+
```

---

## Layer 1 — Ingestion Pipeline

**Phase Description**: Collects raw engineering session artifacts, sanitizes them, deduplicates, and groups them within temporal windows. It outputs a standardized sequence of `ArtifactObject` list representations to feed the reasoning engine.

### Sub-Phases Specification

#### 1. Git Parser
*   **What it does**: Interrogates the Git repository to extract diff changes, log messages, and diff statistics (diffstat) since the last successfully processed commit SHA.
*   **Inputs**:
    *   `repo_path` (str): Absolute path to the project root.
    *   `last_processed_sha` (str | None): Retrieve from PostgreSQL `session_log` table.
*   **Outputs**:
    *   `git_diff_raw` (str): Raw text diff of staged/unstaged changes or commits.
    *   `commit_messages` (List[str]): Extracted git commit logs.
    *   `file_change_stats` (List[Dict]): Map of modified files and changed line counts.
*   **Interconnections & Data Flow**: Reads repository state directly from the local disk. It connects directly to **Layer 1, Sub-Phase 2 (Chat/Log Normalizer & Redactor)**, passing the extracted data `git_diff_raw` (str), `commit_messages` (List[str]), and `file_change_stats` (List[Dict]) for sanitization and redaction.

#### 2. Chat/Log Normalizer & Redactor
*   **What it does**: Accepts terminal stdout/stderr, error logs, and session chat transcripts. Normalizes varying structures (such as Markdown transcripts or json-formatted logs) and executes a regular expression scrubbing pass to remove sensitive secrets (API keys, passwords, database URIs).
*   **Inputs**:
    *   `raw_chat_transcript` (str | None)
    *   `raw_logs` (str | None)
    *   `redaction_rules` (List[str]): List of regex patterns for secret matching.
*   **Outputs**:
    *   `redacted_chat_content` (str)
    *   `redacted_log_content` (str)
*   **Interconnections & Data Flow**: Receives input from **Layer 5, Sub-Phase 3 (MCP Tool: remember_session)** (taking `raw_chat_transcript` and `raw_logs`) and from **Layer 1, Sub-Phase 1 (Git Parser)** (taking `git_diff_raw` and `commit_messages`). It connects directly to **Layer 1, Sub-Phase 3 (Session Grouper & Deduplicator)**, passing the sanitized and redacted strings `redacted_chat_content` (str) and `redacted_log_content` (str).

#### 3. Session Grouper & Deduplicator
*   **What it does**: Groups all incoming sanitized artifacts matching the same project scope within a configurable co-temporal window (default: 4 hours) under a single `session_id`. Computes SHA-256 hashes of `raw_content` to prevent reprocessing duplicate artifacts.
*   **Inputs**:
    *   `raw_artifacts` (List[Dict]): Sanitized outputs from previous sub-phases.
    *   `project_id` (str): Unique project identifier.
    *   `session_window_hours` (int): Configurable window (default: 4).
*   **Outputs**:
    *   `session_id` (str): Instantiated or resolved session token.
    *   `final_artifacts` (List[ArtifactObject]): A list of objects matching the canonical internal scheme.
*   **Interconnections & Data Flow**: Takes inputs from **Layer 1, Sub-Phase 2 (Chat/Log Normalizer & Redactor)** (taking sanitized content strings). It performs queries against **Layer 3, Sub-Phase 1 (PostgreSQL Relational Layer)** (fetching session logs and SHA hashes from the `session_log` and `sessions` tables for deduplication checks). It connects directly to **Layer 2, Sub-Phase 2 (Artifact Router Node)**, passing the list of `final_artifacts` (List[ArtifactObject]) and resolved `session_id` (str) to initialize the LangGraph State.

---

### Layer 1 Implementation Todo Checklist
- [ ] **Scaffold Pipeline Package**: Create directory structure `remnant/ingestion/` with `__init__.py`.
- [ ] **Git Parser Utility**: Implement helper using `gitpython` or subprocess to fetch git diffs (`git diff HEAD~1`), commit logs, and status.
- [ ] **Secret Redaction Pass**: Implement regex patterns matching:
  - [ ] GitHub Tokens (`ghp_[a-zA-Z0-9]{36}`)
  - [ ] Generic API Keys (`api[_-]?key` style)
  - [ ] Database Connection Strings (`postgresql://...`)
- [ ] **Content Hashing Engine**: Add utility to calculate SHA-256 signatures of text blocks to skip identical payloads.
- [ ] **Session Coordinator**: Build logic to query PostgreSQL for active sessions within the 4-hour window, generate UUIDs, and construct `ArtifactObject` objects.
- [ ] **Unit Tests**: Implement mock tests for Git diff output, secret detection/redaction, and temporal grouping.

---

## Layer 2 — Knowledge Extraction Agent (LangGraph)

**Phase Description**: Orchestrates a multi-step LLM extraction pipeline using LangGraph. It ingests the normalized `ArtifactObject`s, runs concurrent LLM extraction passes, resolves code/system entities, constructs graph relations, validates structural completeness, and outputs fully structured `MemoryObject` records.

```
                          [ArtifactObjects Input]
                                     |
                                     v
                          +--------------------+
                          |  artifact_router   |
                          +---------+----------+
                                    |
            +-----------------------+-----------------------+
            v                       v                       v
     +--------------+        +--------------+        +--------------+
     | code_extract |        |chat_extractor|        |error_extract |
     +------+-------+        +------+-------+        +------+-------+
            |                       |                       |
            +-----------------------+-----------------------+
                                    |
                                    v
                          +--------------------+
                          |  entity_resolver   |
                          +---------+----------+
                                    |
                                    v
                          +--------------------+
                          |relationship_mapper |
                          +---------+----------+
                                    |
                                    v
                          +--------------------+
                          |     validator      | <---+ Retry limit < 3
                          +---------+----------+     | (Validation Errors)
                                    |                |
                                    +----------------+
                                    |
                                    | Approved (or Low-Confidence Fallback)
                                    v
                          +--------------------+
                          |   memory_writer    |
                          +--------------------+
```

### Sub-Phases Specification

#### 1. LangGraph State & Schema Skeleton
*   **What it does**: Defines the schema for the agent state (`ExtractionState`) containing inputs, work-in-progress classifications, errors, and final outputs.
*   **Inputs**: None (instantiated with state dictionary).
*   **Outputs**: Fully compiled `StateGraph` skeleton.
*   **Interconnections & Data Flow**: Serves as the static configuration layer for the overall LangGraph. It is instantiated from **Layer 5, Sub-Phase 3 (MCP Tool: remember_session)**, accepting the initial graph state dict containing `artifacts` (List[ArtifactObject]), `session_id` (str), and `project_id` (str). It defines the data-passing boundaries for all graph nodes.

#### 2. Artifact Router Node
*   **What it does**: Classifies `ArtifactObject` types and uses LangGraph's `Send` API to dynamically fan out extraction nodes in parallel based on source classifications.
*   **Inputs**:
    *   `artifacts` (List[ArtifactObject]): From Layer 1.
*   **Outputs**:
    *   `classified_artifacts` (List[Dict]): Routed packets target-bound to extractors.
*   **Interconnections & Data Flow**: Connected from **Layer 1, Sub-Phase 3 (Session Grouper & Deduplicator)**, taking the list of `final_artifacts` from the graph state. It routes and fanned out subsets of `ArtifactObject` objects to the appropriate specialized extractor nodes in **Layer 2, Sub-Phase 3 (Specialized Extractor Nodes)** using LangGraph's dynamic Send API.

#### 3. Specialized Extractor Nodes (Gemini Flash Lite)
*   **What it does**: Executes prompts engineered to extract seven structured memory types:
    1.  `ARCHITECTURAL_DECISION`
    2.  `IMPLEMENTATION_RATIONALE`
    3.  `FAILED_APPROACH`
    4.  `BUG_RESOLUTION`
    5.  `DESIGN_TRADEOFF`
    6.  `COMPONENT_RELATIONSHIP`
    7.  `CONSTRAINT`
*   **Inputs**:
    *   `raw_content` (str)
    *   `source_type` (Enum)
*   **Outputs**:
    *   `raw_memories` (List[Dict]): Unverified memory schema payloads.
*   **Interconnections & Data Flow**: Connected from **Layer 2, Sub-Phase 2 (Artifact Router Node)**, taking the routed `ArtifactObject` subsets. Once the LLM call completes, the extracted, unverified dictionary structures are written to the graph state's `raw_memories` (List[Dict]) field, which is read by **Layer 2, Sub-Phase 4 (Entity Resolver Node)**.

#### 4. Entity Resolver Node
*   **What it does**: Resolves extracted entity names (such as files, folders, and code modules) to absolute repository paths and valid project references by inspecting the current project directories.
*   **Inputs**:
    *   `raw_memories` (List[Dict])
    *   `project_file_tree` (List[str]): Active file list retrieved from the repository directory.
*   **Outputs**:
    *   `resolved_memories` (List[MemoryObject]): Memories with standardized filenames and component labels.
*   **Interconnections & Data Flow**: Connected from **Layer 2, Sub-Phase 3 (Specialized Extractor Nodes)**, taking the `raw_memories` (List[Dict]) list from the graph state. It reads the local repository file tree from disk to normalize references. It passes the resulting `resolved_memories` (List[MemoryObject]) back to the graph state for **Layer 2, Sub-Phase 5 (Relationship Mapper Node)**.

#### 5. Relationship Mapper Node
*   **What it does**: Evaluates dependencies and causal linkages between extracted memories to map relationship edges.
*   **Inputs**:
    *   `resolved_memories` (List[MemoryObject])
*   **Outputs**:
    *   `relationships` (List[Tuple[UUID, RelationshipType, UUID]]): Linked edges (e.g., `REJECTED_IN_FAVOR_OF`, `INFLUENCED`).
*   **Interconnections & Data Flow**: Connected from **Layer 2, Sub-Phase 4 (Entity Resolver Node)**, taking `resolved_memories` (List[MemoryObject]). It scans references to define causal associations and writes `relationships` (List[Tuple[UUID, RelationshipType, UUID]]) to the graph state for **Layer 2, Sub-Phase 6 (Validator Node)**.

#### 6. Validator Node
*   **What it does**: Inspects extracted memory schemas for missing mandatory fields (e.g., title, content, rationale). If deficient, increments `retry_count` and routes back to extractors with feedback.
*   **Inputs**:
    *   `resolved_memories` (List[MemoryObject])
    *   `retry_count` (int)
*   **Outputs**:
    *   `validation_errors` (List[str])
    *   `is_valid` (bool)
*   **Interconnections & Data Flow**: Connected from **Layer 2, Sub-Phase 5 (Relationship Mapper Node)**, taking the state variables `resolved_memories` and `relationships`. On validation failure (if `retry_count < 3`), it loops back to **Layer 2, Sub-Phase 3 (Specialized Extractor Nodes)** passing validation error details. On validation success (or retry exhaustion), it passes the finalized `final_memories` (List[MemoryObject]) and `relationships` to the **Layer 3, Sub-Phase 4 (Resilient Fan-Out Writer)**.

---

### Layer 2 Implementation Todo Checklist
- [ ] **Define Structures**: Implement `ExtractionState` (TypedDict) and `MemoryObject` (dataclass) in `remnant/structures.py`.
- [ ] **LangGraph Topology Setup**: In `remnant/agent/graph.py`, build the `StateGraph` skeleton.
- [ ] **Artifact Router Node**: Code routing logic evaluating `ArtifactObject.source_type`.
- [ ] **Prompt Engineering (Gemini Flash Lite)**:
  - [ ] Write system instruction templates for Git Diff extraction (`code_extract`).
  - [ ] Write system instruction templates for Chat Transcript extraction (`chat_extractor`).
  - [ ] Write system instruction templates for Error/Log extraction (`error_extractor`).
- [ ] **Fuzzy Entity Resolver**: Implement matching logic (using regex/Levenshtein) to verify paths against `git ls-files` output.
- [ ] **Relationship Inference Engine**: Implement rule-based/LLM-aided extraction of links between decisions and failed approaches.
- [ ] **Validation Loop**: Program conditional loop evaluating schema fields; implement LLM self-correction prompt interface.
- [ ] **StateGraph Compilation**: Finalize compile path for validation tests.

---

## Layer 3 — Unified Memory Store

**Phase Description**: Accepts validated `MemoryObject` records and relationship maps from Layer 2. Executes a fan-out persistence operation writing structured metadata to PostgreSQL, semantic embedding indexes to Qdrant Cloud, and semantic/causal dependency relations to Neo4j.

### Storage Connections & Rationale

```
                                  [MemoryWriter Node]
                                           |
                   +-----------------------+-----------------------+
                   |                       |                       |
                   v                       v                       v
          +-----------------+     +-----------------+     +-----------------+
          |   PostgreSQL    |     |  Qdrant Cloud   |     |   Neo4j Aura    |
          |  (Relational)   |     | (Vector Search) |     |  (Graph Store)  |
          +-----------------+     +-----------------+     +-----------------+
          - memories table        - Voyage Code 3   - Memory nodes
          - relationships         - Payload filters - causal links
```

### Sub-Phases Specification

#### 1. PostgreSQL Relational Layer
*   **What it does**: Stores the canonical source-of-truth records for all entities. Handles versioning, superseding links, project metadata, and session history logs.
*   **Inputs**:
    *   `final_memories` (List[MemoryObject])
    *   `relationships` (List[Tuple])
*   **Outputs**:
    *   Saved records (DB states)
*   **Interconnections & Data Flow**: Connected from **Layer 3, Sub-Phase 4 (Resilient Fan-Out Writer)**, taking the `final_memories` and `relationships` database records. It inserts these within a single transactional boundary and provides database confirmation and UUID registration to the writer.

#### 2. Qdrant Cloud Vector Layer
*   **What it does**: Generates embeddings for memory content + rationale utilizing **Voyage Code 3** and indexes them. Implements payload filtering configuration.
*   **Inputs**:
    *   Concatenated string: `title` + `content` + `rationale`.
    *   Payload data: `memory_id`, `project_id`, `memory_type`, `component`, `file_paths`, `timestamp`.
*   **Outputs**:
    *   Vector payload insertion status.
*   **Interconnections & Data Flow**: Connected from **Layer 3, Sub-Phase 4 (Resilient Fan-Out Writer)**, taking each memory's content, rationale, and metadata payload details. It communicates with the Voyage Code 3 embedding API to obtain vectors and indexes them inside Qdrant Cloud. It is queried by **Layer 4, Sub-Phase 1 (Semantic Search)** during search tasks.

#### 3. Neo4j Aura Graph Layer
*   **What it does**: Builds the physical knowledge graph representing causal dependencies, component mappings, and session histories using Cypher transactions.
*   **Inputs**:
    *   Graph nodes: `Memory`, `Component`, `File`, `Session`, `Project`.
    *   Edges: `INFLUENCED`, `REJECTED_IN_FAVOR_OF`, `FIXES`, `APPLIES_TO`, `TOUCHES`, `PRODUCED`.
*   **Outputs**:
    *   Graph write confirmation.
*   **Interconnections & Data Flow**: Connected from **Layer 3, Sub-Phase 4 (Resilient Fan-Out Writer)**, taking the database-registered memory entities and relationship maps. It runs Cypher queries to build node connections. It is queried by **Layer 4, Sub-Phase 2 (Graph Expansion)** during context retrieval.

#### 4. Resilient Fan-Out Writer
*   **What it does**: Orchestrates the multi-database write transaction. Ensures that if any primary storage (Postgres) fails, the entire transaction is rolled back. Handles soft failures of auxiliary stores (e.g. Qdrant/Neo4j offline errors).
*   **Inputs**:
    *   `final_memories`, `relationships`
*   **Outputs**:
    *   `storage_results` (Dict)
*   **Interconnections & Data Flow**: Connected from **Layer 2, Sub-Phase 6 (Validator Node)**, taking the validated `final_memories` and `relationships`. It orchestrates the write order to **Layer 3, Sub-Phase 1 (PostgreSQL)**, **Layer 3, Sub-Phase 2 (Qdrant)**, and **Layer 3, Sub-Phase 3 (Neo4j)**, and updates the graph state's `storage_results` (Dict) which is read by **Layer 5, Sub-Phase 3 (MCP Tool: remember_session)** to return execution summaries.

---

### Layer 3 Implementation Todo Checklist
- [ ] **PostgreSQL Database Schema Setup**:
  - [ ] Write schema migration script creating `projects`, `sessions`, `memories`, `memory_relationships`, and `session_log` tables.
  - [ ] Implement foreign keys, unique constraint on content hash, indexes on `project_id`, `memory_type`.
- [ ] **PostgreSQL Repository Implementation**: Build CRUD database query handlers.
- [ ] **Voyage Code 3 Client Setup**: Integrate Voyage AI SDK and configure embeddings helper function.
- [ ] **Qdrant Collection Orchestrator**:
  - [ ] Write setup script to create Qdrant collection (1024-dimension matching Voyage Code 3).
  - [ ] Implement payload filter checks for `project_id` and `component`.
- [ ] **Neo4j Aura Driver Setup**:
  - [ ] Initialize Python Neo4j Driver wrapper.
  - [ ] Write parameterized Cypher queries to upsert `Memory`, `Component`, `File` nodes, and build directional relationships.
- [ ] **Fan-Out Writer Node**: Write transaction wrapper that executes Postgres insert, Qdrant upsert, and Neo4j graph insert.
- [ ] **Resilience Logic**:
  - [ ] Wrap Postgres insertion in transaction rollback blocks.
  - [ ] Implement fallback catch to record a `DRAFT` or retry queue event if Qdrant or Neo4j is offline.

---

## Layer 4 — Retrieval & Ranking Engine

**Phase Description**: Accepts a user query (like a task description or active file context), performs a three-stage hybrid retrieval operation (Semantic + Graph Expansion + Cross-Encoder Re-ranking), and packs the output into a token-budgeted context block.

```
                          [Developer Query / Task]
                                     |
                                     v
                          +--------------------+
                          | Phase 1: Qdrant    |
                          | (Semantic Search)  |
                          +---------+----------+
                                    | Top-K Candidate IDs
                                    v
                          +--------------------+
                          | Phase 2: Neo4j     |
                          | (Graph Expansion)  |
                          +---------+----------+
                                    | Expanded Candidates Set
                                    v
                          +--------------------+
                          | Phase 3: Voyage    |
                          | (Cross-Reranking)  |
                          +---------+----------+
                                    | Ranked Memories
                                    v
                          +--------------------+
                          |   Context Packer   |
                          |  (Token Budget)    |
                          +---------+----------+
                                    |
                                    v
                          [Formatted Context Block]
```

### Sub-Phases Specification

#### 1. Phase 1 — Semantic Search (Qdrant)
*   **What it does**: Embeds the input query with Voyage Code 3, queries Qdrant with `project_id` constraints, and pulls top-K (default: 20) candidate memory objects.
*   **Inputs**:
    *   `query` (str): Task description/code snippet.
    *   `project_id` (str)
    *   `filters` (Dict - optional component/file constraints)
*   **Outputs**:
    *   `semantic_candidates` (List[UUID]): Memory IDs.
*   **Interconnections & Data Flow**: Connected from **Layer 5, Sub-Phase 4 (MCP Tool: recall_context)**, taking the target search `query` and `project_id` filters. It embeds the query text using Voyage Code 3 and queries **Layer 3, Sub-Phase 2 (Qdrant Cloud Vector Layer)**. It outputs `semantic_candidates` (List[UUID]) to **Layer 4, Sub-Phase 2 (Phase 2 — Graph Expansion)**.

#### 2. Phase 2 — Graph Expansion (Neo4j)
*   **What it does**: Traverses Neo4j 1-2 hops away from semantic candidate IDs. Gathers related constraints, failed attempts, and historical dependencies.
*   **Inputs**:
    *   `semantic_candidates` (List[UUID])
*   **Outputs**:
    *   `expanded_candidates` (List[MemoryObject]): Merged list of semantic + causally connected memory nodes.
*   **Interconnections & Data Flow**: Connected from **Layer 4, Sub-Phase 1 (Semantic Search)**, taking `semantic_candidates` (List[UUID]). It queries **Layer 3, Sub-Phase 3 (Neo4j Aura Graph Layer)** to traverse related nodes. It then queries **Layer 3, Sub-Phase 1 (PostgreSQL)** to resolve the full payloads for all candidate UUIDs, passing `expanded_candidates` (List[MemoryObject]) to **Layer 4, Sub-Phase 3 (Phase 3 — Re-ranking)**.

#### 3. Phase 3 — Re-ranking (Voyage rerank-2.5-lite)
*   **What it does**: Submits `(query, memory_content)` pairs to Voyage rerank-2.5-lite. Adjusts scoring dynamically using recency (decay factor) and agent extraction confidence.
*   **Inputs**:
    *   `query` (str)
    *   `expanded_candidates` (List[MemoryObject])
*   **Outputs**:
    *   `ranked_memories` (List[Tuple[MemoryObject, float]]): Sorted list of memories with composite scores.
*   **Interconnections & Data Flow**: Connected from **Layer 4, Sub-Phase 2 (Graph Expansion)**, taking the merged list of `expanded_candidates` (List[MemoryObject]). It calls the Voyage rerank API with the original query string. It outputs the composite-scored, sorted `ranked_memories` list to **Layer 4, Sub-Phase 4 (Context Packer & Token Budgeting)**.

#### 4. Context Packer & Token Budgeting
*   **What it does**: Iterates through the ranked list of memories, formats them into a clean string block, tracks estimated token length, and stops once the budget limit is reached.
*   **Inputs**:
    *   `ranked_memories` (List[MemoryObject])
    *   `max_tokens` (int): Configurable token limit (default: 2000).
*   **Outputs**:
    *   `context_block` (str): Formatted memory context.
*   **Interconnections & Data Flow**: Connected from **Layer 4, Sub-Phase 3 (Re-ranking)**, taking the sorted `ranked_memories` list and `max_tokens` constraints. It outputs the compiled `context_block` (str) back to **Layer 5, Sub-Phase 4 (MCP Tool: recall_context)**.

#### 5. Search Fallback Handler
*   **What it does**: If Qdrant or Neo4j are unreachable, seamlessly downgrades search to a local PostgreSQL full-text search fallback.
*   **Inputs**:
    *   `query` (str), `project_id` (str), `error_state` (Enum)
*   **Outputs**:
    *   `fallback_candidates` (List[MemoryObject])
*   **Interconnections & Data Flow**: Triggered by system errors in **Layer 4, Sub-Phase 1 (Qdrant)** or **Layer 4, Sub-Phase 2 (Neo4j)**. It reads search matches directly from **Layer 3, Sub-Phase 1 (PostgreSQL)** using full-text index queries and routes the resolved list of `fallback_candidates` to the **Layer 4, Sub-Phase 4 (Context Packer)**.

---

### Layer 4 Implementation Todo Checklist
- [ ] **Qdrant Semantic Querying**: Implement search function with Voyage embedding input and metadata payload filters.
- [ ] **Neo4j Graph Expansion Query**:
  - [ ] Write Cypher script to traverse paths: `(Memory)-[:INFLUENCED|REJECTED_IN_FAVOR_OF|FIXES]-(RelatedMemory)`.
  - [ ] De-duplicate expansion results against initial semantic candidates.
- [ ] **Voyage Reranker integration**:
  - [ ] Write connector for Voyage `rerank-2.5-lite` endpoint.
  - [ ] Implement composite scoring function: $Score = (Reranker \times 0.7) + (Confidence \times 0.15) + (Recency \times 0.15)$ where recency represents exponential time decay from `created_at`.
- [ ] **PostgreSQL Full-Text Search Fallback**: Implement database `tsvector` query fallback matching `title` and `content`.
- [ ] **Token-Aware Context Packer**: Build utility using `tiktoken` to estimate context token bounds and format output text.
- [ ] **Retrieval Coordinator Orchestrator**: Write wrapper to run retrieval phases in sequence.

---

## Layer 5 — MCP Server (Tool Interface)

**Phase Description**: Serves as the public interface for the memory system. Exposes tools using FastMCP, manages single/multi-user connection protocols, auto-detects active projects via git remote URL mapping, and writes invocation logs to database tables.

### MCP Interface Connections

```
      [AI Coding Assistant] (Cursor / Claude Desktop / Windsurf)
                                   |
                                   v  (JSON-RPC over stdio / SSE)
                      +--------------------------+
                      |      FastMCP Server      |
                      +------------+-------------+
                                   |
      +----------------------------+----------------------------+
      |                            |                            |
      v                            v                            v
[remember_session]          [recall_context]            [Auxiliary Tools]
 (Runs Layer 1 & 2)          (Runs Layer 4)          (list_decisions, etc)
      |                            |                            |
      +----------------------------+----------------------------+
                                   |
                                   v
                      +--------------------------+
                      | PostgreSQL Audit Logger  |
                      +--------------------------+
```

### Sub-Phases Specification

#### 1. FastMCP Server Skeleton & Environment
*   **What it does**: Setup the FastMCP wrapper instance. Configures environment variables (`REMNANT_DB_URL`, `REMNANT_QDRANT_URL`, `REMNANT_NEO4J_URL`) and handles the stdio/SSE transport selection.
*   **Inputs**: Environment parameters.
*   **Outputs**: Initialized MCP server daemon.
*   **Interconnections & Data Flow**: Serves as the networking front-end. It receives JSON-RPC requests from the client IDE and maps them to the respective tool executors. It queries and writes configurations to environment variables.

#### 2. Project Detection Node
*   **What it does**: Executes git shell commands inside the active workspace directory to retrieve the remote origin URL, maps it to a canonical key to identify the `project_id`.
*   **Inputs**:
    *   `project_root` (str): Working directory path.
*   **Outputs**:
    *   `project_id` (UUID)
*   **Interconnections & Data Flow**: Connected from the MCP server startup lifecycle. It runs git checks on the workspace root and queries/inserts project profiles in **Layer 3, Sub-Phase 1 (PostgreSQL Relational Layer)**. It returns the resolved project UUID (`project_id`) for use in all subsequent tool calls.

#### 3. MCP Tool: remember_session
*   **What it does**: Ingests chat transcripts and commit metrics, then executes Layer 1 parsing and Layer 2 agent graph pipelines.
*   **Inputs**:
    *   `project_id` (str)
    *   `chat_transcript` (str - optional)
    *   `commit_sha` (str - optional)
    *   `session_notes` (str - optional)
*   **Outputs**:
    *   `memories_extracted` (int), `session_id` (str), `summary` (str).
*   **Interconnections & Data Flow**: Triggered by the IDE client JSON-RPC call. It coordinates the execution flow: it feeds workspace variables to **Layer 1, Sub-Phase 1 (Git Parser)** and **Layer 1, Sub-Phase 2 (Chat/Log Normalizer)**, receives the resulting `final_artifacts` and runs the **Layer 2 (LangGraph Agent)** graph. It reads the final `storage_results` state from Layer 2 to return the operation summary back to the client.

#### 4. MCP Tool: recall_context
*   **What it does**: Triggers Layer 4 Hybrid Retrieval pipeline and returns a formatted context string.
*   **Inputs**:
    *   `project_id` (str), `query` (str), `component` (str - optional), `file_path` (str - optional), `max_tokens` (int).
*   **Outputs**:
    *   `context_block` (str).
*   **Interconnections & Data Flow**: Triggered by the IDE client JSON-RPC call. It parses the incoming query and filters, invokes **Layer 4, Sub-Phase 1 (Semantic Search)** with the arguments, and receives the resulting `context_block` (str) which it returns to the IDE client.

#### 5. Auxiliary Tools (`list_decisions`, `get_failed_approaches`, `mark_superseded`)
*   **What it does**: Exposes utility actions to retrieve metadata lists or set soft-deprecations (linking obsolete memories to new ones using `superseded_by`).
*   **Inputs**: Tool-specific parameters (e.g. `memory_id` and `reason`).
*   **Outputs**: Operation status.
*   **Interconnections & Data Flow**: Triggered by the IDE client JSON-RPC call. They directly execute reads and writes (e.g. updating `superseded_by` columns or fetching `FAILED_APPROACH` rows) on **Layer 3, Sub-Phase 1 (PostgreSQL Relational Layer)**, returning JSON summaries back to the client.

---

### Layer 5 Implementation Todo Checklist
- [ ] **Initialize FastMCP Framework**: Write server entrypoint `remnant/mcp_server.py`.
- [ ] **Git Remote Resolution Hook**:
  - [ ] Implement helper to execute `git remote get-url origin`.
  - [ ] Normalize URLs (e.g. `git@github.com:org/repo.git` vs `https://github.com/org/repo`) into a unique key.
- [ ] **Claude & Cursor Configurations**: Create `mcp_config.json` examples for integrating the server command with standard clients.
- [ ] **Audit Logger Interceptor**: Build middleware to capture MCP requests and write details to the database.
- [ ] **Implement remember_session Tool**:
  - [ ] Parse parameters.
  - [ ] Coordinate Layer 1 ingestion and run the LangGraph Extraction pipeline.
- [ ] **Implement recall_context Tool**: Map query criteria to Layer 4 engine.
- [ ] **Implement list_decisions Tool**: Build relational query parser to list architectural decisions.
- [ ] **Implement get_failed_approaches Tool**: Build search queries filtering specifically for `FAILED_APPROACH` memories.
- [ ] **Implement mark_superseded Tool**: Implement updating `is_superseded` and `superseded_by` columns inside the database.

---

## Interconnection Data Flow Matrix

| Source Sub-Phase | Target Sub-Phase | Data Passed | Transport Method |
|---|---|---|---|
| **L1 Git Parser** | **L1 Redactor** | Raw diffs, commit logs | In-Memory Python Strings |
| **L1 Normalizer** | **L1 Grouper** | Sanitized raw text artifacts | List of Dict payloads |
| **L1 Grouper** | **L2 Router** | Grouped, hashed `ArtifactObject` list | LangGraph input State |
| **L2 Router** | **L2 Extractors** | Targeted `ArtifactObject` subsets | LangGraph Send API |
| **L2 Extractors** | **L2 Entity Resolver** | Fuzzy, unverified memory fields | LangGraph state `raw_memories` |
| **L2 Entity Resolver** | **L2 Relationship Mapper** | Memories with valid file paths | LangGraph state `resolved_memories` |
| **L2 Relationship Mapper**| **L2 Validator** | Connected memory node representations | LangGraph state `relationships` |
| **L2 Validator (Retry)** | **L2 Extractors** | Feedback errors, incremented retry | LangGraph conditional edge |
| **L2 Validator (Success)**| **L3 Database Fan-Out** | Validated `MemoryObject` arrays | Python variables to Writer |
| **L3 PostgreSQL Write** | **L3 Qdrant & Neo4j** | Canonical DB UUIDs | Shared memory reference mapping |
| **L5 MCP Entrypoint** | **L4 Retrieval Engine** | Target query, filters | Function arguments |
| **L4 Retrieval Engine** | **L5 Client Output** | Token-budgeted context string | JSON-RPC response block |
