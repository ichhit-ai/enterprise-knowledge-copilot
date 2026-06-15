# Enterprise Knowledge Copilot — Open Source Tools & Libraries

> **Project:** Enterprise Knowledge Copilot  
> **Philosophy:** 100% open-source stack — zero vendor lock-in, zero cloud dependencies, complete data sovereignty.

---

## Core Technology Stack

| Library / Tool | Version | Role in Architecture | Why This One? |
|---|---|---|---|
| **Ollama** | Latest | Local LLM Runtime | Hosts LLaMA 3.2 (3B) entirely offline. No GPU required. Runs on standard corporate hardware with 8GB RAM. |
| **LangGraph** | 0.2+ | Agent Orchestration | State machine approach enables deterministic routing, conditional branching (escalation on 0 hits), and multi-turn memory — superior to simple chain-based architectures. |
| **ChromaDB** | 0.4+ | Vector Database | Zero-config, file-based, no server process needed. Perfect for local deployment. Supports metadata filtering with `$and` operators for SQL-like queries. |
| **Elasticsearch** | 8.x | Production Database | Distributed search and analytics engine. Used in Production Mode to offload vector similarity, BM25 keyword, and metadata-filtered search from Python RAM. |
| **Model Context Protocol (MCP)** | Latest | Database Gateway | Open standard protocol connecting the FastAPI backend to the Elasticsearch container via a lock-free Node.js proxy server. |
| **rank-bm25** | 0.2+ | Keyword Search (BM25Okapi) | Industry-standard sparse retrieval algorithm. Excels at exact-match queries (error codes, ticket IDs) where dense vectors fail. Pure Python, no external dependencies. |
| **NetworkX** | 3.0+ | Knowledge Graph | Pure Python directed graph library. Enables relational traversal (system → owner lookups) without requiring a graph database server like Neo4j. |
| **spaCy** | 3.7+ | PII Redaction (NER) | Offline Named Entity Recognition using `en_core_web_sm` model. Detects PERSON entities for name redaction. Fast inference, no cloud API calls. |
| **SentenceTransformers** | 2.0+ | Embeddings + Cross-Encoder | Provides both the embedding model (`BAAI/bge-small-en-v1.5`, 33MB) for vectorization and the Cross-Encoder (`ms-marco-MiniLM-L-6-v2`) for precision reranking. |
| **Streamlit** | 1.30+ | Frontend UI | Rapid prototyping framework for data apps. Provides chat interface, session state management, custom CSS injection, and sidebar dashboards with minimal code. |
| **NumPy** | 1.24+ | Vector Mathematics | Cosine similarity computation, embedding array operations, and score normalization for RRF fusion and evaluation metrics. |

---

## Embedding & Reranking Models

| Model | Source | Parameters | Size | Purpose |
|---|---|---|---|---|
| `BAAI/bge-small-en-v1.5` | HuggingFace | 33M | 33 MB | Document and query embedding. Top-tier performance for its size on MTEB benchmark. Runs entirely on CPU. |
| `ms-marco-MiniLM-L-6-v2` | HuggingFace | 22M | 22 MB | Cross-Encoder reranker. Trained on MS MARCO passage ranking dataset. Processes (query, document) pairs together through a transformer for maximum relevance precision. |
| `LLaMA 3.2` | Meta via Ollama | 3B | ~2 GB | Local language model for answer generation. Chosen as optimal balance between quality and CPU/RAM requirements. |
| `en_core_web_sm` | spaCy | — | 12 MB | NER model for detecting PERSON, ORG, PRODUCT, GPE entities. Used in PII redaction and entity extraction pipelines. |

---

## Why 100% Open Source?

1. **Data Sovereignty**: Enterprise data containing employee PII, system credentials, and internal procedures must never leave the organization's infrastructure. Every component runs locally.
2. **Zero Vendor Lock-in**: No dependency on OpenAI, Anthropic, Google, or any commercial API. The entire stack can be audited, forked, and modified.
3. **Reproducibility**: Open weights and open code mean the system behaves identically across deployments. No API version changes or model deprecations.
4. **Cost**: Zero ongoing API costs. After initial setup, the system runs indefinitely on local hardware at zero marginal cost per query.
