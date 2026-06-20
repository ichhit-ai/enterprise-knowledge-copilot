import os
import sys
import time
from langchain_huggingface import HuggingFaceEmbeddings
from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from src.ingestion.ingest_full import load_text_files, load_csv_files, load_pdf_files, DATA_DIR

INDEX_NAME = "nexacorp_docs"

def get_elasticsearch_client():
    url = os.environ.get("ES_URL", "http://localhost:9200")
    api_key = os.environ.get("ES_API_KEY")
    username = os.environ.get("ES_USERNAME")
    password = os.environ.get("ES_PASSWORD")
    
    es_kwargs = {}
    if api_key:
        es_kwargs["api_key"] = api_key
    elif username and password:
        es_kwargs["basic_auth"] = (username, password)
        
    print(f"Connecting to Elasticsearch at {url}...")
    return Elasticsearch(url, **es_kwargs)

def recreate_index(es):
    if es.indices.exists(index=INDEX_NAME):
        print(f"Index '{INDEX_NAME}' already exists. Recreating it to run at 200k scale...")
        es.indices.delete(index=INDEX_NAME)

    # Define the mapping for both text search (BM25) and vector search (dense_vector)
    mapping = {
        "settings": {
            "index": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "refresh_interval": "-1"  # Disable refresh during bulk indexing for 10x performance boost
            }
        },
        "mappings": {
            "properties": {
                "page_content": {
                    "type": "text",
                    "analyzer": "standard"
                },
                "embeddings": {
                    "type": "dense_vector",
                    "dims": 384,
                    "index": True,
                    "similarity": "cosine"
                },
                "metadata": {
                    "type": "object",
                    "properties": {
                        "source": { "type": "keyword" },
                        "type": { "type": "keyword" },
                        "system": { "type": "keyword" },
                        "priority": { "type": "keyword" },
                        "status": { "type": "keyword" },
                        "error_code": { "type": "keyword" },
                        "ticket_id": { "type": "keyword" }
                    }
                }
            }
        }
    }
    es.indices.create(index=INDEX_NAME, body=mapping)
    print(f"Created index '{INDEX_NAME}' with vector mappings.")

def ingest_to_elasticsearch_full():
    # Load secrets dynamically from secrets.toml if not in env
    if not os.environ.get("ES_URL"):
        try:
            import tomllib
            secrets_path = os.path.join(os.path.dirname(__file__), "..", "..", ".streamlit", "secrets.toml")
            if os.path.exists(secrets_path):
                with open(secrets_path, "rb") as f:
                    secrets = tomllib.load(f)
                    for k, v in secrets.items():
                        os.environ[k] = str(v)
        except Exception:
            pass

    # Ensure local ES is used if running locally
    import getpass
    if getpass.getuser() == "ichhit":
        os.environ.pop("ES_URL", None)
        os.environ.pop("ES_API_KEY", None)

    es = get_elasticsearch_client()
    if not es.ping():
        raise ConnectionError("Could not connect to Elasticsearch.")

    recreate_index(es)

    print("Loading all files (including 200k tickets) and redacting PII...")
    start_time = time.time()
    docs = load_text_files(DATA_DIR) + load_csv_files(DATA_DIR) + load_pdf_files(DATA_DIR)
    print(f"Loaded {len(docs)} document chunks in {time.time() - start_time:.2f}s.")

    print("Initializing HuggingFace embedding model (BAAI/bge-small-en-v1.5) on GPU (CUDA)...")
    embeddings_model = HuggingFaceEmbeddings(
        model_name="BAAI/bge-small-en-v1.5",
        model_kwargs={"device": "cuda"},
        encode_kwargs={"batch_size": 256}
    )

    print("Generating embeddings and uploading to Elasticsearch in chunks...")
    chunk_size = 5000
    total_docs = len(docs)
    
    for start_idx in range(0, total_docs, chunk_size):
        chunk_docs = docs[start_idx:start_idx + chunk_size]
        print(f"Processing chunk {start_idx // chunk_size + 1}: docs {start_idx} to {min(start_idx + chunk_size, total_docs)}...")
        
        # 1. Embed current chunk contents on GPU
        texts = [doc.page_content for doc in chunk_docs]
        chunk_embeddings = embeddings_model.embed_documents(texts)
        
        # 2. Build bulk actions list
        actions = []
        for offset, doc in enumerate(chunk_docs):
            idx = start_idx + offset
            actions.append({
                "_index": INDEX_NAME,
                "_id": f"doc_{idx}",
                "_source": {
                    "page_content": doc.page_content,
                    "embeddings": chunk_embeddings[offset],
                    "metadata": doc.metadata
                }
            })
            
        # 3. Upload chunk via Elasticsearch Bulk API
        success, failed = bulk(es, actions, chunk_size=2000, request_timeout=300)
        print(f"  Successfully indexed {success} documents in this chunk. Failures: {len(failed) if isinstance(failed, list) else failed}")

    # Reset refresh interval to auto and refresh the index
    print("Re-enabling index refresh...")
    es.indices.put_settings(index=INDEX_NAME, body={"index": {"refresh_interval": "1s"}})
    es.indices.refresh(index=INDEX_NAME)
    
    print(f"Indexing complete! Total time taken: {time.time() - start_time:.2f}s.")

if __name__ == "__main__":
    ingest_to_elasticsearch_full()
