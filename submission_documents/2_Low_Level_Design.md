# Enterprise Knowledge Copilot — Low Level Design (LLD)

> **Project:** Enterprise Knowledge Copilot  
> **Total Modules:** 4 Core Python Files + 2 Evaluation Scripts  
> **Lines of Code:** ~1,350+ (production logic, excluding tests)

---

## 1. Module Dependency Graph

The system supports dual-mode operation (Local Sandbox vs. Production Elasticsearch) and is structured as follows:

```
┌──────────────────────────────────────────────────────────────┐
│  app.py (Presentation)                                       │
│  - Streamlit session management / Chat UI rendering          │
│  - Calls build_agent() from agent.py                         │
└──────────────────────┬───────────────────────────────────────┘
                       │ imports
                       ▼
┌──────────────────────────────────────────────────────────────┐
│  agent.py (Orchestration)                                    │
│  - LangGraph StateGraph definition                           │
│  - 6 tool nodes + router + escalation                        │
└──────────────────────┬───────────────────────────────────────┘
                       │ imports
                       ├───────────────────────────────────────┐
                       ▼                                       ▼
┌──────────────────────────────────────────────────────────────┐ ┌──────────────────────────────────────────────────────────────┐
│  retriever.py (Local Retrieval)                              │ │  retriever_mcp.py (Production Retrieval)                     │
│  - ChromaDB, BM25, NetworkX query execution                  │ │  - StdioServerParameters launcher                            │
│  - RRF fusion & Cross-Encoder reranking                      │ │  - Lock-free session connection pool                         │
│  - Embedding & similarity utilities                          │ │  - Elasticsearch queries via Node.js MCP Server              │
└──────────────────────────────────────────────────────────────┘ └──────────────────────────────────────────────────────────────┘
                       ▲ reads indices built by                                           ▲ queries database populated by
┌──────────────────────────────────────────────────────────────┐                                          │
│  ingest.py (Local ETL)                                       ├──────────────────────────────────────────┘
│  - PII redaction pipeline & chunking                         │ (via scripts/ingest_elasticsearch.py)
│  - Local index construction (Chroma, BM25, Graph)            │
└──────────────────────────────────────────────────────────────┘
```

**Key design principle**: Ingestion (local/production) is an offline task. The retrieval layer handles dual-mode database queries asynchronously. The agent orchestration is decoupled from physical data layers.

---

## 2. Module-Level Design

### 2.1 `src/ingest.py` — The ETL Engine (217 lines)

**Responsibility**: Reads raw CSV/TXT files, redacts PII, chunks text, and builds three independent search indices.

**Key Functions**:

| Function | Purpose | Implementation Detail |
|---|---|---|
| `redact(text)` | Strips PII from raw text | Regex for emails/phones → spaCy NER for PERSON entities → Sorts by string length (longest first) to prevent partial leaks |
| `chunk_text(text, size=800, overlap=150)` | Splits text into overlapping windows | Rolling window ensures no sentence is split without appearing in at least one complete chunk |
| `load_text_files(data_dir)` | Parses `.txt` handbook/runbook files | Tags each chunk with `source`, `chunk_index`, `type=handbook`, and detected `system` name |
| `load_csv_files(data_dir)` | Parses `.csv` ticket/org files | Extracts structured metadata: `priority`, `status`, `error_code`, `ticket_id`, `created_at` |
| `build_graph_from_csv(data_dir)` | Constructs the NetworkX DiGraph | Loads explicit triples from org chart (entity→relationship→target) + discovers co-mentioned entities via NER |
| `build_index()` | Master orchestrator | Calls all loaders, builds ChromaDB (vector), BM25 (keyword), and NetworkX (graph) indices. Records ingest timestamp. |

