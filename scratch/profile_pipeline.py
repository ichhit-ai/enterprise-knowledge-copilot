import os
import sys
import time
import asyncio
import getpass

# Setup import path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils import classify_query, extract_entities, extract_ticket_id
from src.retriever import Retriever
from src.retriever_mcp import HybridMCPRetriever
from src.agent import build_agent
from langchain_core.messages import HumanMessage

async def profile_query(question, role="IT Admin", mode="local", index_dir=".index_full"):
    print(f"\n==================================================")
    print(f"PROFILING QUERY: '{question}'")
    print(f"Role: {role} | Mode: {mode}")
    print(f"==================================================")

    # 1. Router Time
    t_start = time.time()
    route = classify_query(question)
    entities = extract_entities(question)
    t_router = time.time() - t_start
    print(f"⏱️ Router classification: {t_router*1000:.2f} ms (Route: '{route}', Entities: {entities})")

    # Initialize retrievers
    t0 = time.time()
    retriever_local = Retriever(index_dir=index_dir)
    retriever_mcp = HybridMCPRetriever()
    # Warm up session if ES
    if mode == "elasticsearch":
        await retriever_mcp.get_session_info()
    t_init = time.time() - t0
    print(f"⏱️ Retriever init/warmup: {t_init*1000:.2f} ms")

    # 2. Retrieval time
    t0 = time.time()
    tkt_id = extract_ticket_id(question)
    docs = []
    retrieval_method = ""
    
    if route == "tickets":
        if tkt_id:
            retrieval_method = f"exact ticket lookup ({tkt_id})"
            if mode == "elasticsearch":
                doc_res = await retriever_mcp.search_ticket_by_id(tkt_id)
            else:
                doc_res = retriever_local.search_ticket_by_id(tkt_id)
            docs = [doc_res] if doc_res else []
        else:
            retrieval_method = "semantic ticket lookup"
            if mode == "elasticsearch":
                docs, score = await retriever_mcp.search_docs(question, index_name="nexacorp_docs", k=5)
            else:
                docs, score = retriever_local.search_tickets(question, k=5)
    else:
        retrieval_method = "doc lookup"
        if mode == "elasticsearch":
            docs, score = await retriever_mcp.search_docs(question, index_name="nexacorp_docs", k=5)
        else:
            docs, score = retriever_local.search_docs(question, k=5)
            
    t_retrieval = time.time() - t0
    print(f"⏱️ Retrieval via {retrieval_method}: {t_retrieval*1000:.2f} ms (Retrieved {len(docs)} documents)")

    # 3. Cross-Encoder reranking (local retriever does this for search_docs/search_tickets)
    # Let's measure how long cross-encoder takes to score these docs
    t_rerank = 0.0
    if route != "tickets" or not tkt_id:
        if mode == "local" and docs:
            t0 = time.time()
            encoder = retriever_local._get_cross_encoder()
            if encoder:
                texts = [d.page_content[:512] for d in docs]
                pairs = [[question, txt] for txt in texts]
                scores = encoder.predict(pairs)
            t_rerank = time.time() - t0
            print(f"⏱️ Cross-encoder reranking: {t_rerank*1000:.2f} ms")

    # 4. LLM Generation vs Bypass
    t0 = time.time()
    is_bypass = False
    ans = ""
    
    # Simulate generate step bypass check
    from src.config import BYPASS_ENABLED
    if BYPASS_ENABLED:
        if route == "tickets" and tkt_id and docs:
            is_bypass = True
            ans = "⚡ Bypassed LLM and formatted template directly"
        elif route == "docs" and "who owns" in question.lower():
            is_bypass = True
            ans = "⚡ Bypassed LLM for owner lookup"
            
    if is_bypass:
        t_gen = time.time() - t0
        print(f"⏱️ Generation (LLM Bypass Triggered): {t_gen*1000:.4f} ms")
    else:
        # Actually call the model (Ollama or Groq)
        print("🤖 Invoking LLM (Groq/Ollama)...")
        # Initialize agent to get LLM
        agent, _ = build_agent(index_dir=index_dir)
        # We invoke the LLM directly with a mock prompt to time it
        t0 = time.time()
        groq_key = os.environ.get("GROQ_API_KEY")
        if groq_key:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(
                model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
                openai_api_key=groq_key,
                openai_api_base="https://api.groq.com/openai/v1",
                temperature=0
            )
        else:
            from langchain_ollama import ChatOllama
            llm = ChatOllama(model="llama3.2", temperature=0)
            
        resp = llm.invoke([HumanMessage(content=f"Explain briefly: {question}")])
        t_gen = time.time() - t0
        print(f"⏱️ LLM generation: {t_gen*1000:.2f} ms")

    total_time = t_router + t_retrieval + t_rerank + t_gen
    print(f"📈 Total execution path: {total_time*1000:.2f} ms")

async def main():
    # 1. Profile ticket exact lookup (local)
    await profile_query("TKT-10084", mode="local")
    # 2. Profile ticket exact lookup (ES)
    await profile_query("TKT-10084", mode="elasticsearch")
    
    # 3. Profile semantic doc query (local)
    await profile_query("How do I request time off?", mode="local")
    # 4. Profile semantic doc query (ES)
    await profile_query("How do I request time off?", mode="elasticsearch")

if __name__ == "__main__":
    # Ensure GROQ key is loaded from secrets if present
    secrets_path = ".streamlit/secrets.toml"
    if os.path.exists(secrets_path):
        with open(secrets_path) as f:
            for line in f:
                if "GROQ_API_KEY" in line:
                    key = line.split("=")[1].strip().replace('"', '').replace("'", "")
                    os.environ["GROQ_API_KEY"] = key
                    print("Loaded GROQ_API_KEY from secrets.")
    asyncio.run(main())
