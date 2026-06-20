import os
import sys
import time
import random
import threading
import gc
from concurrent.futures import ThreadPoolExecutor
from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk
from rank_bm25 import BM25Okapi

# Connection config
ES_URL = "http://localhost:9200"
INDEX_NAME = "giga_1m"

# Sample pools
SYSTEMS = ["AUTH-GATEWAY", "NEXAVPN", "NEXACORE-DB", "HRPORTAL", "BUILDPIPE-CI", "MONITORX", "NEXASEC-FW", "CLOUDSYNC-S3", "TICKETSYS", "MAIL-RELAY"]
EMPLOYEES = ["Marcus", "Derek", "Priya", "Jordan", "Simone", "Lena", "Tomas", "Raj", "Farida", "Oliver"]
ERROR_CODES = ["ERR-9092", "DB-404X", "FW-3341", "VPN-7731", "SYNC-2201", "DISK-88A", "API-4429", "DOCKER-55X", "MAIL-8823", "POOL-99"]
PRIORITIES = ["P1", "P2", "P3", "P4"]
STATUSES = ["Open", "In Progress", "Escalated", "Resolved"]
VERBS = ["failed", "timed out", "alerted", "warned", "dropped", "errored", "deadlocked"]

# ── 1. DATA GENERATION ──
def generate_1m_data():
    print("Generating 1,000,000 documents with metadata in memory...")
    corpus = []
    for i in range(1000000):
        sys_name = random.choice(SYSTEMS)
        employee = random.choice(EMPLOYEES)
        err_code = random.choice(ERROR_CODES)
        priority = random.choice(PRIORITIES)
        status = random.choice(STATUSES)
        verb = random.choice(VERBS)
        
        # Keep string length short to optimize memory
        content = f"TKT-{400000+i}: {employee} reports {sys_name} {verb} ({err_code})."
        
        doc = {
            "page_content": content,
            "metadata": {
                "system": sys_name,
                "priority": priority,
                "status": status,
                "employee": employee
            }
        }
        corpus.append(doc)
    return corpus

# Helper to index Elasticsearch
def setup_es_index(es, corpus):
    if es.indices.exists(index=INDEX_NAME):
        es.indices.delete(index=INDEX_NAME)
    
    es.indices.create(
        index=INDEX_NAME,
        body={
            "settings": {
                "index": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "refresh_interval": "-1"  # Turn off refreshes during bulk load
                }
            },
            "mappings": {
                "properties": {
                    "page_content": { "type": "text", "analyzer": "standard" },
                    "metadata": {
                        "type": "object",
                        "properties": {
                            "system": { "type": "keyword" },
                            "priority": { "type": "keyword" },
                            "status": { "type": "keyword" },
                            "employee": { "type": "keyword" }
                        }
                    }
                }
            }
        }
    )
    
    print("Bulk uploading 1,000,000 documents to Elasticsearch...")
    actions = [
        {
            "_index": INDEX_NAME,
            "_id": str(i),
            "_source": doc
        }
        for i, doc in enumerate(corpus)
    ]
    
    batch_size = 10000
    for idx in range(0, len(actions), batch_size):
        bulk(es, actions[idx : idx + batch_size])
        if (idx + batch_size) % 100000 == 0:
            print(f"  Uploaded {idx + batch_size:,} / 1,000,000 documents...")
            
    # Restore normal settings and refresh
    print("Finalizing index configuration and forcing refresh...")
    es.indices.put_settings(index=INDEX_NAME, body={"index": {"refresh_interval": "1s"}})
    es.indices.refresh(index=INDEX_NAME)
    print("Elasticsearch index populated successfully.")

