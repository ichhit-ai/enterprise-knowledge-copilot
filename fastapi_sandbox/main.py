import os
import sys
import asyncio
import concurrent.futures

# Try loading from .streamlit/secrets.toml if env variables are missing
for key in ["ES_URL", "ES_API_KEY", "GROQ_API_KEY"]:
    if not os.environ.get(key):
        try:
            import tomllib
            secrets_path = os.path.join(os.path.dirname(__file__), "..", ".streamlit", "secrets.toml")
            if os.path.exists(secrets_path):
                with open(secrets_path, "rb") as f:
                    secrets = tomllib.load(f)
                    if key in secrets:
                        os.environ[key] = str(secrets[key])
        except Exception:
            pass
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Scale ThreadPoolExecutor to prevent thread queuing for LangGraph sync nodes
executor = concurrent.futures.ThreadPoolExecutor(max_workers=500)
asyncio.get_event_loop().set_default_executor(executor)

# Add parent directory to sys.path so we can import src.agent
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from src.agent import build_agent
    index_full_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".index_full"))
    index_dir = index_full_path if os.path.exists(index_full_path) else None
    agent, retriever = build_agent(index_dir=index_dir)
except Exception as e:
    agent, retriever = None, None
    print(f"Error loading agent: {e}")

app = FastAPI(title="NexaCorp Enterprise Copilot - FastAPI Sandbox")

class ChatRequest(BaseModel):
    message: str
    role: str = "Employee"
    mode: str = "local"
    history: list = []

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    if not agent:
        raise HTTPException(
            status_code=500, 
            detail="LangGraph agent is not initialized. Make sure local Ollama or Elasticsearch is running."
        )
    
    try:
        # Run agent asynchronously
        result = await agent.ainvoke({
            "question": request.message,
            "role": request.role,
            "history": request.history,
            "mode": request.mode
        })
        
        return {
            "answer": result.get("answer", "No response generated."),
            "tool_used": result.get("tool_used", "Agent Node"),
            "confidence": result.get("confidence", 0.0),
            "retrieval_score": result.get("retrieval_score", 0.0),
            "faithfulness": result.get("faithfulness", 0.0),
            "semantic_similarity": result.get("semantic_similarity", 0.0),
            "context_relevance": result.get("context_relevance", 0.0),
            "reasoning_trace": result.get("reasoning_trace", []),
            "citations": result.get("citations", [])
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class SearchRequest(BaseModel):
    query: str
    mode: str = "local"

@app.post("/api/search")
async def search_endpoint(request: SearchRequest):
    if not agent:
        raise HTTPException(status_code=500, detail="Agent not initialized.")
    try:
        if request.mode == "elasticsearch":
            from src.retriever_mcp import HybridMCPRetriever
            global _retriever_mcp_cached
            if "_retriever_mcp_cached" not in globals():
                _retriever_mcp_cached = HybridMCPRetriever()
            # Use search_docs directly to query Elasticsearch
            docs, score = await _retriever_mcp_cached.search_docs(request.query, index_name="nexacorp_docs", k=5)
        else:
            docs, score = retriever.search_tickets(request.query, k=5)
        return {
            "docs_count": len(docs),
            "score": score
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404, detail="static/index.html not found.")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()

# Mount the static directory
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
