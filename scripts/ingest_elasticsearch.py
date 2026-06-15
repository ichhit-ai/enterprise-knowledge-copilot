import os
import sys
import csv
import glob
import re
import spacy
from datetime import datetime
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document

# Re-use ingestion parsing logic from src/ingest.py
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from src.ingest import load_text_files, load_csv_files, load_pdf_files, DATA_DIR

try:
    from elasticsearch import Elasticsearch
    from elasticsearch.helpers import bulk
except ImportError:
    print("Please install the elasticsearch python library first: pip install elasticsearch")
    sys.exit(1)

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

def create_index_if_not_exists(es):
    if es.indices.exists(index=INDEX_NAME):
        print(f"Index '{INDEX_NAME}' already exists. Recreating it...")
        es.indices.delete(index=INDEX_NAME)

    # Define the mapping for both text search (BM25) and vector search (dense_vector)
    mapping = {
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

def ingest_to_elasticsearch():
    es = get_elasticsearch_client()
    
    if not es.ping():
        raise ConnectionError("Could not connect to Elasticsearch. Please check your credentials and ensure your instance is running.")

    create_index_if_not_exists(es)

    print("Loading raw files and redacting PII...")
    docs = load_text_files(DATA_DIR) + load_csv_files(DATA_DIR) + load_pdf_files(DATA_DIR)
    print(f"Loaded {len(docs)} document chunks.")

    print("Initializing HuggingFace embedding model (BAAI/bge-small-en-v1.5)...")
    embeddings_model = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    
    print("Generating embeddings and preparing bulk payload...")
    actions = []
    for i, doc in enumerate(docs):
        # Generate the dense vector embedding for the page content
        vector = embeddings_model.embed_query(doc.page_content)
        
        action = {
            "_index": INDEX_NAME,
            "_id": f"doc_{i}",
            "_source": {
                "page_content": doc.page_content,
                "embeddings": vector,
                "metadata": doc.metadata
            }
        }
        actions.append(action)
        if (i + 1) % 100 == 0:
            print(f"  Processed {i + 1}/{len(docs)} documents...")

    print("Uploading to Elasticsearch via bulk API...")
    success, failed = bulk(es, actions)
    print(f"Done! Successfully indexed {success} documents. Failures: {len(failed) if isinstance(failed, list) else failed}")

if __name__ == "__main__":
    ingest_to_elasticsearch()
