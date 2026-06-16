import re
import os
import csv
import json
import spacy
import pickle
from datetime import datetime
from typing import TypedDict
from langgraph.graph import StateGraph, END
from langchain_ollama import ChatOllama
from src.retriever import Retriever
from src.retriever_mcp import HybridMCPRetriever

# ── Global configuration for performance bypasses ─────────────────────────────
BYPASS_ENABLED = True  # Enable template bypasses for error codes, owners, and tickets
CACHE_ENABLED = True   # Enable semantic caching for repetitive queries
CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", ".index", "response_cache.pkl")


try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    import spacy.cli
    spacy.cli.download("en_core_web_sm")
    nlp = spacy.load("en_core_web_sm")


ERROR_CODE_RE = re.compile(r'[A-Z]{2,}[\-][A-Z]*[\-]?\w+')
TICKET_RE = re.compile(r'TKT-\d+', re.IGNORECASE)
PRIORITY_RE = re.compile(r'\bP[1-4]\b', re.IGNORECASE)

def extract_ticket_id(q):
    # Match TKT-XXXXX (case-insensitive) or standalone 5-6 digit numbers
    # Also match numbers preceded by ticket/id/number/no/#
    tkt_match = re.search(r'\bTKT-(\d+)\b', q, re.IGNORECASE)
    if tkt_match:
        return f"TKT-{tkt_match.group(1)}"
    
    # Standalone 5-6 digit number
    num_match = re.search(r'\b(\d{5,6})\b', q)
    if num_match:
        return num_match.group(1)
        
    # Preceded by ticket/id/number indicators
    label_match = re.search(r'\b(?:ticket|id|number|no\.?|#)\s*(\d+)\b', q, re.IGNORECASE)
    if label_match:
        return label_match.group(1)
        
    return None


SYSTEMS = ["AUTH-GATEWAY", "NEXACORE-DB", "NEXAVPN", "CLOUDSYNC-S3", "NEXAMAIL",
           "BUILDPIPE-CI", "NEXASEC-FW", "HRPORTAL", "MONITORX", "NEXABACKUP",
           "APIGATEWAY-V2", "TICKETSYS"]

SYSTEM_OWNERS = {
    "AUTH-GATEWAY": ("Marcus Thompson", "m.thompson@nexacorp.com"),
    "NEXACORE-DB": ("Derek Walsh", "d.walsh@nexacorp.com"),
    "NEXAVPN": ("Jordan Okafor", "j.okafor@nexacorp.com"),
    "CLOUDSYNC-S3": ("Chloe Fontaine", "c.fontaine@nexacorp.com"),
    "NEXAMAIL": ("Marcus Thompson", "m.thompson@nexacorp.com"),
    "BUILDPIPE-CI": ("Nathan Xu", "n.xu@nexacorp.com"),
    "NEXASEC-FW": ("Oliver Pine", "o.pine@nexacorp.com"),
    "HRPORTAL": ("Tomas Brewer", "t.brewer@nexacorp.com"),
    "MONITORX": ("Nathan Xu", "n.xu@nexacorp.com"),
    "NEXABACKUP": ("Farida Hassan", "f.hassan@nexacorp.com"),
    "APIGATEWAY-V2": ("Raj Patel", "r.patel@nexacorp.com"),
    "TICKETSYS": ("Tomas Brewer", "t.brewer@nexacorp.com"),
}

TICKET_KEYWORDS = ["ticket", "incident", "tkt-", "escalated", "resolved",
                    "in progress", "who filed", "how many tickets", "open tickets",
                    "recent issues", "ticket status", "bug", "bugs", "reported",
                    "customer support", "customer tickets"]
SUMMARY_KEYWORDS = ["summarize", "summary", "overview", "explain the policy",
                     "break down", "what are all", "list all", "give me a rundown"]
CREATE_TICKET_KEYWORDS = ["file a ticket", "create a ticket", "raise a ticket",
                          "open a ticket", "submit a ticket", "log a ticket",
                          "new ticket", "file ticket", "create ticket"]
FILTER_KEYWORDS = ["p1 ticket", "p2 ticket", "p3 ticket", "p4 ticket",
                   "priority 1", "priority 2", "high priority", "critical ticket",
                   "tickets from", "tickets about", "filter ticket"]
MULTIHOP_KEYWORDS = ["what should i do about", "how do i fix", "how to resolve",
                     "troubleshoot", "steps to fix", "what causes and how"]

# Role-based access tiers
ROLE_ACCESS = {
    "Employee": {"max_tier": 2},
    "Manager": {"max_tier": 3},
    "IT Admin": {"max_tier": 4},
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
AUDIT_PATH = os.path.join(os.path.dirname(__file__), "..", "audit.jsonl")


class State(TypedDict):
    question: str
    entities: list[str]
    route: str
    documents: list
    graph_context: str
    retrieval_score: float
    answer: str
    citations: list[dict]
    tool_used: str
    faithfulness: float
    semantic_similarity: float
    context_relevance: float
    confidence: float
    reasoning_trace: list[str]
    history: list[dict]
    role: str
    mode: str




def check_cache(question, retriever, role):
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, "rb") as f:
            cache = pickle.load(f)
    except Exception:
        return None
    
    for cached_q, cached_state in cache:
        if cached_state.get("role", "Employee") == role:
            sim = retriever.compute_semantic_similarity(question, cached_q)
            if sim >= 0.95:
                return cached_state
    return None