**Data Flow**:
```
[Local Mode Data Flow]
Raw Files → redact() → chunk_text() → [Document objects with metadata]
                                              ↓
                                    ┌─────────┼──────────┐
                                    ▼         ▼          ▼
                              ChromaDB    BM25 Index   NetworkX
                              (.index/    (.index/     (.index/
                               chroma/)    bm25.pkl)    graph.pkl)

[Production Mode Data Flow]
Raw Files → redact() → chunk_text() → [Document objects with metadata]
                                              ↓
                                    ┌─────────┴──────────┐
                                    ▼                    ▼
                              Elasticsearch Index      NetworkX Graph
                              (http://localhost:9200)  (.index/graph.pkl)
```

### 2.2 `src/retriever.py` — The Three-Headed Search Engine (231 lines)

**Responsibility**: Executes queries against all three indices, fuses results with RRF, and reranks with a Cross-Encoder.

**Key Functions**:

| Function | Purpose | Implementation Detail |
|---|---|---|
| `expand_query(query)` | Abbreviation resolution | Maps "vpn" → "NEXAVPN", "ci" → "BUILDPIPE-CI", "password" → "credential password reset" via a 24-entry lookup table |
| `search_semantic(query, k, filter_dict)` | ChromaDB vector search | Uses `bge-small-en-v1.5` embeddings (33MB, CPU-friendly). Supports metadata filtering via ChromaDB's `$and` operator |
| `search_keyword(query, k, source_filter)` | BM25 sparse retrieval | Tokenizes query, scores against pre-built BM25 corpus, filters by source file |
| `search_graph(entities)` | NetworkX graph traversal | Finds matching nodes, traverses both outgoing and incoming edges, deduplicates, returns up to 20 triples |
| `_rrf_fuse(query, sem_results, kw_results, k)` | Reciprocal Rank Fusion | `score = Σ 1/(60 + rank)` — normalizes across different scoring scales. Feeds top 2k candidates to Cross-Encoder |
| `_rerank(query, docs, k)` | Cross-Encoder reranking | Loads `ms-marco-MiniLM-L-6-v2` (lazy-loaded). Pushes (query, doc) pairs through transformer together. Returns top-k by relevance |
| `compute_semantic_similarity(query, answer)` | Cosine similarity | Embeds both texts, computes vector dot product. Used for deduplication and evaluation metrics |
| `compute_context_relevance(query, documents)` | Average chunk similarity | Embeds query + each retrieved chunk, averages cosine similarities. Measures retrieval quality |

**Retrieval Pipeline (per query)**:
```
Query → expand_query() → [search_semantic() + search_keyword()] 
                                    ↓
                            _rrf_fuse() → Top 10 merged
                                    ↓
                            _rerank() → Top 5 final (Cross-Encoder)
```

### 2.2.b `src/retriever_mcp.py` — The MCP Elasticsearch Gateway (115 lines)

**Responsibility**: Connects to the Model Context Protocol (MCP) server over stdin/stdout IPC to delegate searches to the containerized Elasticsearch cluster.

**Key Functions**:

| Function | Purpose | Implementation Detail |
|---|---|---|
| `get_session_info()` | Thread-safe connection pooling | Uses a lock-free fast-path to bypass global locks if the MCP server session is already warm, eliminating IPC latency bottlenecking |
| `search_docs(query, k)` | Remote search tool execution | Invokes `search_docs` on the Node.js MCP server, which performs Elasticsearch dense vector search, BM25 text match, and RRF fusion natively |
| `add_ticket_doc(ticket_str)` | Remote document ingestion | Pushes new customer support tickets to Elasticsearch asynchronously for real-time indexing |

### 2.3 `src/agent.py` — The Agentic Brain (600 lines)

**Responsibility**: Defines the LangGraph state machine, implements 6 tools, handles routing, escalation, metric computation, and audit logging.

**State Schema** (TypedDict):