# ── 2. TEST 1: READ-WRITE CONTENTION ──
def test_read_write_contention(es, local_corpus):
    print("\n--- TEST 1: Read-Write Contention (Concurrency) ---")
    
    stop_signal = threading.Event()
    
    # Background writer logic
    def background_writer_es():
        counter = 1000000
        while not stop_signal.is_set():
            batch = []
            for _ in range(50):
                counter += 1
                batch.append({
                    "_index": INDEX_NAME,
                    "_id": str(counter),
                    "_source": {
                        "page_content": f"New ticket TKT-{counter}",
                        "metadata": {"system": "NEXAVPN", "priority": "P2", "status": "Open", "employee": "Priya"}
                    }
                })
            try:
                bulk(es, batch)
                es.indices.refresh(index=INDEX_NAME)
            except Exception:
                pass
            time.sleep(0.1)

    def background_writer_local():
        counter = 1000000
        while not stop_signal.is_set():
            for _ in range(50):
                counter += 1
                local_corpus.append({
                    "page_content": f"New ticket TKT-{counter}",
                    "metadata": {"system": "NEXAVPN", "priority": "P2", "status": "Open", "employee": "Priya"}
                })
            time.sleep(0.1)

    # 1. Benchmark Elasticsearch
    print("Starting Elasticsearch Read-Write Contention...")
    writer_thread = threading.Thread(target=background_writer_es)
    writer_thread.start()
    
    start_time = time.time()
    def search_es():
        for _ in range(10):
            es.search(
                index=INDEX_NAME,
                body={"query": {"match": {"page_content": "failed ticket alerted"}}},
                size=5
            )
            
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(search_es) for _ in range(4)]
        for f in futures:
            f.result()
            
    es_duration = time.time() - start_time
    stop_signal.set()
    writer_thread.join()
    
    # 2. Benchmark Local Python
    print("Starting Local Python Read-Write Contention...")
    stop_signal.clear()
    writer_thread = threading.Thread(target=background_writer_local)
    writer_thread.start()
    
    # Rebuild index once for starting
    tokenized = [doc["page_content"].lower().split() for doc in local_corpus]
    bm25 = BM25Okapi(tokenized)
    
    start_time = time.time()
    def search_local():
        for _ in range(10):
            tokens = ["failed", "ticket", "alerted"]
            scores = bm25.get_scores(tokens)
            top_idx = sorted(range(len(scores)), key=lambda idx: scores[idx], reverse=True)[:5]
            _ = [local_corpus[idx] for idx in top_idx]
            
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(search_local) for _ in range(4)]
        for f in futures:
            f.result()
            
    local_duration = time.time() - start_time
    stop_signal.set()
    writer_thread.join()
    
    print(f"Results - ES: {es_duration:.4f}s | Local Python: {local_duration:.4f}s")
    return es_duration, local_duration

# ── 3. TEST 2: COMPLEX METADATA FILTERING ──
def test_metadata_filtering(es, local_corpus):
    print("\n--- TEST 2: Complex Metadata Filtering (50 queries) ---")
    
    # Rebuild local index for purity
    tokenized = [doc["page_content"].lower().split() for doc in local_corpus]
    bm25 = BM25Okapi(tokenized)
    
    filter_queries = [
        ("deadlock error", "NEXACORE-DB", "P1", "Escalated"),
        ("MFA validation failure", "AUTH-GATEWAY", "P2", "Open"),
        ("disk capacity full", "MONITORX", "P3", "In Progress"),
        ("firewall packet drop", "NEXASEC-FW", "P1", "Resolved"),
        ("S3 cloud sync error", "CLOUDSYNC-S3", "P2", "Resolved")
    ] * 10 # 50 queries
    
    # 1. Local Python filtering
    print("Running Local Python filtering...")
    start_time = time.time()
    for text, sys_filter, pri_filter, stat_filter in filter_queries:
        filtered_docs = [
            doc for doc in local_corpus
            if doc["metadata"]["system"] == sys_filter
            and doc["metadata"]["priority"] == pri_filter
            and doc["metadata"]["status"] == stat_filter
        ]
        if filtered_docs:
            tokenized_sub = [doc["page_content"].lower().split() for doc in filtered_docs]
            sub_bm25 = BM25Okapi(tokenized_sub)
            scores = sub_bm25.get_scores(text.lower().split())
            top_idx = sorted(range(len(scores)), key=lambda idx: scores[idx], reverse=True)[:5]
            _ = [filtered_docs[idx] for idx in top_idx]
            
    local_duration = time.time() - start_time
    
    # 2. Elasticsearch native filtering
    print("Running Elasticsearch filtering...")
    start_time = time.time()
    for text, sys_filter, pri_filter, stat_filter in filter_queries:
        es.search(
            index=INDEX_NAME,
            body={
                "query": {
                    "bool": {
                        "must": {"match": {"page_content": text}},
                        "filter": [
                            {"term": {"metadata.system": sys_filter}},
                            {"term": {"metadata.priority": pri_filter}},
                            {"term": {"metadata.status": stat_filter}}
                        ]
                    }
                }
            },
            size=5
        )
    es_duration = time.time() - start_time
    
    print(f"Results - ES: {es_duration:.4f}s | Local Python: {local_duration:.4f}s")
    return es_duration, local_duration

