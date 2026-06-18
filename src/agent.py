import os
import re
import pickle
from typing import TypedDict
from langgraph.graph import StateGraph, END
from langchain_ollama import ChatOllama
from src.retriever import Retriever
from src.retriever_mcp import HybridMCPRetriever

# Import config, helpers, metrics, and tools
from src.config import BYPASS_ENABLED, CACHE_ENABLED, CACHE_PATH
from src.utils import extract_entities, classify_query, needs_multihop, extract_ticket_id
from src.metrics import build_smart_escalation, log_audit
from src.tools import (
    tool_search_docs, tool_search_tickets, tool_summarize, tool_filtered_tickets, tool_multihop,
    tool_create_ticket, generate_docs, generate_tickets, generate_summary, generate_multihop
)

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

def pick_tool(state: State):
    return state["route"]

def should_escalate(state: State):
    if state.get("answer"):
        return "generate"
    if not state.get("documents"):
        return "escalate"
    return "generate"

def should_escalate_create(state: State):
    return "generate"

def build_agent(model="llama3.2", index_dir=None):
    import getpass
    is_local = (getpass.getuser() == "ichhit")
    
    groq_key = os.environ.get("GROQ_API_KEY")
    vllm_url = os.environ.get("VLLM_URL")
    
    # Force local Ollama run when on developer's laptop, use Groq/vLLM only for cloud deployments
    # (Allow override with FORCE_GROQ env var if needed for testing)
    use_groq = groq_key and (not is_local or os.environ.get("FORCE_GROQ") == "true")
    use_vllm = vllm_url and not use_groq
    
    if use_groq:
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
    elif use_vllm:
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
        print(f"[LLM] Using local Ollama model {model}")
        llm = ChatOllama(model=model, temperature=0)
        
    retriever_local = Retriever(index_dir=index_dir)
    retriever_mcp = HybridMCPRetriever()

    # Closures to bind llm and retrievers to tool functions
    async def node_search_docs(state):
        return await tool_search_docs(state, retriever_local, retriever_mcp)

    async def node_search_tickets(state):
        return await tool_search_tickets(state, retriever_local, retriever_mcp)

    async def node_summarize(state):
        return await tool_summarize(state, retriever_local, retriever_mcp)

    async def node_filtered(state):
        return await tool_filtered_tickets(state, retriever_local, retriever_mcp)

    async def node_create(state):
        return await tool_create_ticket(state, retriever_local, retriever_mcp)

    async def node_multihop(state):
        return await tool_multihop(state, retriever_local, retriever_mcp)

    def node_gen_docs(state):
        return generate_docs(state, llm, retriever_local)

    def node_gen_tickets(state):
        return generate_tickets(state, llm, retriever_local)

    def node_gen_summary(state):
        return generate_summary(state, llm, retriever_local)

    def node_gen_multihop(state):
        return generate_multihop(state, llm, retriever_local)

    def escalate(state: State):
        entities = state.get("entities", [])
        trace = state.get("reasoning_trace", [])
        trace.append("⚠️ No relevant documents found — escalating with contact info")
        answer = build_smart_escalation(entities, retriever_local.graph)
        return {"answer": answer, "citations": [], "retrieval_score": 0.0,
                "faithfulness": 0.0, "semantic_similarity": 0.0,
                "context_relevance": 0.0, "confidence": 0.0,
                "tool_used": "⚠️ Escalated", "reasoning_trace": trace}

    def route_query(state: State):
        q = state["question"]
        entities = extract_entities(q)
        route = classify_query(q)
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

    def audit_log(state: State):
        log_audit(state)
        if CACHE_ENABLED and state.get("route") != "cache_hit":
            if state.get("answer") and not state.get("answer", "").startswith("⚠️"):
                save_to_cache(state["question"], state)
        return {}

    # Build Graph
    g = StateGraph(State)

    g.add_node("route", route_query)
    g.add_node("tool_docs", node_search_docs)
    g.add_node("tool_tickets", node_search_tickets)
    g.add_node("tool_summarize", node_summarize)
    g.add_node("tool_filtered", node_filtered)
    g.add_node("tool_create", node_create)
    g.add_node("tool_multihop", node_multihop)
    g.add_node("gen_docs", node_gen_docs)
    g.add_node("gen_tickets", node_gen_tickets)
    g.add_node("gen_summary", node_gen_summary)
    g.add_node("gen_multihop", node_gen_multihop)
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