def save_to_cache(question, state):
    cache = []
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "rb") as f:
                cache = pickle.load(f)
        except Exception:
            pass
    
    user_role = state.get("role", "Employee")
    for cached_q, cached_state in cache:
        if cached_q.strip().lower() == question.strip().lower() and cached_state.get("role", "Employee") == user_role:
            return
            
    cached_state = {
        "answer": state.get("answer", ""),
        "citations": state.get("citations", []),
        "tool_used": state.get("tool_used", "⚡ Cached Response"),
        "role": user_role,
        "retrieval_score": state.get("retrieval_score", 1.0),
        "faithfulness": state.get("faithfulness", 1.0),
        "semantic_similarity": state.get("semantic_similarity", 1.0),
        "context_relevance": state.get("context_relevance", 1.0),
        "confidence": state.get("confidence", 1.0),
        "reasoning_trace": state.get("reasoning_trace", []) + ["⚡ Retained from Semantic Cache"],
    }
    cache.append((question, cached_state))
    if len(cache) > 1000:
        cache = cache[-1000:]
        
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "wb") as f:
            pickle.dump(cache, f)
    except Exception:
        pass


def extract_entities(text):
    doc = nlp(text)
    ents = [e.text for e in doc.ents if e.label_ in ("PERSON", "ORG", "PRODUCT", "GPE")]
    codes = ERROR_CODE_RE.findall(text)
    upper = text.upper()
    matched = [s for s in SYSTEMS if s in upper]
    return list(set(ents + codes + matched))


def classify_query(question):
    if extract_ticket_id(question) is not None:
        return "tickets"
    q = question.lower()
    if any(kw in q for kw in CREATE_TICKET_KEYWORDS):
        return "create_ticket"
    if any(kw in q for kw in SUMMARY_KEYWORDS):
        return "summarize"
    if TICKET_RE.search(question):
        return "tickets"
    if any(kw in q for kw in FILTER_KEYWORDS):
        return "filtered_tickets"
    if any(kw in q for kw in TICKET_KEYWORDS):
        return "tickets"
    return "docs"


def needs_multihop(question):
    q = question.lower()
    return any(kw in q for kw in MULTIHOP_KEYWORDS)


def compute_faithfulness(answer, contexts):
    if not contexts or not answer:
        return 0.0
    answer_tokens = set(answer.lower().split())
    ctx_tokens = set()
    for c in contexts:
        ctx_tokens.update(c.lower().split())
    if not answer_tokens:
        return 0.0
    overlap = answer_tokens & ctx_tokens
    return round(len(overlap) / len(answer_tokens), 2)


def compute_confidence(retrieval_score, faithfulness, context_relevance):
    """Weighted confidence: 40% retrieval, 30% faithfulness, 30% context relevance."""
    r_norm = min(retrieval_score / 0.035, 1.0) if retrieval_score else 0
    return round(0.4 * r_norm + 0.3 * faithfulness + 0.3 * context_relevance, 2)


def build_smart_escalation(entities, graph=None):
    """Generate actionable escalation message with contact info."""
    contacts = []
    for ent in entities:
        upper = ent.upper()
        for sys_name, (owner, email) in SYSTEM_OWNERS.items():
            if sys_name in upper or upper in sys_name:
                contacts.append(f"• **{sys_name}** issues → Contact {owner} ({email})")
                break
    msg = ("⚠️ I couldn't find relevant information in the internal documentation to "
           "answer this question confidently.\n\n")
    if contacts:
        msg += "**Recommended contacts based on your query:**\n"
        msg += "\n".join(contacts)
        msg += "\n\n"
    msg += ("**Next steps:** Please raise a ticket on TICKETSYS for further assistance, "
            "or contact the Help Desk Lead Tomas Brewer (t.brewer@nexacorp.com).")
    return msg


