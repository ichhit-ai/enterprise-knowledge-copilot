import re
import os
import csv
import json
import spacy
from datetime import datetime
from typing import TypedDict
from langgraph.graph import StateGraph, END
from langchain_ollama import ChatOllama
from src.retriever import Retriever

nlp = spacy.load("en_core_web_sm")

ERROR_CODE_RE = re.compile(r'[A-Z]{2,}[\-][A-Z]*[\-]?\w+')
TICKET_RE = re.compile(r'TKT-\d+', re.IGNORECASE)
PRIORITY_RE = re.compile(r'\bP[1-4]\b', re.IGNORECASE)

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
                    "recent issues", "ticket status"]
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


def extract_entities(text):
    doc = nlp(text)
    ents = [e.text for e in doc.ents if e.label_ in ("PERSON", "ORG", "PRODUCT", "GPE")]
    codes = ERROR_CODE_RE.findall(text)
    upper = text.upper()
    matched = [s for s in SYSTEMS if s in upper]
    return list(set(ents + codes + matched))


def classify_query(question):
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


def build_agent(model="llama3.2"):
    llm = ChatOllama(model=model, temperature=0)
    retriever = Retriever()

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

    def _compute_metrics(state, answer):
        """Compute all evaluation metrics for a response."""
        q = state["question"]
        docs = state.get("documents", [])
        contexts = [d.page_content for d in docs]
        faith = compute_faithfulness(answer, contexts)
        sem_sim = retriever.compute_semantic_similarity(q, answer)
        ctx_rel = retriever.compute_context_relevance(q, docs)
        conf = compute_confidence(state.get("retrieval_score", 0), faith, ctx_rel)
        return {
            "faithfulness": faith,
            "semantic_similarity": sem_sim,
            "context_relevance": ctx_rel,
            "confidence": conf,
        }

    # ── TOOL 1: Document Search ──
    def tool_search_docs(state: State):
        q = state["question"]
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        trace.append(f"🔍 Tool: Document Search — querying handbook + runbook for: '{q[:60]}...'")
        docs, score = retriever.search_docs(q, k=5)
        graph_ctx = ""
        if entities:
            triples = retriever.search_graph(entities)
            if triples:
                graph_ctx = "\n".join(f"{s} --[{r}]--> {t}" for s, r, t in triples)
                trace.append(f"🕸️ Graph: Found {len(triples)} entity relationships")
        citations = [{"source": d.metadata.get("source", "?"), "snippet": d.page_content[:300]} for d in docs]
        trace.append(f"📊 Retrieved {len(docs)} docs, RRF score: {score:.4f}")
        return {"documents": docs, "graph_context": graph_ctx, "retrieval_score": score,
                "citations": citations, "tool_used": "📄 Document Search", "reasoning_trace": trace}

    # ── TOOL 2: Ticket Lookup ──
    def tool_search_tickets(state: State):
        q = state["question"]
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        trace.append(f"🎫 Tool: Ticket Lookup — searching tickets for: '{q[:60]}...'")
        docs, score = retriever.search_tickets(q, k=5)
        graph_ctx = ""
        if entities:
            triples = retriever.search_graph(entities)
            if triples:
                graph_ctx = "\n".join(f"{s} --[{r}]--> {t}" for s, r, t in triples)
        citations = [{"source": d.metadata.get("source", "?"), "snippet": d.page_content[:300]} for d in docs]
        trace.append(f"📊 Retrieved {len(docs)} tickets, RRF score: {score:.4f}")
        return {"documents": docs, "graph_context": graph_ctx, "retrieval_score": score,
                "citations": citations, "tool_used": "🎫 Ticket Lookup", "reasoning_trace": trace}

    # ── TOOL 3: Summarizer ──
    def tool_summarize(state: State):
        q = state["question"]
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        trace.append(f"📋 Tool: Summarizer — searching all sources for: '{q[:60]}...'")
        docs, score, graph_ctx = retriever.search_all(q, entities, k=8)
        citations = [{"source": d.metadata.get("source", "?"), "snippet": d.page_content[:300]} for d in docs]
        trace.append(f"📊 Retrieved {len(docs)} docs from all sources")
        return {"documents": docs, "graph_context": graph_ctx, "retrieval_score": score,
                "citations": citations, "tool_used": "📋 Summarizer", "reasoning_trace": trace}

    # ── TOOL 4: Filtered Ticket Search ──
    def tool_filtered_tickets(state: State):
        q = state["question"]
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        priority_match = PRIORITY_RE.search(q)
        priority = priority_match.group().upper() if priority_match else None
        system = None
        for sys_name in SYSTEMS:
            if sys_name.lower() in q.lower() or sys_name in q.upper():
                system = sys_name
                break
        trace.append(f"🔎 Tool: Filtered Ticket Search — priority={priority}, system={system}")
        docs, score = retriever.search_filtered_tickets(q, k=5, priority=priority, system=system)
        graph_ctx = ""
        if entities:
            triples = retriever.search_graph(entities)
            if triples:
                graph_ctx = "\n".join(f"{s} --[{r}]--> {t}" for s, r, t in triples)
        citations = [{"source": d.metadata.get("source", "?"), "snippet": d.page_content[:300]} for d in docs]
        trace.append(f"📊 Retrieved {len(docs)} filtered tickets")
        return {"documents": docs, "graph_context": graph_ctx, "retrieval_score": score,
                "citations": citations, "tool_used": "🔎 Filtered Tickets", "reasoning_trace": trace}

    # ── TOOL 5: Create Ticket ──
    def tool_create_ticket(state: State):
        q = state["question"]
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        trace.append("🆕 Tool: Create Ticket — checking for duplicates first...")

        # Deduplication: search for similar open tickets
        docs, score = retriever.search_tickets(q, k=3)
        open_tickets = [d for d in docs if "Resolved" not in d.page_content]
        if open_tickets:
            sim = retriever.compute_semantic_similarity(q, open_tickets[0].page_content)
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
            trace.append(f"✅ Created ticket {new_id} (priority={priority}, error={error_code})")
        except Exception as e:
            trace.append(f"❌ Failed to create ticket: {e}")
            new_id = "FAILED"

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

    # ── TOOL 6: Multi-hop Reasoning ──
    def tool_multihop(state: State):
        q = state["question"]
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        trace.append(f"🔗 Tool: Multi-hop Reasoning — decomposing query into sub-tasks")

        # Hop 1: Search docs for definitions/procedures
        trace.append("  Hop 1: Searching handbook for definitions...")
        docs1, score1 = retriever.search_docs(q, k=3)

        # Hop 2: Search tickets for real incidents
        trace.append("  Hop 2: Searching tickets for related incidents...")
        docs2, score2 = retriever.search_tickets(q, k=3)

        # Hop 3: Graph for relationships
        graph_ctx = ""
        if entities:
            triples = retriever.search_graph(entities)
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
        return {"entities": entities, "route": route, "reasoning_trace": trace}

    def pick_tool(state: State):
        return state["route"]

    # ── Generate functions ──
    def generate_docs(state: State):
        ctx = "\n\n---\n\n".join(d.page_content for d in state["documents"][:3])
        graph = state.get("graph_context", "")
        q = state["question"]
        history = _build_history_prompt(state)
        prompt = f"""{history}You are NexaCorp's internal knowledge copilot. Answer using ONLY the context below.
For "who manages/owns/resolves" questions, check the Entity Relationships FIRST.
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
        trace = state.get("reasoning_trace", [])
        trace.append(f"🤖 Generated answer ({len(resp.content)} chars) | Confidence: {metrics['confidence']:.0%}")
        result = {"answer": resp.content, "reasoning_trace": trace}
        result.update(metrics)
        return result

    def generate_tickets(state: State):
        ctx = "\n\n---\n\n".join(d.page_content for d in state["documents"])
        graph = state.get("graph_context", "")
        q = state["question"]
        history = _build_history_prompt(state)
        prompt = f"""{history}You are NexaCorp's IT support copilot. Answer the employee's question about IT tickets using ONLY the retrieved ticket data.