# ── 4. TEST 3: AGGREGATE ANALYTICS ──
def test_aggregations(es, local_corpus):
    print("\n--- TEST 3: Aggregate Analytics (Faceting - 10 runs) ---")
    
    # 1. Local Python aggregate
    print("Running Local Python aggregations...")
    start_time = time.time()
    for _ in range(10):
        counts = {}
        for doc in local_corpus:
            sys_name = doc["metadata"]["system"]
            pri = doc["metadata"]["priority"]
            if sys_name not in counts:
                counts[sys_name] = {}
            counts[sys_name][pri] = counts[sys_name].get(pri, 0) + 1
    local_duration = time.time() - start_time
    
    # 2. Elasticsearch aggregation
    print("Running Elasticsearch aggregations...")
    start_time = time.time()
    for _ in range(10):
        es.search(
            index=INDEX_NAME,
            body={
                "size": 0,
                "aggs": {
                    "by_system": {
                        "terms": {"field": "metadata.system"},
                        "aggs": {
                            "by_priority": {
                                "terms": {"field": "metadata.priority"}
                            }
                        }
                    }
                }
            }
        )
    es_duration = time.time() - start_time
    
    print(f"Results - ES: {es_duration:.4f}s | Local Python: {local_duration:.4f}s")
    return es_duration, local_duration

