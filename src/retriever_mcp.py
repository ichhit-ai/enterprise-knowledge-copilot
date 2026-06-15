import asyncio
import os
import sys
import threading
import concurrent.futures
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document

# Persistent background loop to run all MCP operations and avoid cold starts on every Streamlit query
_mcp_loop = asyncio.new_event_loop()
def _start_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

_loop_thread = threading.Thread(target=_start_loop, args=(_mcp_loop,), daemon=True)
_loop_thread.start()

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
        self._session_info = None
        self._lock = None

    async def _get_session_info_impl(self):
        """Internal method executed strictly on _mcp_loop to initialize/return the session."""
        if self._session_info:
            return self._session_info

        if self._lock is None:
            self._lock = asyncio.Lock()

        # Initialize connection under the loop's lock
        async with self._lock:
            if self._session_info:
                return self._session_info

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

            self._session_info = {
                "session": session,
                "context": client_context,
                "search_tool": search_tool
            }
            return self._session_info

    async def get_session_info(self):
        """Thread-safe and loop-safe public method to get session info."""
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop != _mcp_loop:
            future = asyncio.run_coroutine_threadsafe(self._get_session_info_impl(), _mcp_loop)
            return await asyncio.wrap_future(future)
        else:
            return await self._get_session_info_impl()

    async def close(self):
        """Gracefully close active session."""
        if asyncio.get_running_loop() != _mcp_loop:
            future = asyncio.run_coroutine_threadsafe(self._close_impl(), _mcp_loop)
            return await asyncio.wrap_future(future)
        else:
            return await self._close_impl()

    async def _close_impl(self):
        if self._session_info:
            try:
                await self._session_info["session"].__aexit__(None, None, None)
            except Exception:
                pass
            try:
                await self._session_info["context"].__aexit__(None, None, None)
            except Exception:
                pass
            self._session_info = None

    async def search_docs(self, query_text: str, index_name: str = "nexacorp_docs", k: int = 5, filter_dict: dict = None):
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop != _mcp_loop:
            future = asyncio.run_coroutine_threadsafe(
                self._search_docs_impl(query_text, index_name, k, filter_dict),
                _mcp_loop
            )
            return await asyncio.wrap_future(future)
        else:
            return await self._search_docs_impl(query_text, index_name, k, filter_dict)

    async def _search_docs_impl(self, query_text: str, index_name: str = "nexacorp_docs", k: int = 5, filter_dict: dict = None):
        # 1. Expand and embed query
        from src.retriever import expand_query
        expanded_query = expand_query(query_text)
        query_vector = self.embeddings_model.embed_query(expanded_query)

        # 2. Get the running session info (already running on _mcp_loop)
        session_info = await self._get_session_info_impl()
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
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop != _mcp_loop:
            future = asyncio.run_coroutine_threadsafe(
                self._search_ticket_by_id_impl(ticket_id),
                _mcp_loop
            )
            return await asyncio.wrap_future(future)
        else:
            return await self._search_ticket_by_id_impl(ticket_id)

    async def _search_ticket_by_id_impl(self, ticket_id: str):
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

        session_info = await self._get_session_info_impl()
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
