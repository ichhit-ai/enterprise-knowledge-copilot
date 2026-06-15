# 📘 The Ultimate Guide: Enterprise Knowledge Copilot & Hybrid MCP Architecture

This guide is written to help you thoroughly understand every layer of your project:
1. **Your Current Project**: How the files interact, what the code does, and why it is designed this way.
2. **Elasticsearch & MCP**: What these technologies are and how they operate.
3. **The Hybrid Architecture**: How we merge your project's code with Elasticsearch and MCP to build a production-grade system.
4. **Hackathon Mastery**: Key takeaways and talking points to impress the NASSCOM judges.

---

## 📂 PART 1: Your Current Project Explained in Detail

Your project, the **Enterprise Knowledge Copilot**, is an AI agent designed to help employees troubleshoot IT issues, read internal company policies, and file support tickets without duplicate spam. It runs **100% locally** (using LLaMA 3.2 via Ollama) to guarantee absolute data privacy.

The codebase is split into four distinct phases: **Ingestion**, **Retrieval**, **Agent Orchestration**, and **Frontend UI**.

```text
========================================================================================
[1. INGESTION] ──► [2. RETRIEVAL] ───────────────► [3. AGENT ORCHESTRATION] ──► [4. UI]
Reads raw files    Searches ChromaDB & BM25        LangGraph state machine      Streamlit
Redacts PII        Merges rankings via RRF         Routes query to tools        Chat interface
Builds 3 indices   Reranks via Cross-Encoder       Generates response + metrics Dashboard
========================================================================================
```

---

### Phase 1: Ingestion (`src/ingest.py`)
This script builds the "brain" of your system. It parses unstructured handbooks (`.txt`), structured tickets (`.csv`), and org charts (`.csv`), processes them, and outputs three separate search indices to the `.index/` folder.

#### 1. Zero-Leak PII Redaction
Before any data is written to a database, the `redact()` function scans the text:
* It uses **Regular Expressions (Regex)** to find and replace email addresses and phone numbers.
* It uses **spaCy NER (Named Entity Recognition)** with the `en_core_web_sm` model to find names of people (`PERSON`).
* **The Length Sort Trick**: To prevent partial redactions (like turning *"Dr. Sarah Jenkins"* into *"Dr. [REDACTED] Jenkins"*), the code sorts detected names by length and replaces the longest ones first, yielding *"Dr. [REDACTED_PERSON]"*.

#### 2. Text Chunking
Long text files (like `nexacorp_handbook.txt`) are split into smaller chunks:
* **Size**: 800 characters (~200 words).
* **Overlap**: 150 characters. The overlap ensures that if a sentence is split at the boundary of Chunk 1, the full sentence is preserved in Chunk 2.

#### 3. Building the Three Indices
* **ChromaDB**: A vector database. The text chunks are converted into 384-dimensional mathematical vectors using the HuggingFace `BAAI/bge-small-en-v1.5` embedding model. Vector database searches find *meaning* (semantic search).
* **BM25 Index**: A keyword search index. It tokenizes words and scores documents based on how often search words appear in them. It finds *exact matches* (e.g., error codes like `ERR-AUTH-9092`).
* **NetworkX Knowledge Graph**: A python relational graph. It parses the Org Chart CSV (triples of Subject → Relationship → Object) to map relationships, such as `Marcus Thompson ---[OWNS_SYSTEM]---> AUTH-GATEWAY`.

---

### Phase 2: Hybrid Retrieval (`src/retriever.py`)
When a user asks a question, the `Retriever` class finds the most relevant documents. It uses a **Three-Headed Search** to maximize accuracy.