# ── 5. THE COMBINED GIGA TEST ──
def run_giga_test(es, local_corpus):
    print("\n================== THE GIGA TEST (COMBINED LOAD) ==================")
    print("Simulating concurrent read/writes, nested filtering, and faceting simultaneously...")
    
    stop_signal = threading.Event()
    
    # Background writers
    def bg_writer_es():
        counter = 2000000
        while not stop_signal.is_set():
            batch = []
            for _ in range(50):
                counter += 1
                batch.append({
                    "_index": INDEX_NAME,
                    "_id": str(counter),
                    "_source": {
                        "page_content": f"Update ticket TKT-{counter}",
                        "metadata": {"system": "NEXACORE-DB", "priority": "P1", "status": "Escalated", "employee": "Tomas"}
                    }
                })
            try:
                bulk(es, batch)
                es.indices.refresh(index=INDEX_NAME)
            except Exception:
                pass
            time.sleep(0.1)

    def bg_writer_local():
        counter = 2000000
        while not stop_signal.is_set():
            for _ in range(50):
                counter += 1
                local_corpus.append({
                    "page_content": f"Update ticket TKT-{counter}",
                    "metadata": {"system": "NEXACORE-DB", "priority": "P1", "status": "Escalated", "employee": "Tomas"}
                })
            time.sleep(0.1)

    # 1. Run Elasticsearch Giga Test
    print("Starting Elasticsearch Combined Giga Test...")
    writer_thread = threading.Thread(target=bg_writer_es)
    writer_thread.start()
    
    start_time = time.time()
    def user_actions_es():
        for _ in range(5):
            es.search(
                index=INDEX_NAME,
                body={
                    "query": {
                        "bool": {
                            "must": {"match": {"page_content": "deadlock failed"}},
                            "filter": [{"term": {"metadata.system": "NEXACORE-DB"}}]
                        }
                    },
                    "aggs": {
                        "status_counts": {
                            "terms": {"field": "metadata.status"}
                        }
                    },
                    "size": 5
                }
            )
            
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(user_actions_es) for _ in range(6)]
        for f in futures:
            f.result()
            
    es_duration = time.time() - start_time
    stop_signal.set()
    writer_thread.join()
    
    # 2. Run Local Python Giga Test
    print("Starting Local Python Combined Giga Test...")
    stop_signal.clear()
    writer_thread = threading.Thread(target=bg_writer_local)
    writer_thread.start()
    
    tokenized = [doc["page_content"].lower().split() for doc in local_corpus]
    bm25 = BM25Okapi(tokenized)
    
    start_time = time.time()
    def user_actions_local():
        for _ in range(5):
            filtered = [doc for doc in local_corpus if doc["metadata"]["system"] == "NEXACORE-DB"]
            tokenized_sub = [doc["page_content"].lower().split() for doc in filtered]
            sub_bm25 = BM25Okapi(tokenized_sub)
            scores = sub_bm25.get_scores(["deadlock", "failed"])
            top_idx = sorted(range(len(scores)), key=lambda idx: scores[idx], reverse=True)[:5]
            _ = [filtered[idx] for idx in top_idx]
            
            counts = {}
            for doc in filtered:
                stat = doc["metadata"]["status"]
                counts[stat] = counts.get(stat, 0) + 1
                
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(user_actions_local) for _ in range(6)]
        for f in futures:
            f.result()
            
    local_duration = time.time() - start_time
    stop_signal.set()
    writer_thread.join()
    
    print(f"Results - ES: {es_duration:.4f}s | Local Python: {local_duration:.4f}s")
    return es_duration, local_duration

def main():
    es = Elasticsearch(ES_URL, request_timeout=60)
    if not es.ping():
        print("Error: Could not connect to Elasticsearch.")
        sys.exit(1)
        
    # Generate 1 Million corpus
    corpus = generate_1m_data()
    
    # Index to ES
    setup_es_index(es, corpus)
    
    # Run tests
    es_t1, py_t1 = test_read_write_contention(es, list(corpus))
    es_t2, py_t2 = test_metadata_filtering(es, list(corpus))
    es_t3, py_t3 = test_aggregations(es, list(corpus))
    es_giga, py_giga = run_giga_test(es, list(corpus))
    
    # Clean up index
    es.indices.delete(index=INDEX_NAME)
    
    # ── Print Final Dashboard ──
    print("\n" + "=" * 70)
    print("                    GIGA BENCHMARK REPORT (1M DOCS)                  ")
    print("=" * 70)
    print(f"{'Test Case':<32} | {'Local Python':<14} | {'Elasticsearch':<14} | {'Speedup':<8}")
    print("-" * 70)
    print(f"{'1. Read-Write Contention':<32} | {py_t1:<12.4f}s | {es_t1:<12.4f}s | {py_t1/es_t1:.1f}x ⚡")
    print(f"{'2. Complex Metadata Filtering':<32} | {py_t2:<12.4f}s | {es_t2:<12.4f}s | {py_t2/es_t2:.1f}x ⚡")
    print(f"{'3. Aggregate Analytics':<32} | {py_t3:<12.4f}s | {es_t3:<12.4f}s | {py_t3/es_t3:.1f}x ⚡")
    print("-" * 70)
    print(f"{'★ COMBINED GIGA TEST ★':<32} | {py_giga:<12.4f}s | {es_giga:<12.4f}s | {py_giga/es_giga:.1f}x ⚡")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    main()