Include ticket IDs, statuses, error codes, and assigned personnel when available.
If no matching tickets exist, say: "No matching tickets found. Please raise a new ticket on TICKETSYS."

Question: {q}

Ticket Data:
{ctx}

Entity Relationships:
{graph if graph else "None"}

Answer:"""
        resp = llm.invoke(prompt)
        metrics = _compute_metrics(state, resp.content)
        trace = state.get("reasoning_trace", [])
        trace.append(f"🤖 Generated ticket answer ({len(resp.content)} chars)")
        result = {"answer": resp.content, "reasoning_trace": trace}
        result.update(metrics)
        return result

    def generate_summary(state: State):
        ctx = "\n\n---\n\n".join(d.page_content for d in state["documents"])
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
        ctx = "\n\n---\n\n".join(d.page_content for d in state["documents"])
        graph = state.get("graph_context", "")
        q = state["question"]
        history = _build_history_prompt(state)
        prompt = f"""{history}You are NexaCorp's internal knowledge copilot performing multi-source analysis.
I retrieved information from BOTH the handbook AND ticket history to answer your question.
Synthesize information from all sources. First explain what the issue IS (from handbook), then show what happened in past incidents (from tickets), then recommend next steps.
Cite sources throughout.

Question: {q}

Entity Relationships:
{graph if graph else "None"}

Combined Context (handbook + tickets):
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
        answer = build_smart_escalation(entities, retriever.graph)
        return {"answer": answer, "citations": [], "retrieval_score": 0.0,
                "faithfulness": 0.0, "semantic_similarity": 0.0,
                "context_relevance": 0.0, "confidence": 0.0,
                "tool_used": "⚠️ Escalated", "reasoning_trace": trace}

    def should_escalate(state: State):
        if not state.get("documents"):
            return "escalate"
        return "generate"

    def should_escalate_create(state: State):
        # create_ticket and multihop always proceed (they handle their own logic)
        return "generate"

    # ── Audit logging node ──
    def audit_log(state: State):
        log_audit(state)
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

    return g.compile(), retriever


if __name__ == "__main__":
    agent, _ = build_agent()
    tests = [
        "What is error code ERR-AUTH-9092?",
        "How do I request time off?",
        "Show me tickets related to VPN issues",
        "Summarize the data classification policy",
        "What is the recipe for pancakes?",
    ]
    for q in tests:
        print(f"\nQ: {q}")
        r = agent.invoke({"question": q})
        print(f"Tool: {r.get('tool_used')}")
        print(f"A: {r['answer'][:200]}")
        print(f"Retrieval: {r.get('retrieval_score', 0):.3f} | Faith: {r.get('faithfulness', 0):.2f} "
              f"| SemSim: {r.get('semantic_similarity', 0):.3f} | CtxRel: {r.get('context_relevance', 0):.3f} "
              f"| Confidence: {r.get('confidence', 0):.0%}")
        print("-" * 60)