```text
               [User Search Query]
                        │
                        ▼
               [Query Expansion] (Maps abbreviations: e.g. vpn ──► NEXAVPN)
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
   [ChromaDB]        [BM25]     [NetworkX Graph]
 (Semantic Search) (Keyword Search) (Relational Search)
         │              │              │
         └──────┬───────┘              ▼
                ▼                 [Graph Triples]
         [RRF Rank Fusion]        (Who owns what system)
                │
                ▼
      [Top-10 Candidates]
                │
                ▼
     [Cross-Encoder Reranker] (ms-marco-MiniLM-L-6-v2)
                │
                ▼
       [Top-5 Best Chunks]
```

#### 1. Query Expansion
If a user types *"why is my vpn slow?"*, the retriever expands the query to *"why is my vpn slow? NEXAVPN"*. It uses a lookup dictionary (`ABBREVIATION_MAP`) to translate abbreviations into exact system codes, ensuring higher retrieval success.

#### 2. Reciprocal Rank Fusion (RRF)
Vector search scores (Chroma) and keyword scores (BM25) are on completely different mathematical scales and cannot be directly added. 
* **RRF** solves this by ignoring the raw scores and focusing only on *rankings*.
* **Formula**: `RRF_Score = 1 / (60 + Rank_in_Vector_DB) + 1 / (60 + Rank_in_Keyword_DB)`.
* If a document is ranked #1 by both engines, it moves to the absolute top of the combined list.

#### 3. Cross-Encoder Reranking
RRF returns the top 10 candidates. Since our local LLM has a limited context window, we pass these 10 chunks through a **Cross-Encoder model** (`ms-marco-MiniLM-L-6-v2`).
* Unlike vector database searches which evaluate query and documents separately, a Cross-Encoder compares the query and document *together* in the transformer. It is highly precise and selects the absolute top 5 chunks.

---

### Phase 3: Agentic Orchestrator (`src/agent.py`)
This is the controller of your system built using **LangGraph**. It coordinates the workflow using a state machine.

```text
               [User Query]
                    │
                    ▼
             [Route Query Node] (Deterministic classification)
                    │
       ┌────────────┼────────────┬─────────────┐
       ▼            ▼            ▼             ▼
  [docs tool] [tickets tool] [multihop] [create ticket tool]
       │            │            │             │
       └────────────┼────────────┘             ▼
                    ▼                   [Duplicate Check]
             [Assess Context]                  │
             ├── Docs Found? ──► [Generate Answer]
             └── 0 Hits?     ──► [Smart Escalation] (Zero-LLM Fast Path)
```

#### 1. Deterministic Routing
Instead of letting a small 3B LLM route queries (which leads to mistakes), the agent uses a **fast Python function** with pre-defined keyword lists to route queries:
* Query contains "file/create ticket" ──► Routes to `tool_create_ticket`.
* Query contains "priority/status" ──► Routes to `tool_filtered_tickets`.
* Query contains "summarize/overview" ──► Routes to `tool_summarize`.

#### 2. The 6 Agent Tools
1. `tool_search_docs`: Queries handbooks and runbooks.
2. `tool_search_tickets`: Searches historical IT support cases.
3. `tool_filtered_tickets`: Uses metadata tags in Chroma to filter queries (e.g., searching specifically for `P1` tickets).
4. `tool_summarize`: Broader search (k=8) to summarize topics.
5. `tool_multihop`: Searches both handbook and ticket databases to answer complex troubleshooting questions.
6. `tool_create_ticket`: Files a ticket into `nexacorp_tickets.csv`.
   * **Duplicate Guardrail**: Before writing a ticket, it runs a semantic cosine similarity check (>0.75 similarity) against open tickets. If a similar issue is open, it blocks ticket creation to prevent duplicate spam.

#### 3. Smart Escalation (Zero-LLM Fast Path)
If a search returns **0 hits**, the agent bypasses the LLM entirely to save compute and eliminate hallucination. It traverses the `NetworkX` graph, finds the system owner, and returns: *"I couldn't find the answer. Please contact Marcus Thompson (m.thompson@nexacorp.com) who owns AUTH-GATEWAY."*

