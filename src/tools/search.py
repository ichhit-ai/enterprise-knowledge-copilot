from src.utils import extract_ticket_id, PRIORITY_RE, SYSTEMS
from src.tools.helpers import apply_rbac_filter, build_graph_context, format_document

async def tool_search_docs(state, retriever_local, retriever_mcp):
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

    docs = apply_rbac_filter(docs, role, trace)
    graph_ctx = build_graph_context(entities, retriever_local, trace)
    citations = [{"source": d.metadata.get("source", "?"), "snippet": format_document(d)[:300]} for d in docs]
    trace.append(f"📊 Retrieved {len(docs)} docs, score: {score:.4f}")
    return {"documents": docs, "graph_context": graph_ctx, "retrieval_score": score,
            "citations": citations, "tool_used": "📄 Document Search", "reasoning_trace": trace}

async def tool_search_tickets(state, retriever_local, retriever_mcp):
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
            citations = [{"source": direct_doc.metadata.get("source", "?"), "snippet": format_document(direct_doc)[:300]}]
            return {"documents": [direct_doc], "graph_context": "", "retrieval_score": 1.0,
                    "citations": citations, "tool_used": "🎫 Ticket Lookup", "reasoning_trace": trace}
        else:
            trace.append(f"⚠️ No exact match found for ticket ID '{tkt_id}'. Falling back to hybrid search.")

    # Graph-assisted query expansion for employee/system relationships
    expanded_q = q
    if entities:
        triples = retriever_local.search_graph(entities)
        related_terms = []
        for s, r, t in triples:
            if r in ("OWNS_SYSTEM", "CO_OWNS_SYSTEM", "secondary_contact_for", "RESOLVES"):
                related_terms.append(t)
        if related_terms:
            expanded_q = q + " " + " ".join(related_terms)
            trace.append(f"🕸️ Graph: Found related systems/errors: {related_terms}. Expanded query to: '{expanded_q}'")

    if mode == "elasticsearch":
        trace.append(f"🎫 [Elasticsearch MCP] Tool: Ticket Lookup — searching ES for: '{expanded_q[:60]}...'")
        filter_dict = {
            "terms": {
                "metadata.source": ["nexacorp_tickets.csv", "customer_support_tickets_200k.csv", "customer_support_tickets_200k.csv.bak"]
            }
        }
        docs, score = await retriever_mcp.search_docs(expanded_q, index_name="nexacorp_docs", k=5, filter_dict=filter_dict)
    else:
        trace.append(f"🎫 [Local Edge] Tool: Ticket Lookup — searching Chroma+BM25 for: '{expanded_q[:60]}...'")
        docs, score = retriever_local.search_tickets(expanded_q, k=5)

    graph_ctx = build_graph_context(entities, retriever_local, trace)
    citations = [{"source": d.metadata.get("source", "?"), "snippet": format_document(d)[:300]} for d in docs]
    trace.append(f"📊 Retrieved {len(docs)} tickets, score: {score:.4f}")
    return {"documents": docs, "graph_context": graph_ctx, "retrieval_score": score,
            "citations": citations, "tool_used": "🎫 Ticket Lookup", "reasoning_trace": trace}

async def tool_summarize(state, retriever_local, retriever_mcp):
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

    docs = apply_rbac_filter(docs, role, trace)
    graph_ctx = build_graph_context(entities, retriever_local, trace)
    citations = [{"source": d.metadata.get("source", "?"), "snippet": format_document(d)[:300]} for d in docs]
    trace.append(f"📊 Retrieved {len(docs)} docs from all sources")
    return {"documents": docs, "graph_context": graph_ctx, "retrieval_score": score,
            "citations": citations, "tool_used": "📋 Summarizer", "reasoning_trace": trace}

async def tool_filtered_tickets(state, retriever_local, retriever_mcp):
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
        must_filters = [{"terms": {"metadata.source": ["nexacorp_tickets.csv", "customer_support_tickets_200k.csv", "customer_support_tickets_200k.csv.bak"]}}]
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

    graph_ctx = build_graph_context(entities, retriever_local, trace)
    citations = [{"source": d.metadata.get("source", "?"), "snippet": format_document(d)[:300]} for d in docs]
    trace.append(f"📊 Retrieved {len(docs)} filtered tickets")
    return {"documents": docs, "graph_context": graph_ctx, "retrieval_score": score,
            "citations": citations, "tool_used": "🔎 Filtered Tickets", "reasoning_trace": trace}

async def tool_multihop(state, retriever_local, retriever_mcp):
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
            filter_tickets = {"terms": {"metadata.source": ["nexacorp_tickets.csv", "customer_support_tickets_200k.csv", "customer_support_tickets_200k.csv.bak"]}}
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

    docs1 = apply_rbac_filter(docs1, role, trace)
    graph_ctx = build_graph_context(entities, retriever_local, trace)

    all_docs = docs1 + docs2
    avg_score = (score1 + score2) / 2
    citations = [{"source": d.metadata.get("source", "?"), "snippet": format_document(d)[:300]} for d in all_docs]
    trace.append(f"📊 Multi-hop: {len(docs1)} doc results + {len(docs2)} ticket results")

    return {"documents": all_docs, "graph_context": graph_ctx, "retrieval_score": avg_score,
            "citations": citations, "tool_used": "🔗 Multi-hop Search", "reasoning_trace": trace}
