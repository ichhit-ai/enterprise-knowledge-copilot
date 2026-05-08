import os
import pickle
import numpy as np
import networkx as nx
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

INDEX_DIR = os.path.join(os.path.dirname(__file__), "..", ".index")

# ── Abbreviation / synonym expansion map ──────────────────────────────────────
ABBREVIATION_MAP = {
    "vpn": "NEXAVPN", "ci": "BUILDPIPE-CI", "cd": "BUILDPIPE-CI",
    "db": "NEXACORE-DB", "database": "NEXACORE-DB", "fw": "NEXASEC-FW",
    "firewall": "NEXASEC-FW", "mail": "NEXAMAIL", "email": "NEXAMAIL",
    "hr": "HRPORTAL", "backup": "NEXABACKUP", "api": "APIGATEWAY-V2",
    "gateway": "AUTH-GATEWAY", "auth": "AUTH-GATEWAY", "monitor": "MONITORX",
    "monitoring": "MONITORX", "cloud": "CLOUDSYNC-S3", "s3": "CLOUDSYNC-S3",
    "sync": "CLOUDSYNC-S3", "ticket": "TICKETSYS", "jira": "TICKETSYS",
    "mfa": "AUTH-GATEWAY MFA", "sso": "AUTH-GATEWAY SSO",
    "ldap": "AUTH-GATEWAY LDAP", "dns": "NEXAVPN DNS",
    "vacation": "leave request", "time off": "leave request",
    "pto": "leave request", "holiday": "leave request",
    "password": "credential password reset", "cert": "certificate",
}


def expand_query(query):
    """Expand abbreviations and add synonyms to improve retrieval."""
    tokens = query.lower().split()
    expansions = []
    for tok in tokens:
        clean = tok.strip("?,.")
        if clean in ABBREVIATION_MAP:
            expansions.append(ABBREVIATION_MAP[clean])
    if expansions:
        return query + " " + " ".join(expansions)
    return query