#### 4. Audit Logging
Every action, tool execution, and query metric is logged into `audit.jsonl` for compliance reviews.

---

### Phase 4: UI & Confidence Calibration (`src/app.py`)
A Streamlit interface that renders the chat, displays system health metrics (total chunks, graph size, last ingestion time), and calculates response confidence:
* **Confidence formula**: `40% retrieval score + 30% faithfulness + 30% context relevance`.
* **Faithfulness**: Calculates the overlap of words between the LLM's response and the retrieved documents. If the LLM generates facts not present in the documents, faithfulness drops.

---

## 🔌 PART 2: Understanding Elasticsearch & MCP (Model Context Protocol)

To understand how to upgrade the project, you must first understand what these two technologies do.

### What is Elasticsearch?
Elasticsearch is a dedicated, distributed search engine. Unlike SQLite or ChromaDB (which are small files stored locally on your hard drive), Elasticsearch is built to run on servers.
* **Unified Retrieval**: It handles vector embeddings (HNSW indexes), exact keyword searches (BM25), and metadata filtering inside a single database.
* **Sharding**: It splits a database into multiple "shards" across multiple servers, letting you search millions of documents instantly.
* **Security**: It has built-in Document-Level Security (DLS). You can configure it so that only managers can retrieve sensitive documents.

### What is Model Context Protocol (MCP)?
Created by Anthropic, MCP is a standardized API protocol that allows AI agents to talk to databases and services without custom integration code.
* Think of MCP like a **USB port** for AI. Instead of wiring custom cables for every phone and mouse, you use a standard USB port. MCP is a standard USB port for connecting LLMs to databases.
* **MCP Server**: Runs next to your database. It translates the database's custom APIs into standard JSON-RPC tools.
* **MCP Client**: Your Python agent. It queries the MCP server using standardized functions.

---

## 🤝 PART 3: The Hybrid Architecture (The Best of Both Worlds)

If you combine the **Normal Flow** (your current codebase) with **Elasticsearch + MCP**, you get the ultimate enterprise architecture.

### Why do we combine them?
A local 3B LLM is too small to handle complex database tools directly. If you tell LLaMA 3.2 to write a complex Elasticsearch query, it will crash.
In the **Hybrid Architecture**:
1. **Python** manages the user input, redacts PII, and decides which tool to use (Normal Flow).
2. **Python** acts as the **MCP Client** and queries the **Elasticsearch MCP Server** using standard protocol queries.
3. The **MCP Server** queries **Elasticsearch**, which runs the vector and keyword search, merges the results natively, and returns the top chunks.
4. **Python** receives the chunks, calculates metrics, formats a clean prompt, and asks the **local 3B LLM** to write the final answer.

```text
[User Query]
     │
     ▼
[LangGraph Agent (Python)] ◄─── Normal Flow (Strict Guardrails)
     │
     ├── 1. Redact PII (spaCy NER)
     ├── 2. Determine routing (Python keyword check)
     │
     ▼
[MCP Client Connection] ◄─────── Handshake over JSON-RPC protocol
     │
     ▼ (JSON-RPC: call_tool("search", {"query": "NEXAVPN issues"}))
[Elasticsearch MCP Server]
     │
     ▼ (Native Vector + Lexical Search + RRF)
[Elasticsearch Index] ◄──────── Elasticsearch Database
     │
     ▼ (Returns top-5 fused document chunks)
[LangGraph Agent (Python)]
     │
     ├── 3. Calculate Faithfulness & Relevance metrics in Python
     │
     ▼ (Injected Context Prompt)
[Ollama LLM (LLaMA 3.2)] ◄───── Local LLM (Only used to write the final answer)
     │
     ▼
[Streamlit UI Chat]
```

---

### Code-Level Comparison: How the Retrieval Changes

Here is how your `src/retriever.py` code changes between the current local flow and the Hybrid MCP flow.

