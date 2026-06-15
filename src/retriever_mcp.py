import asyncio
import os
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document

class HybridMCPRetriever:
    def __init__(self):
        # Configuration to launch the Node.js Elastic MCP Server
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_dir = os.path.dirname(current_dir)
        
        # Check and install Node.js dependencies if not present
        node_modules_path = os.path.join(project_dir, "mcp-server", "node_modules")
        if not os.path.exists(node_modules_path):
            import subprocess
            print("Node.js dependencies not found. Installing mcp-server node modules...")
            try:
                subprocess.run(["npm", "install"], cwd=os.path.join(project_dir, "mcp-server"), check=True)
            except Exception as e:
                print(f"Warning: Failed to auto-install npm packages: {e}. Please ensure node and npm are installed.")

        index_js_path = os.path.join(project_dir, "mcp-server", "node_modules", "@elastic", "mcp-server-elasticsearch", "dist", "index.js")

        self.server_params = StdioServerParameters(
            command="node",
            args=[index_js_path],
            env={
                "ES_URL": "http://localhost:9200",
                "OTEL_LOG_LEVEL": "none",
                **os.environ
            }
        )
        raw_emb = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5", model_kwargs={"device": "cpu"})
        from src.retriever import CachedEmbeddings
        self.embeddings_model = CachedEmbeddings(raw_emb)
        # Loop-isolated sessions and locks to support concurrent event loops in Streamlit threads
        self._sessions = {}  # loop -> {"session": ClientSession, "context": stdio_client, "search_tool": str}
        self._locks = {}     # loop -> asyncio.Lock

    async def get_session_info(self):
        """Get or initialize the persistent MCP connection session info for the current loop."""
        current_loop = asyncio.get_running_loop()
        
        # Lock-free fast path for already initialized session
        if current_loop in self._sessions:
            return self._sessions[current_loop]
        
        # 1. Create a lock for the current loop if it doesn't exist yet
        if current_loop not in self._locks:
            self._locks[current_loop] = asyncio.Lock()
        
        # 2. Initialize connection under the loop's lock to ensure thread-safety and avoid concurrency conflicts
        async with self._locks[current_loop]:
            if current_loop in self._sessions:
                return self._sessions[current_loop]
                
            # Prune closed event loops to prevent memory leaks
            closed_loops = [l for l in self._sessions if l.is_closed()]
            for l in closed_loops:
                self._sessions.pop(l, None)
                self._locks.pop(l, None)

            if current_loop not in self._sessions:
                client_context = stdio_client(self.server_params)
                read, write = await client_context.__aenter__()
                session = ClientSession(read, write)
                await session.__aenter__()
                await session.initialize()

                # Find and cache the search tool name dynamically for this session
                search_tool = None
                tools_list = await session.list_tools()
                for tool in tools_list.tools:
                    if tool.name in ["elasticsearch_search", "search", "search_documents"]:
                        search_tool = tool.name
                        break

                if not search_tool:
                    raise RuntimeError(f"Could not find a search tool in the Elasticsearch MCP server. Available tools: {[t.name for t in tools_list.tools]}")

                self._sessions[current_loop] = {
                    "session": session,
                    "context": client_context,
                    "search_tool": search_tool
                }

            return self._sessions[current_loop]

    async def close(self):
        """Gracefully close all active sessions across all event loops."""
        for loop, info in list(self._sessions.items()):
            # Only attempt clean up if the loop is still open
            if not loop.is_closed():
                try:
                    await info["session"].__aexit__(None, None, None)
                except Exception:
                    pass
                try:
                    await info["context"].__aexit__(None, None, None)
                except Exception:
                    pass
        self._sessions.clear()
        self._locks.clear()

    async def search_docs(self, query_text: str, index_name: str = "nexacorp_docs", k: int = 5, filter_dict: dict = None):
        # 1. Expand and embed query
        from src.retriever import expand_query
        expanded_query = expand_query(query_text)
        query_vector = self.embeddings_model.embed_query(expanded_query)

        # 2. Get the running session info for the current loop
        session_info = await self.get_session_info()
        session = session_info["session"]
        search_tool = session_info["search_tool"]

        # 4. Construct query body with optional filtering
        query_body = {
            "match": {
                "page_content": expanded_query
            }
        }
        knn_body = {
            "field": "embeddings",
            "query_vector": query_vector,
            "k": k * 2,
            "num_candidates": 100,
            "boost": 15.0
        }

        if filter_dict:
            query_body = {
                "bool": {
                    "must": query_body,
                    "filter": filter_dict
                }
            }
            knn_body["filter"] = filter_dict

        # Combine query and knn (score-based hybrid search, compatible with highlighting)
        es_query = {
            "query": query_body,
            "knn": knn_body
        }

        # 5. Call the search tool on the running session
        response = await session.call_tool(
            name=search_tool,
            arguments={
                "index": index_name,
                "queryBody": es_query,
                "size": k
            }
        )

        # 6. Parse response content
        documents = []
        if not response.content or len(response.content) <= 1:
            return [], 0.0

        import json
        for item in response.content[1:]:
            text_content = item.text
            page_content = ""
            metadata = {}
            
            for line in text_content.split('\n'):
                if line.startswith("page_content: "):
                    val = line[len("page_content: "):].strip()
                    try:
                        page_content = json.loads(val)
                    except Exception:
                        page_content = val
                elif line.startswith("page_content (highlighted): "):
                    page_content = line[len("page_content (highlighted): "):].strip()
                elif line.startswith("metadata: "):
                    val = line[len("metadata: "):].strip()
                    try:
                        metadata = json.loads(val)
                    except Exception:
                        metadata = {"raw_metadata": val}
                        
            if not page_content:
                page_content = text_content
                
            documents.append(Document(
                page_content=page_content,
                metadata=metadata
            ))

        return documents, 1.0

    async def search_ticket_by_id(self, ticket_id: str):
        """Perform a direct exact match lookup for a ticket ID in Elasticsearch."""
        t_id_clean = str(ticket_id).upper()
        if t_id_clean.startswith("TKT-"):
            clean_num = t_id_clean[4:]
        else:
            clean_num = t_id_clean

        # We will check both 'TKT-XXXXX' and 'XXXXX' formats
        terms = [t_id_clean, clean_num]
        if not t_id_clean.startswith("TKT-"):
            terms.append(f"TKT-{t_id_clean}")

        session_info = await self.get_session_info()
        session = session_info["session"]
        search_tool = session_info["search_tool"]

        es_query = {
            "query": {
                "bool": {
                    "must": [
                        {
                            "terms": {
                                "metadata.ticket_id": terms
                            }
                        }
                    ],
                    "filter": [
                        {
                            "term": {
                                "metadata.source": "nexacorp_tickets.csv"
                            }
                        }
                    ]
                }
            }
        }

        response = await session.call_tool(
            name=search_tool,
            arguments={
                "index": "nexacorp_docs",
                "queryBody": es_query,
                "size": 1
            }
        )

        import json
        documents = []
        if response.content and len(response.content) > 1:
            for item in response.content[1:]:
                text_content = item.text
                page_content = ""
                metadata = {}
                
                for line in text_content.split('\n'):
                    if line.startswith("page_content: "):
                        val = line[len("page_content: "):].strip()
                        try:
                            page_content = json.loads(val)
                        except Exception:
                            page_content = val
                    elif line.startswith("page_content (highlighted): "):
                        page_content = line[len("page_content (highlighted): "):].strip()
                    elif line.startswith("metadata: "):
                        val = line[len("metadata: "):].strip()
                        try:
                            metadata = json.loads(val)
                        except Exception:
                            metadata = {"raw_metadata": val}
                            
                if not page_content:
                    page_content = text_content
                    
                documents.append(Document(
                    page_content=page_content,
                    metadata=metadata
                ))

        return documents[0] if documents else None