class Retriever:
    def __init__(self):
        self.emb = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
        self.chroma = Chroma(persist_directory=os.path.join(INDEX_DIR, "chroma"),
                             embedding_function=self.emb)

        with open(os.path.join(INDEX_DIR, "bm25.pkl"), "rb") as f:
            self.bm25, self.bm25_docs = pickle.load(f)

        with open(os.path.join(INDEX_DIR, "graph.pkl"), "rb") as f:
            self.graph = pickle.load(f)

        # Cross-encoder reranker (lazy load)
        self._cross_encoder = None

    def _get_cross_encoder(self):
        if self._cross_encoder is None:
            try:
                from sentence_transformers import CrossEncoder
                self._cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
            except Exception:
                self._cross_encoder = False
        return self._cross_encoder if self._cross_encoder is not False else None

    # ── Embedding utilities ───────────────────────────────────────────────────
    def embed_text(self, text):
        """Embed a single text string, return numpy array."""
        return np.array(self.emb.embed_query(text))

    def cosine_similarity(self, a, b):
        """Compute cosine similarity between two vectors."""
        a, b = np.array(a), np.array(b)
        norm = np.linalg.norm(a) * np.linalg.norm(b)
        if norm == 0:
            return 0.0
        return float(np.dot(a, b) / norm)

    def compute_semantic_similarity(self, query, answer):
        """Cosine similarity between query and answer embeddings."""
        if not query or not answer:
            return 0.0
        q_emb = self.embed_text(query)
        a_emb = self.embed_text(answer)
        return round(self.cosine_similarity(q_emb, a_emb), 3)

    def compute_context_relevance(self, query, documents):
        """Average cosine similarity between query and each retrieved chunk."""
        if not query or not documents:
            return 0.0
        q_emb = self.embed_text(query)
        scores = []
        for doc in documents:
            d_emb = self.embed_text(doc.page_content[:500])
            scores.append(self.cosine_similarity(q_emb, d_emb))
        return round(sum(scores) / len(scores), 3) if scores else 0.0

    # ── Core search methods ───────────────────────────────────────────────────
    def search_semantic(self, query, k=5, filter_dict=None):
        q = expand_query(query)
        if filter_dict:
            results = self.chroma.similarity_search_with_score(q, k=k, filter=filter_dict)
        else:
            results = self.chroma.similarity_search_with_score(q, k=k)
        return [(doc, float(score)) for doc, score in results]

    def search_keyword(self, query, k=5, source_filter=None):
        q = expand_query(query)
        tokens = q.lower().split()
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        results = []
        for i in ranked:
            if scores[i] <= 0:
                break
            doc = self.bm25_docs[i]
            if source_filter and doc.metadata.get("source") != source_filter:
                continue
            results.append((doc, scores[i]))
            if len(results) >= k:
                break
        return results

    def search_graph(self, entities):
        results = []
        for entity in entities:
            matches = [n for n in self.graph.nodes if entity.lower() in n.lower()]
            for node in matches:
                out = [(node, self.graph[node][t].get("relation", "RELATED"), t)
                       for t in self.graph.successors(node)]
                inc = [(s, self.graph[s][node].get("relation", "RELATED"), node)
                       for s in self.graph.predecessors(node)]
                results.extend(out + inc)
        seen = set()
        deduped = []
        for triple in results:
            key = (triple[0], triple[2])
            if key not in seen:
                seen.add(key)
                deduped.append(triple)
        return deduped[:20]

    # ── Filtered search methods ───────────────────────────────────────────────
    def search_docs(self, query, k=5):
        sem = self.search_semantic(query, k, filter_dict={"type": "handbook"})
        kw = self.search_keyword(query, k, source_filter="nexacorp_handbook.txt")
        # Also search runbook documents
        sem_rb = self.search_semantic(query, k, filter_dict={"type": "handbook"})
        return self._rrf_fuse(query, sem + sem_rb, kw, k)

    def search_tickets(self, query, k=5):
        sem = self.search_semantic(query, k, filter_dict={"source": "nexacorp_tickets.csv"})
        kw = self.search_keyword(query, k, source_filter="nexacorp_tickets.csv")
        return self._rrf_fuse(query, sem, kw, k)

    def search_filtered_tickets(self, query, k=5, priority=None, system=None):
        """Metadata-aware ticket search with priority/system filters."""
        filter_conditions = {"source": "nexacorp_tickets.csv"}
        if priority:
            filter_conditions["priority"] = priority
        if system:
            filter_conditions["system"] = system

        # Use $and for multiple filters if ChromaDB supports it
        if len(filter_conditions) > 1:
            filter_dict = {"$and": [{k: v} for k, v in filter_conditions.items()]}
        else:
            filter_dict = filter_conditions

        sem = self.search_semantic(query, k, filter_dict=filter_dict)
        kw = self.search_keyword(query, k, source_filter="nexacorp_tickets.csv")
        # Filter BM25 results by metadata too
        if priority or system:
            filtered_kw = []
            for doc, score in kw:
                if priority and doc.metadata.get("priority") != priority:
                    continue
                if system and doc.metadata.get("system") != system:
                    continue
                filtered_kw.append((doc, score))
            kw = filtered_kw
        return self._rrf_fuse(query, sem, kw, k)

    def search_all(self, query, entities=None, k=5):
        sem = self.search_semantic(query, k)
        kw = self.search_keyword(query, k)
        docs, scores = self._rrf_fuse(query, sem, kw, k)
        graph_ctx = ""
        if entities:
            triples = self.search_graph(entities)
            if triples:
                graph_ctx = "\n".join(f"{s} --[{r}]--> {t}" for s, r, t in triples)
        return docs, scores, graph_ctx

    # ── Reranking ─────────────────────────────────────────────────────────────
    def _rerank(self, query, docs, k=5):
        """Cross-encoder reranking for top-k refinement."""
        encoder = self._get_cross_encoder()
        if encoder is None or len(docs) <= k:
            return docs[:k]
        pairs = [(query, d.page_content[:512]) for d in docs]
        scores = encoder.predict(pairs)
        ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
        return [doc for doc, _ in ranked[:k]]

    def _rrf_fuse(self, query, sem_results, kw_results, k):
        scored = {}
        for rank, (doc, dist) in enumerate(sem_results):
            key = doc.page_content[:120]
            scored[key] = {"doc": doc, "rrf": 0, "sim": 1 - min(dist, 1)}
            scored[key]["rrf"] += 1 / (60 + rank)

        for rank, (doc, bm_score) in enumerate(kw_results):
            key = doc.page_content[:120]
            if key not in scored:
                scored[key] = {"doc": doc, "rrf": 0, "sim": 0}
            scored[key]["rrf"] += 1 / (60 + rank)

        fused = sorted(scored.values(), key=lambda x: x["rrf"], reverse=True)
        # Get top-2k for reranking, then rerank to top-k
        candidates = [item["doc"] for item in fused[:k * 2]]
        reranked = self._rerank(
            query,
            candidates, k
        )
        # Use reranked if cross-encoder available, else use fused
        if self._get_cross_encoder():
            final_docs = reranked
        else:
            final_docs = [item["doc"] for item in fused[:k]]
        avg_score = sum(item["rrf"] for item in fused[:k]) / k if fused else 0
        return final_docs, avg_score