| Field | Type | Purpose |
|---|---|---|
| `question` | `str` | The user's natural language query |
| `entities` | `list[str]` | Extracted system names, error codes, and NER entities |
| `route` | `str` | Classified tool route: docs / tickets / summarize / filtered / create / multihop |
| `documents` | `list` | Retrieved LangChain Document objects |
| `graph_context` | `str` | Knowledge Graph relationships as formatted text |
| `retrieval_score` | `float` | Average RRF score from retrieval |
| `answer` | `str` | The LLM's generated response |
| `citations` | `list[dict]` | Source documents with snippets for provenance |
| `tool_used` | `str` | Human-readable label of the selected tool |
| `faithfulness` | `float` | Token overlap between answer and source context |
| `semantic_similarity` | `float` | Cosine similarity between query and answer embeddings |
| `context_relevance` | `float` | Average cosine similarity between query and retrieved chunks |
| `confidence` | `float` | Weighted composite score displayed to user |
| `reasoning_trace` | `list[str]` | Step-by-step log of the agent's internal decisions |
| `history` | `list[dict]` | Previous conversation turns for multi-turn context |
| `role` | `str` | User's access tier (Employee / Manager / IT Admin) |

**The 6 Tools**:

1. **`tool_search_docs`**: Queries handbook + runbook via hybrid search. Also traverses the Knowledge Graph for any detected entities. Returns up to 5 reranked documents.
2. **`tool_search_tickets`**: Searches historical ticket data. Enriches results with graph relationships for system-owner mapping.
3. **`tool_filtered_tickets`**: Performs metadata-aware searches. Extracts priority level (P1-P4) and system name from the query, applies them as ChromaDB filter conditions.
4. **`tool_summarize`**: Broad search across ALL data sources (k=8) for overview-style questions.
5. **`tool_multihop`**: Decomposes complex questions into three sub-tasks (Hop 1: handbook definitions → Hop 2: historical tickets → Hop 3: graph relationships), then synthesizes all results.
6. **`tool_create_ticket`**: Before creating a ticket, searches for similar open tickets. If cosine similarity > 0.75, blocks creation and shows the duplicate. Otherwise, writes a new row to the CSV with auto-generated ticket ID, priority, and error code.

### 2.4 `src/app.py` — The Presentation Layer (311 lines)

**Responsibility**: Streamlit session state management, UI rendering, and user interaction handling.

**Key Components**:

| Component | Implementation |
|---|---|
| **Custom CSS Theme** | Dark gradient background, glassmorphism cards, Inter font, color-coded metric pills |
| **Sidebar Dashboard** | Reads `bm25.pkl`, `graph.pkl`, and `ingest_meta.pkl` to display live system health stats |
| **Role Selector** | Dropdown for Employee/Manager/IT Admin — controls data access tier |
| **Onboarding Mode** | 10 curated questions in expandable cards with on-demand answer generation |
| **Chat Rendering** | Displays tool badges, confidence badges, metric pills, citation expanders, and reasoning traces |
| **Feedback System** | Thumbs up/down buttons per response → logged to `feedback.jsonl` |
| **Suggestion Chips** | 6 pre-built example queries shown when chat history is empty |

---

## 3. Error Handling & Resilience

| Scenario | Handling Strategy |
|---|---|
| Cross-Encoder fails to load | Graceful fallback: `_cross_encoder = False`, system uses RRF-only ranking |
| Zero retrieval results | Agent bypasses LLM entirely → Smart Escalation with Knowledge Graph contacts |
| Malformed CSV rows | Robust DictReader parsing with `.strip().strip('"')` on all fields |
| Duplicate ticket creation | Cosine similarity check (>0.75 threshold) blocks duplicates before CSV write |
| spaCy model not installed | Script fails fast with a clear import error, not a silent degradation |
| Ollama server offline | LangChain raises a connection error surfaced in the Streamlit UI |

---

## 4. File-Level Dependency Map

```
requirements.txt
├── langchain-community     # ChromaDB integration
├── langchain-huggingface   # HuggingFace embeddings
├── langchain-ollama        # Local LLM connection
├── langgraph               # State machine framework
├── chromadb                # Vector database
├── rank-bm25               # BM25 keyword search
├── networkx                # Knowledge graph
├── sentence-transformers   # Cross-encoder reranker
├── spacy                   # NER for PII redaction
├── streamlit               # Frontend UI
└── numpy                   # Vector math
```