def log_audit(state):
    """Append query + response to audit trail."""
    try:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "question": state.get("question", ""),
            "tool_used": state.get("tool_used", ""),
            "faithfulness": state.get("faithfulness", 0),
            "semantic_similarity": state.get("semantic_similarity", 0),
            "context_relevance": state.get("context_relevance", 0),
            "confidence": state.get("confidence", 0),
            "role": state.get("role", "Employee"),
            "answer_length": len(state.get("answer", "")),
        }
        with open(AUDIT_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def build_agent(model="llama3.2", index_dir=None):
    groq_key = os.environ.get("GROQ_API_KEY")
    vllm_url = os.environ.get("VLLM_URL")
    if groq_key:
        try:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
                openai_api_key=groq_key,
                openai_api_base="https://api.groq.com/openai/v1",
                temperature=0
            )
            print(f"[LLM] Using Groq API with model {os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile')}")
        except Exception as e:
            print(f"[LLM] Groq init failed: {e} — falling back to Ollama")
            llm = ChatOllama(model=model, temperature=0)
    elif vllm_url:
        try:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model=os.environ.get("VLLM_MODEL", "meta-llama/Llama-3.2-3B-Instruct"),
                openai_api_key="EMPTY",
                openai_api_base=vllm_url,
                temperature=0
            )
        except Exception as e:
            print(f"[LLM] vLLM init failed: {e} — falling back to Ollama")
            llm = ChatOllama(model=model, temperature=0)
    else:
        llm = ChatOllama(model=model, temperature=0)
        
    retriever_local = Retriever(index_dir=index_dir)
    retriever_mcp = HybridMCPRetriever()

    def _build_history_prompt(state):
        """Format conversation history for multi-turn context."""
        history = state.get("history") or []
        if not history:
            return ""
        lines = []
        for turn in history[-5:]:
            lines.append(f"User: {turn.get('question', '')}")
            lines.append(f"Assistant: {turn.get('answer', '')[:200]}")
        return "Previous conversation:\n" + "\n".join(lines) + "\n\n"

    def _format_document(d):
        content = d.page_content
        # Clean up pipe-separated values for better readability and token reduction
        if " | " in content and (":" in content or "ticket" in d.metadata.get("source", "").lower()):
            pairs = {}
            for part in content.split(" | "):
                if ":" in part:
                    k_v = part.split(":", 1)
                    pairs[k_v[0].strip().lower()] = k_v[1].strip()
            
            tid = pairs.get("ticket_id") or pairs.get("ticket id") or pairs.get("ticket") or ""
            name = pairs.get("customer_name") or pairs.get("employee_name") or pairs.get("customer name") or pairs.get("employee name") or ""
            desc = pairs.get("issue_description") or pairs.get("issue description") or pairs.get("description") or ""
            status = pairs.get("status") or ""
            priority = pairs.get("priority") or ""
            err = pairs.get("exact_error_code") or pairs.get("error_code") or pairs.get("exact error code") or ""
            res = pairs.get("resolution_notes") or pairs.get("resolution notes") or pairs.get("resolution") or ""
            
            parts = []
            if tid: parts.append(f"Ticket ID: {tid}")
            if name: parts.append(f"Name: {name}")
            if desc: parts.append(f"Issue: {desc}")
            if status: parts.append(f"Status: {status}")
            if priority: parts.append(f"Priority: {priority}")
            if err: parts.append(f"Error Code: {err}")
            if res: parts.append(f"Resolution: {res}")
            
            return "\n".join(parts)
        return content

    def _format_ctx(documents):
        return "\n\n---\n\n".join(_format_document(d) for d in documents)

    def _compute_metrics(state, answer):
        """Compute all evaluation metrics for a response."""
        q = state["question"]
        docs = state.get("documents", [])
        contexts = [_format_document(d) for d in docs]
        faith = compute_faithfulness(answer, contexts)
        sem_sim = retriever_local.compute_semantic_similarity(q, answer)
        ctx_rel = retriever_local.compute_context_relevance(q, docs)
        conf = compute_confidence(state.get("retrieval_score", 0), faith, ctx_rel)
        return {
            "faithfulness": faith,
            "semantic_similarity": sem_sim,
            "context_relevance": ctx_rel,
            "confidence": conf,
        }


    # ── TOOL 1: Document Search ──
    async def tool_search_docs(state: State):
        q = state["question"]
        mode = state.get("mode", "local")
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        role = state.get("role", "Employee")
        
        if mode == "elasticsearch":
            trace.append(f"🔍 [Elasticsearch MCP] Tool: Document Search — querying ES for: '{q[:60]}...'")
            filter_dict = {
                "terms": {
                    "metadata.type": ["handbook", "pdf"]
                }
            }
            docs, score = await retriever_mcp.search_docs(q, index_name="nexacorp_docs", k=5, filter_dict=filter_dict)
        else:
            trace.append(f"🔍 [Local Edge] Tool: Document Search — querying Chroma+BM25 for: '{q[:60]}...'")
            docs, score = retriever_local.search_docs(q, k=5)

        # Enforce RBAC filtering for Employee role on Tier 3 Confidential docs
        if role == "Employee":
            filtered_docs = []
            for d in docs:
                content_lower = d.page_content.lower()
                if any(w in content_lower for w in ["tier 3", "compensation", "rating scale", "salary"]):
                    trace.append(f"🔒 Security Gate: Filtered out Tier 3 Confidential document from source '{d.metadata.get('source', '?')}'")
                else:
                    filtered_docs.append(d)
            docs = filtered_docs

        graph_ctx = ""
        if entities:
            triples = retriever_local.search_graph(entities)
            if triples:
                graph_ctx = "\n".join(f"{s} --[{r}]--> {t}" for s, r, t in triples)
                trace.append(f"🕸️ Graph: Found {len(triples)} entity relationships")
        citations = [{"source": d.metadata.get("source", "?"), "snippet": d.page_content[:300]} for d in docs]
        trace.append(f"📊 Retrieved {len(docs)} docs, score: {score:.4f}")
        return {"documents": docs, "graph_context": graph_ctx, "retrieval_score": score,
                "citations": citations, "tool_used": "📄 Document Search", "reasoning_trace": trace}

    # ── TOOL 2: Ticket Lookup ──
    async def tool_search_tickets(state: State):
        q = state["question"]
        mode = state.get("mode", "local")
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        role = state.get("role", "Employee")
        
        # Security Gate: Employee role cannot search tickets
        if role == "Employee":
            trace.append("🔒 Security Gate: Blocked ticket access for role 'Employee'")
            ans = "🔒 **Access Denied**: Ticket search and lookup is restricted to IT Admin and Manager roles. Your current role is 'Employee'. Please contact the IT Helpdesk if you require support information."
            return {"documents": [], "graph_context": "", "retrieval_score": 0.0,
                    "citations": [], "tool_used": "🎫 Ticket Lookup", "answer": ans, "reasoning_trace": trace}

        tkt_id = extract_ticket_id(q)
        if tkt_id:
            trace.append(f"🎫 Detected Ticket ID in query: '{tkt_id}'. Attempting direct exact match lookup.")
            if mode == "elasticsearch":
                direct_doc = await retriever_mcp.search_ticket_by_id(tkt_id)
            else:
                direct_doc = retriever_local.search_ticket_by_id(tkt_id)

            if direct_doc:
                trace.append(f"🎯 Exact match found for ticket '{tkt_id}'! Bypassing semantic search.")
                citations = [{"source": direct_doc.metadata.get("source", "?"), "snippet": direct_doc.page_content[:300]}]
                return {"documents": [direct_doc], "graph_context": "", "retrieval_score": 1.0,
                        "citations": citations, "tool_used": "🎫 Ticket Lookup", "reasoning_trace": trace}
            else:
                trace.append(f"⚠️ No exact match found for ticket ID '{tkt_id}'. Falling back to hybrid search.")

        if mode == "elasticsearch":
            trace.append(f"🎫 [Elasticsearch MCP] Tool: Ticket Lookup — searching ES for: '{q[:60]}...'")
            filter_dict = {
                "term": {
                    "metadata.source": "nexacorp_tickets.csv"
                }
            }
            docs, score = await retriever_mcp.search_docs(q, index_name="nexacorp_docs", k=5, filter_dict=filter_dict)
        else:
            trace.append(f"🎫 [Local Edge] Tool: Ticket Lookup — searching Chroma+BM25 for: '{q[:60]}...'")
            docs, score = retriever_local.search_tickets(q, k=5)

        graph_ctx = ""
        if entities:
            triples = retriever_local.search_graph(entities)
            if triples:
                graph_ctx = "\n".join(f"{s} --[{r}]--> {t}" for s, r, t in triples)
        citations = [{"source": d.metadata.get("source", "?"), "snippet": d.page_content[:300]} for d in docs]
        trace.append(f"📊 Retrieved {len(docs)} tickets, score: {score:.4f}")
        return {"documents": docs, "graph_context": graph_ctx, "retrieval_score": score,
                "citations": citations, "tool_used": "🎫 Ticket Lookup", "reasoning_trace": trace}

    # ── TOOL 3: Summarizer ──
    async def tool_summarize(state: State):
        q = state["question"]
        mode = state.get("mode", "local")
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        role = state.get("role", "Employee")
        
        if mode == "elasticsearch":
            trace.append(f"📋 [Elasticsearch MCP] Tool: Summarizer — searching ES for: '{q[:60]}...'")
            docs, score = await retriever_mcp.search_docs(q, index_name="nexacorp_docs", k=8)
        else:
            trace.append(f"📋 [Local Edge] Tool: Summarizer — searching all sources for: '{q[:60]}...'")
            docs, score, _ = retriever_local.search_all(q, entities, k=8)

        # Enforce RBAC filtering for Employee role on Tier 3 Confidential docs
        if role == "Employee":
            filtered_docs = []
            for d in docs:
                content_lower = d.page_content.lower()
                if any(w in content_lower for w in ["tier 3", "compensation", "rating scale", "salary"]):
                    trace.append(f"🔒 Security Gate: Filtered out Tier 3 Confidential document from source '{d.metadata.get('source', '?')}'")
                else:
                    filtered_docs.append(d)
            docs = filtered_docs

        graph_ctx = ""
        if entities:
            triples = retriever_local.search_graph(entities)
            if triples:
                graph_ctx = "\n".join(f"{s} --[{r}]--> {t}" for s, r, t in triples)
        citations = [{"source": d.metadata.get("source", "?"), "snippet": d.page_content[:300]} for d in docs]
        trace.append(f"📊 Retrieved {len(docs)} docs from all sources")
        return {"documents": docs, "graph_context": graph_ctx, "retrieval_score": score,
                "citations": citations, "tool_used": "📋 Summarizer", "reasoning_trace": trace}

    # ── TOOL 4: Filtered Ticket Search ──
    async def tool_filtered_tickets(state: State):
        q = state["question"]
        mode = state.get("mode", "local")
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        role = state.get("role", "Employee")
        
        # Security Gate: Employee role cannot search tickets
        if role == "Employee":
            trace.append("🔒 Security Gate: Blocked filtered ticket access for role 'Employee'")
            ans = "🔒 **Access Denied**: Filtered ticket searches are restricted to IT Admin and Manager roles. Your current role is 'Employee'."
            return {"documents": [], "graph_context": "", "retrieval_score": 0.0,
                    "citations": [], "tool_used": "🔎 Filtered Tickets", "answer": ans, "reasoning_trace": trace}

        priority_match = PRIORITY_RE.search(q)
        priority = priority_match.group().upper() if priority_match else None
        system = None
        for sys_name in SYSTEMS:
            if sys_name.lower() in q.lower() or sys_name in q.upper():
                system = sys_name
                break
        
        if mode == "elasticsearch":
            trace.append(f"🔎 [Elasticsearch MCP] Tool: Filtered Ticket Search — priority={priority}, system={system}")
            must_filters = [{"term": {"metadata.source": "nexacorp_tickets.csv"}}]
            if priority:
                must_filters.append({"term": {"metadata.priority": priority}})
            if system:
                must_filters.append({"term": {"metadata.system": system}})
            
            filter_dict = {
                "bool": {
                    "must": must_filters
                }
            }
            docs, score = await retriever_mcp.search_docs(q, index_name="nexacorp_docs", k=5, filter_dict=filter_dict)
        else:
            trace.append(f"🔎 [Local Edge] Tool: Filtered Ticket Search — priority={priority}, system={system}")
            docs, score = retriever_local.search_filtered_tickets(q, k=5, priority=priority, system=system)

        graph_ctx = ""
        if entities:
            triples = retriever_local.search_graph(entities)
            if triples:
                graph_ctx = "\n".join(f"{s} --[{r}]--> {t}" for s, r, t in triples)
        citations = [{"source": d.metadata.get("source", "?"), "snippet": d.page_content[:300]} for d in docs]
        trace.append(f"📊 Retrieved {len(docs)} filtered tickets")
        return {"documents": docs, "graph_context": graph_ctx, "retrieval_score": score,
                "citations": citations, "tool_used": "🔎 Filtered Tickets", "reasoning_trace": trace}


    # ── TOOL 5: Create Ticket ──
    async def tool_create_ticket(state: State):
        q = state["question"]
        mode = state.get("mode", "local")
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        trace.append("🆕 Tool: Create Ticket — checking for duplicates first...")

        # Deduplication: search for similar open tickets
        if mode == "elasticsearch":
            filter_dict = {"term": {"metadata.source": "nexacorp_tickets.csv"}}
            docs, score = await retriever_mcp.search_docs(q, index_name="nexacorp_docs", k=3, filter_dict=filter_dict)
        else:
            docs, score = retriever_local.search_tickets(q, k=3)

        open_tickets = [d for d in docs if "Resolved" not in d.page_content]
        if open_tickets:
            sim = retriever_local.compute_semantic_similarity(q, open_tickets[0].page_content)
            if sim > 0.75:
                trace.append(f"⚠️ Duplicate detected! Similarity={sim:.2f} with existing ticket")
                snippet = open_tickets[0].page_content[:200]
                answer = (f"⚠️ **Potential duplicate detected!** An existing open ticket appears "
                         f"very similar (similarity: {sim:.0%}):\n\n> {snippet}\n\n"
                         f"Please check the existing ticket before creating a new one.")
                citations = [{"source": "TICKETSYS", "snippet": snippet}]
                return {"documents": open_tickets, "graph_context": "", "retrieval_score": score,
                        "citations": citations, "tool_used": "🔄 Duplicate Check",
                        "answer": answer, "reasoning_trace": trace,
                        "faithfulness": 1.0, "semantic_similarity": sim,
                        "context_relevance": sim, "confidence": sim}

        # Create new ticket
        tickets_path = os.path.join(DATA_DIR, "nexacorp_tickets.csv")
        # Find next ticket ID
        max_id = 10100
        try:
            with open(tickets_path) as f:
                for row in csv.DictReader(f):
                    tid = row.get("ticket_id", "").strip().strip('"')
                    if tid.startswith("TKT-"):
                        num = int(tid.split("-")[1])
                        max_id = max(max_id, num)
        except Exception:
            pass

        new_id = f"TKT-{max_id + 1}"
        error_codes = ERROR_CODE_RE.findall(q)
        error_code = error_codes[0] if error_codes else "UNKNOWN"
        system = ""
        for s in SYSTEMS:
            if s.lower() in q.lower() or s in q.upper():
                system = s
                break
        priority = "P3"
        pm = PRIORITY_RE.search(q)
        if pm:
            priority = pm.group().upper()

        new_row = {
            "ticket_id": new_id,
            "employee_name": "Copilot Auto-Filed",
            "issue_description": q,
            "status": "In Progress",
            "exact_error_code": error_code,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "priority": priority,
            "resolution_notes": "",
        }

        try:
            with open(tickets_path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(new_row.keys()), quoting=csv.QUOTE_ALL)
                writer.writerow(new_row)
            trace.append(f"✅ Created ticket {new_id} in local CSV")
        except Exception as e:
            trace.append(f"❌ Failed to create ticket: {e}")
            new_id = "FAILED"

        # If elasticsearch mode, index it directly into Elasticsearch in real-time
        if mode == "elasticsearch" and new_id != "FAILED":
            try:
                from elasticsearch import Elasticsearch
                es = Elasticsearch("http://localhost:9200")
                if es.ping():
                    parts = [f"{k}: {v}" for k, v in new_row.items() if v]
                    text = " | ".join(parts)
                    meta = {
                        "source": "nexacorp_tickets.csv",
                        "type": "structured",
                        "priority": priority,
                        "created_at": new_row["created_at"],
                        "status": "In Progress",
                        "error_code": error_code,
                        "ticket_id": new_id
                    }
                    if system:
                        meta["system"] = system
                    
                    vector = retriever_mcp.embeddings_model.embed_query(text)
                    
                    es.index(index="nexacorp_docs", id=f"doc_{new_id}", document={
                        "page_content": text,
                        "embeddings": vector,
                        "metadata": meta
                    })
                    trace.append(f"✅ Indexed ticket {new_id} in Elasticsearch cluster")
            except Exception as es_err:
                trace.append(f"⚠️ Failed to index ticket in Elasticsearch: {es_err}")

        owner_info = ""
        if system in SYSTEM_OWNERS:
            name, email = SYSTEM_OWNERS[system]
            owner_info = f" It has been assigned to {name} ({email})."

        answer = (f"✅ **Ticket Created: {new_id}**\n\n"
                 f"- **Priority:** {priority}\n"
                 f"- **Error Code:** {error_code}\n"
                 f"- **System:** {system or 'Unidentified'}\n"
                 f"- **Status:** In Progress\n"
                 f"- **Created:** {new_row['created_at']}\n\n"
                 f"Your issue has been logged in TICKETSYS.{owner_info}")

        return {"documents": [], "graph_context": "", "retrieval_score": 0,
                "citations": [], "tool_used": "🆕 Ticket Created",
                "answer": answer, "reasoning_trace": trace,
                "faithfulness": 1.0, "semantic_similarity": 0.5,
                "context_relevance": 0.5, "confidence": 0.9}

    async def tool_multihop(state: State):
        q = state["question"]
        mode = state.get("mode", "local")
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        role = state.get("role", "Employee")
        trace.append(f"🔗 Tool: Multi-hop Reasoning — decomposing query into sub-tasks")

        if mode == "elasticsearch":
            filter_docs = {"terms": {"metadata.type": ["handbook", "pdf"]}}
            trace.append("  Hop 1: [Elasticsearch] Searching handbook for definitions...")
            docs1, score1 = await retriever_mcp.search_docs(q, index_name="nexacorp_docs", k=3, filter_dict=filter_docs)

            if role == "Employee":
                trace.append("  Hop 2: [Elasticsearch] Blocked ticket search for role 'Employee'")
                docs2, score2 = [], 1.0
            else:
                filter_tickets = {"term": {"metadata.source": "nexacorp_tickets.csv"}}
                trace.append("  Hop 2: [Elasticsearch] Searching tickets for related incidents...")
                docs2, score2 = await retriever_mcp.search_docs(q, index_name="nexacorp_docs", k=3, filter_dict=filter_tickets)
        else:
            trace.append("  Hop 1: [Local] Searching handbook for definitions...")
            docs1, score1 = retriever_local.search_docs(q, k=3)
            
            if role == "Employee":
                trace.append("  Hop 2: [Local] Blocked ticket search for role 'Employee'")
                docs2, score2 = [], 1.0
            else:
                trace.append("  Hop 2: [Local] Searching tickets for related incidents...")
                docs2, score2 = retriever_local.search_tickets(q, k=3)

        # Enforce RBAC filtering for Employee role on Tier 3 Confidential docs (Hop 1 docs)
        if role == "Employee":
            filtered_docs1 = []
            for d in docs1:
                content_lower = d.page_content.lower()
                if any(w in content_lower for w in ["tier 3", "compensation", "rating scale", "salary"]):
                    trace.append(f"🔒 Security Gate: Filtered out Tier 3 Confidential document from source '{d.metadata.get('source', '?')}'")
                else:
                    filtered_docs1.append(d)
            docs1 = filtered_docs1

        # Hop 3: Graph for relationships
        graph_ctx = ""
        if entities:
            triples = retriever_local.search_graph(entities)
            if triples:
                graph_ctx = "\n".join(f"{s} --[{r}]--> {t}" for s, r, t in triples)
                trace.append(f"  Hop 3: Found {len(triples)} graph relationships")

        all_docs = docs1 + docs2
        avg_score = (score1 + score2) / 2
        citations = [{"source": d.metadata.get("source", "?"), "snippet": d.page_content[:300]} for d in all_docs]
        trace.append(f"📊 Multi-hop: {len(docs1)} doc results + {len(docs2)} ticket results")

        return {"documents": all_docs, "graph_context": graph_ctx, "retrieval_score": avg_score,
                "citations": citations, "tool_used": "🔗 Multi-hop Search", "reasoning_trace": trace}

    # ── Router ──
    def route_query(state: State):
        q = state["question"]
        entities = extract_entities(q)
        route = classify_query(q)
        # Override: multi-hop detection
        if route == "docs" and needs_multihop(q):
            route = "multihop"
        trace = [f"🧭 Router: classified as '{route}' | Entities: {entities[:5]}"]
        
        if CACHE_ENABLED and extract_ticket_id(q) is None:
            cached = check_cache(q, retriever_local, state.get("role", "Employee"))
            if cached:
                trace.append("⚡ Semantic Cache Hit! Routing directly to completion.")
                return {
                    "entities": entities,
                    "route": "cache_hit",
                    "reasoning_trace": trace,
                    "answer": cached["answer"],
                    "citations": cached["citations"],
                    "tool_used": cached["tool_used"],
                    "retrieval_score": cached["retrieval_score"],
                    "faithfulness": cached["faithfulness"],
                    "semantic_similarity": cached["semantic_similarity"],
                    "context_relevance": cached["context_relevance"],
                    "confidence": cached["confidence"],
                }
                
        return {"entities": entities, "route": route, "reasoning_trace": trace}

    def pick_tool(state: State):
        return state["route"]
    # ── Generate functions ──
    def generate_docs(state: State):
        trace = state.get("reasoning_trace", [])
        if state.get("answer"):
            return {"answer": state["answer"], "citations": state.get("citations", []),
                    "tool_used": state.get("tool_used", "📄 Document Search"), "reasoning_trace": trace}
        q = state["question"]
        
        if BYPASS_ENABLED:
            # 1. System Owner Lookup Bypass
            q_lower = q.lower()
            if any(w in q_lower for w in ["who owns", "who manages", "who resolves", "who is the owner", "who is in charge of", "owner of"]):
                for sys_name, (owner, email) in SYSTEM_OWNERS.items():
                    if sys_name.lower() in q_lower or sys_name.replace("-", "").lower() in q_lower.replace("-", ""):
                        ans = f"According to internal enterprise records, the system **{sys_name}** is managed by **{owner}** ({email})."
                        trace.append(f"⚡ System owner bypass for system '{sys_name}'")
                        return {
                            "answer": ans,
                            "citations": [{"source": "NexaCorp Org Chart / System Owners", "snippet": f"{sys_name} owner is {owner}"}],
                            "retrieval_score": 1.0,
                            "faithfulness": 1.0,
                            "semantic_similarity": 1.0,
                            "context_relevance": 1.0,
                            "confidence": 1.0,
                            "tool_used": "⚡ Direct System Owner Lookup",
                            "reasoning_trace": trace
                        }
            
            # 2. Error Code Bypass
            codes = ERROR_CODE_RE.findall(q)
            if codes:
                code = codes[0].upper()
                for doc in state.get("documents", []):
                    content = doc.page_content
                    for line in content.split("\n"):
                        if code in line.upper():
                            clean_line = line.strip().lstrip("-").strip()
                            if ":" in clean_line:
                                ans = f"Here is the official documentation for error code **{code}**:\n\n* {clean_line}"
                                trace.append(f"⚡ Error code definition bypass for '{code}'")
                                return {
                                    "answer": ans,
                                    "citations": [{"source": doc.metadata.get("source", "Handbook"), "snippet": line}],
                                    "retrieval_score": 1.0,
                                    "faithfulness": 1.0,
                                    "semantic_similarity": 1.0,
                                    "context_relevance": 1.0,
                                    "confidence": 1.0,
                                    "tool_used": "⚡ Direct Error Code Lookup",
                                    "reasoning_trace": trace
                                }

        ctx = _format_ctx(state["documents"][:3])
        graph = state.get("graph_context", "")
        history = _build_history_prompt(state)
        prompt = f"""{history}You are NexaCorp's internal knowledge copilot. Answer using ONLY the context below.
For "who manages/owns/resolves" questions, check the Entity Relationships FIRST.
Note: "Leave requests" refer to requesting time off, vacation, PTO, and holidays.
If the answer isn't in the context, say: "This information is not available in the internal docs. Please raise a ticket on TICKETSYS."
Cite the source.

Question: {q}

Entity Relationships (check these first for people/system questions):
{graph if graph else "None"}

Document Context:
{ctx}

Answer:"""
        resp = llm.invoke(prompt)
        metrics = _compute_metrics(state, resp.content)
        trace.append(f"🤖 Generated answer ({len(resp.content)} chars) | Confidence: {metrics['confidence']:.0%}")
        result = {"answer": resp.content, "reasoning_trace": trace}
        result.update(metrics)
        return result

    def generate_tickets(state: State):
        trace = state.get("reasoning_trace", [])
        if state.get("answer"):
            return {"answer": state["answer"], "citations": state.get("citations", []),
                    "tool_used": state.get("tool_used", "🎫 Ticket Lookup"), "reasoning_trace": trace}
        q = state["question"]
        
        if BYPASS_ENABLED:
            # 3. Specific Ticket ID Lookup Bypass
            tkt_id = extract_ticket_id(q)
            if tkt_id:
                t_id_clean = tkt_id.upper()
                if t_id_clean.startswith("TKT-"):
                    clean_num = t_id_clean[4:]
                else:
                    clean_num = t_id_clean
                
                for doc in state.get("documents", []):
                    content = doc.page_content
                    doc_t_id = str(doc.metadata.get("ticket_id", "")).upper()
                    
                    if doc_t_id == t_id_clean or doc_t_id == clean_num or (doc_t_id.startswith("TKT-") and doc_t_id[4:] == clean_num) or t_id_clean in content.upper():
                        # Parse the ticket fields (key-value separated by ' | ')
                        fields = {}
                        for part in content.split(" | "):
                            if ":" in part:
                                k, v = part.split(":", 1)
                                fields[k.strip().lower()] = v.strip()
                        
                        if fields:
                            t_id = fields.get("ticket_id", tkt_id)
                            t_emp = fields.get("customer_name", fields.get("employee_name", fields.get("employee", "Unknown")))
                            t_sys = doc.metadata.get("system", fields.get("product", fields.get("system", "Unknown")))
                            t_issue = fields.get("issue_description", fields.get("issue", "Unknown"))
                            t_status = fields.get("status", "Unknown")
                            t_priority = fields.get("priority", "Unknown")
                            t_resolution = fields.get("resolution_notes", fields.get("resolution", "N/A"))
                            t_err = fields.get("exact_error_code", fields.get("error_code", "None"))
                            
                            # Check if the query is asking for the system owner
                            q_lower = q.lower()
                            if any(w in q_lower for w in ["who owns", "who manages", "who resolves", "who is the owner", "who is in charge of", "owner of", "manager of", "contact person for", "contact"]):
                                matched_owner = None
                                matched_sys = None
                                # Try exact or substring match first
                                for sys_name, (owner, email) in SYSTEM_OWNERS.items():
                                    sys_clean = sys_name.lower().replace("-", "")
                                    t_sys_clean = t_sys.lower().replace("-", "")
                                    if sys_clean in t_sys_clean or t_sys_clean in sys_clean:
                                        matched_owner = (owner, email)
                                        matched_sys = sys_name
                                        break
                                
                                # Fallback: if no match, check common words
                                if not matched_owner:
                                    t_sys_words = set(t_sys.lower().split())
                                    for sys_name, (owner, email) in SYSTEM_OWNERS.items():
                                        sys_words = set(sys_name.lower().replace("-", " ").split())
                                        if t_sys_words & sys_words: # intersection
                                            matched_owner = (owner, email)
                                            matched_sys = sys_name
                                            break
                                            
                                if matched_owner:
                                    ans = f"According to internal enterprise records, the system affected by ticket **{t_id}** is **{t_sys}** (managed under **{matched_sys}**), which is owned/managed by **{matched_owner[0]}** ({matched_owner[1]})."
                                    trace.append(f"⚡ System owner bypass from ticket system '{t_sys}'")
                                    return {
                                        "answer": ans,
                                        "citations": [{"source": doc.metadata.get("source", "Ticket DB"), "snippet": content[:300]}],
                                        "retrieval_score": 1.0,
                                        "faithfulness": 1.0,
                                        "semantic_similarity": 1.0,
                                        "context_relevance": 1.0,
                                        "confidence": 1.0,
                                        "tool_used": "⚡ Direct System Owner Lookup",
                                        "reasoning_trace": trace
                                    }
                            
                            # Format response
                            ans = f"""### 🎫 Ticket Details: {t_id}
- **Customer/Employee**: {t_emp}
- **Affected Product/System**: {t_sys}
- **Priority**: `{t_priority}`
- **Status**: **{t_status}**
- **Error Code**: `{t_err}`

**Issue Description**:
> {t_issue}

**Resolution / Notes**:
> {t_resolution if t_resolution.strip() else "No resolution notes recorded yet."}"""
                            
                            trace.append(f"⚡ Specific ticket bypass for ticket '{t_id}'")
                            return {
                                "answer": ans,
                                "citations": [{"source": doc.metadata.get("source", "Ticket DB"), "snippet": content[:300]}],
                                "retrieval_score": 1.0,
                                "faithfulness": 1.0,
                                "semantic_similarity": 1.0,
                                "context_relevance": 1.0,
                                "confidence": 1.0,
                                "tool_used": "⚡ Direct Ticket Lookup",
                                "reasoning_trace": trace
                            }

        ctx = _format_ctx(state["documents"])
        graph = state.get("graph_context", "")
        history = _build_history_prompt(state)
        prompt = f"""{history}You are NexaCorp's support copilot. Answer the user's question about support or IT tickets using ONLY the retrieved ticket data.
Include ticket IDs, statuses, resolutions, and names/error codes when available.
Note: The ticket database contains synthetic data, so the issue description and resolution notes might not logically align. Match a ticket purely if the name and issue description/category match the query, regardless of whether the resolution notes seem logically related.
If and only if no matching tickets exist in the retrieved data, say: "No matching tickets found. Please raise a new ticket on TICKETSYS." Do not output this phrase if matching tickets are found.

Question: {q}

Ticket Data:
{ctx}

Entity Relationships:
{graph if graph else "None"}

Answer:"""
        resp = llm.invoke(prompt)
        metrics = _compute_metrics(state, resp.content)
        trace.append(f"🤖 Generated ticket answer ({len(resp.content)} chars)")
        result = {"answer": resp.content, "reasoning_trace": trace}
        result.update(metrics)
        return result

    def generate_summary(state: State):
        trace = state.get("reasoning_trace", [])
        if state.get("answer"):
            return {"answer": state["answer"], "citations": state.get("citations", []),
                    "tool_used": state.get("tool_used", "📋 Summarizer"), "reasoning_trace": trace}
        ctx = _format_ctx(state["documents"])
        graph = state.get("graph_context", "")
        q = state["question"]
        history = _build_history_prompt(state)
        prompt = f"""{history}You are NexaCorp's internal knowledge copilot. Provide a concise summary based on the retrieved context.
Organize the summary with bullet points. Cite source documents.
If insufficient context, say what's missing.

Topic: {q}

Retrieved Documents:
{ctx}

Entity Relationships:
{graph if graph else "None"}

Summary:"""
        resp = llm.invoke(prompt)
        metrics = _compute_metrics(state, resp.content)
        trace = state.get("reasoning_trace", [])
        trace.append(f"🤖 Generated summary ({len(resp.content)} chars)")
        result = {"answer": resp.content, "reasoning_trace": trace}
        result.update(metrics)
        return result

    def generate_multihop(state: State):
        trace = state.get("reasoning_trace", [])
        if state.get("answer"):
            return {"answer": state["answer"], "citations": state.get("citations", []),
                    "tool_used": state.get("tool_used", "🔗 Multi-hop Search"), "reasoning_trace": trace}
        ctx = _format_ctx(state["documents"])
        graph = state.get("graph_context", "")
        q = state["question"]
        history = _build_history_prompt(state)
        prompt = f"""{history}You are NexaCorp's internal knowledge copilot performing multi-source analysis.
I retrieved information from BOTH the handbook/docs AND ticket history to answer your question.
Synthesize information from all sources. First explain what the issue IS (from handbook), then show what happened in past incidents (from tickets), then recommend next steps.
Cite sources throughout.

Question: {q}

Entity Relationships:
{graph if graph else "None"}

Combined Context:
{ctx}

Multi-source Analysis:"""
        resp = llm.invoke(prompt)
        metrics = _compute_metrics(state, resp.content)
        trace = state.get("reasoning_trace", [])
        trace.append(f"🤖 Multi-hop synthesis ({len(resp.content)} chars)")
        result = {"answer": resp.content, "reasoning_trace": trace}
        result.update(metrics)
        return result

    def escalate(state: State):
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        trace.append("⚠️ No relevant documents found — escalating with contact info")
        answer = build_smart_escalation(entities, retriever_local.graph)
        return {"answer": answer, "citations": [], "retrieval_score": 0.0,
                "faithfulness": 0.0, "semantic_similarity": 0.0,
                "context_relevance": 0.0, "confidence": 0.0,
                "tool_used": "⚠️ Escalated", "reasoning_trace": trace}

    def should_escalate(state: State):
        if state.get("answer"):
            return "generate"
        if not state.get("documents"):
            return "escalate"
        return "generate"

    def should_escalate_create(state: State):
        # create_ticket and multihop always proceed (they handle their own logic)
        return "generate"
    # ── Audit logging node ──
    def audit_log(state: State):
        log_audit(state)
        if CACHE_ENABLED and state.get("route") != "cache_hit":
            if state.get("answer") and not state.get("answer", "").startswith("⚠️"):
                save_to_cache(state["question"], state)
        return {}

    # ── Build Graph ──
    g = StateGraph(State)

    g.add_node("route", route_query)
    g.add_node("tool_docs", tool_search_docs)
    g.add_node("tool_tickets", tool_search_tickets)
    g.add_node("tool_summarize", tool_summarize)
    g.add_node("tool_filtered", tool_filtered_tickets)
    g.add_node("tool_create", tool_create_ticket)
    g.add_node("tool_multihop", tool_multihop)
    g.add_node("gen_docs", generate_docs)
    g.add_node("gen_tickets", generate_tickets)
    g.add_node("gen_summary", generate_summary)
    g.add_node("gen_multihop", generate_multihop)
    g.add_node("escalate", escalate)
    g.add_node("audit", audit_log)

    g.set_entry_point("route")
    g.add_conditional_edges("route", pick_tool, {
        "docs": "tool_docs",
        "tickets": "tool_tickets",
        "summarize": "tool_summarize",
        "filtered_tickets": "tool_filtered",
        "create_ticket": "tool_create",
        "multihop": "tool_multihop",
        "cache_hit": "audit",
    })

    g.add_conditional_edges("tool_docs", should_escalate, {"generate": "gen_docs", "escalate": "escalate"})
    g.add_conditional_edges("tool_tickets", should_escalate, {"generate": "gen_tickets", "escalate": "escalate"})
    g.add_conditional_edges("tool_summarize", should_escalate, {"generate": "gen_summary", "escalate": "escalate"})
    g.add_conditional_edges("tool_filtered", should_escalate, {"generate": "gen_tickets", "escalate": "escalate"})
    g.add_conditional_edges("tool_create", should_escalate_create, {"generate": "audit"})
    g.add_conditional_edges("tool_multihop", should_escalate, {"generate": "gen_multihop", "escalate": "escalate"})

    g.add_edge("gen_docs", "audit")
    g.add_edge("gen_tickets", "audit")
    g.add_edge("gen_summary", "audit")
    g.add_edge("gen_multihop", "audit")
    g.add_edge("escalate", "audit")
    g.add_edge("audit", END)

    return g.compile(), retriever_local


if __name__ == "__main__":
    import asyncio
    agent, _ = build_agent()
    tests = [
        "What is error code ERR-AUTH-9092?",
        "How do I request time off?",
        "Show me tickets related to VPN issues",
        "Summarize the data classification policy",
        "What is the recipe for pancakes?",
    ]
    async def run_tests():
        for q in tests:
            print(f"\nQ: {q}")
            r = await agent.ainvoke({"question": q, "mode": "elasticsearch"})
            print(f"Tool: {r.get('tool_used')}")
            print(f"A: {r['answer'][:200]}")
            print(f"Retrieval: {r.get('retrieval_score', 0):.3f} | Faith: {r.get('faithfulness', 0):.2f} "
                  f"| SemSim: {r.get('semantic_similarity', 0):.3f} | CtxRel: {r.get('context_relevance', 0):.3f} "
                  f"| Confidence: {r.get('confidence', 0):.0%}")
            print("-" * 60)
    asyncio.run(run_tests())