#### Current Local Code Flow (Custom Python Retrieval)
```python
# In your current retriever.py, you manually run everything:
class Retriever:
    def __init__(self):
        self.chroma = Chroma(...)  # Load local Chroma vector DB
        self.bm25 = load_pickle("bm25.pkl")  # Load memory-based BM25
        
    def search_docs(self, query):
        # 1. Manually query Chroma
        semantic_results = self.chroma.similarity_search(query)
        # 2. Manually query BM25 list
        keyword_results = self.bm25.get_scores(query.split())
        # 3. Manually write code to fuse rankings
        fused_docs = self._rrf_fuse(semantic_results, keyword_results)
        # 4. Manually run Cross-Encoder reranker
        reranked_docs = self._rerank(query, fused_docs)
        return reranked_docs
```

#### Hybrid MCP Code Flow (Python calls Elasticsearch MCP Server)
```python
# In the hybrid flow, your Python agent acts as an MCP Client:
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class HybridMCPRetriever:
    def __init__(self):
        # Define connection parameters to the Elasticsearch MCP Server
        self.server_params = StdioServerParameters(
            command="npx",
            args=["-y", "@elastic/mcp-server-elasticsearch"],
            env={"ELASTICSEARCH_URL": "http://localhost:9200", "API_KEY": "your-key"}
        )

    async def search_docs(self, query_text):
        # 1. Connect to the MCP Server
        async with stdio_client(self.server_params) as (read, write):
            async with ClientSession(read, write) as session:
                # 2. Initialize connection
                await session.initialize()
                
                # 3. Call the standardized search tool exposed by the MCP Server
                response = await session.call_tool(
                    name="search", 
                    arguments={
                        "index": "nexacorp_handbook",
                        "query": query_text,
                        "hybrid": True  # Tells Elasticsearch to do Vector + BM25 + RRF internally
                    }
                )
                
                # 4. Process the standard MCP output
                documents = self._parse_mcp_response(response)
                return documents
```
*Note: In the hybrid flow, the database cluster handles all vector, keyword, and RRF operations. Python code only handles the connection and final parsing, saving memory and processing time.*

---

## 🏆 PART 4: Hackathon Cheat-Sheet (Winning the NASSCOM Jury)

When presenting to NASSCOM judges, emphasize these design choices to prove your engineering maturity:

### 1. The "Hybrid Edge/Cloud" Pitch
* **The Pitch**: *"Our system is architected for dual-deployability. For local, offline edge workstations where privacy is critical, we run an 'Edge Sandbox' using in-process ChromaDB and localized BM25. For full enterprise scale, our agent connects directly to an **Elasticsearch cluster via the Model Context Protocol (MCP)**. This guarantees that our architecture scales to millions of records and supports enterprise security standards like Document-Level Security (DLS)."*

### 2. Why Model Context Protocol (MCP)?
* **The Pitch**: *"We used MCP to prevent vendor lock-in. MCP is the new industry standard for Agentic integrations. Because our search cluster runs as an MCP Server, we can hot-swap our agent's brain (from local LLaMA to cloud Claude 3.5 or Azure OpenAI) or plug our database into other IDEs (like Cursor or VS Code) without rewriting any backend code."*

### 3. How do you handle PII in Production?
* **The Pitch**: *"In our local edge demo, we run PII redaction inside Python using spaCy NER. In our production Elasticsearch design, we move this to the **Elasticsearch Ingest Pipeline**. We use regex and local NER processing nodes in the ingest pipeline to scrub names, emails, and phones from documents *before* they are indexed on disk, ensuring compliant zero-PII vector and text storage."*

### 4. How does the system handle high-concurrency writes?
* **The Pitch**: *"While local files (SQLite/CSV) fail under multiple concurrent users due to database write locks, our production target architecture (Elasticsearch) utilizes distributed lock management, document routing, and sharding to handle thousands of concurrent queries and ticket submissions without latency spikes or lock collisions."*
