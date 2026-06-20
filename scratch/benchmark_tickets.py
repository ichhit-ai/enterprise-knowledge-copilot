import os
import sys
import time
import json
import csv
import asyncio
import numpy as np
import psutil
import subprocess
import matplotlib.pyplot as plt

# Setup import path to access src directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils import classify_query, extract_entities, extract_ticket_id
from src.retriever import Retriever
from src.retriever_mcp import HybridMCPRetriever
from src.tools.generate import generate_tickets

# Global parameters
WARMUP_IDS = [f"TKT-{11900 + i}" for i in range(10)]
NUM_REPEATS = 5
REQUESTS_PER_REPEAT = 50
TOTAL_REQUESTS = NUM_REPEATS * REQUESTS_PER_REPEAT

# Helpers for system stats
def get_docker_stats():
    """Get Elasticsearch container CPU and Memory usage using podman/docker stats."""
    try:
        res = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}},{{.MemUsage}}", "elasticsearch"],
            capture_output=True, text=True, check=True
        )
        output = res.stdout.strip()
        lines = [line for line in output.split('\n') if '%' in line]
        if lines:
            parts = lines[0].split(',')
            cpu_str = parts[0].strip().replace('%', '')
            mem_str = parts[1].split('/')[0].strip()
            
            # Convert mem_str (e.g., "317.8MB", "1.2GB") to MB
            val = float(''.join(c for c in mem_str if c.isdigit() or c == '.'))
            if 'GB' in mem_str or 'GiB' in mem_str:
                val *= 1024
            return float(cpu_str), val
    except Exception:
        pass
    return 0.0, 0.0

def get_python_stats():
    """Get Python process CPU and Memory (RSS) in MB."""
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    return process.cpu_percent(interval=None), mem_info.rss / (1024 * 1024)

# Keep track of peak memory in background/polls
peak_python_mem = 0.0
def update_peak_mem():
    global peak_python_mem
    _, mem = get_python_stats()
    if mem > peak_python_mem:
        peak_python_mem = mem

class BenchmarkRunner:
    def __init__(self):
        print("Initializing retrievers (Local & ES MCP)...")
        # Load local retriever (loads index and local dictionaries)
        self.retriever_local = Retriever(index_dir=".index_full")
        self.retriever_mcp = HybridMCPRetriever()
        
    async def warmup(self):
        print("\n🔥 Running 10 warmup requests against Local and ES stack...")
        # Force session initialization
        await self.retriever_mcp.get_session_info()
        
        for t_id in WARMUP_IDS:
            # Local
            route = classify_query(t_id)
            tkt_id = extract_ticket_id(t_id)
            doc_local = self.retriever_local.search_ticket_by_id(tkt_id)
            if doc_local:
                state = {"question": t_id, "documents": [doc_local]}
                _ = generate_tickets(state, None, None)
                
            # ES
            doc_es = await self.retriever_mcp.search_ticket_by_id(tkt_id)
            if doc_es:
                state = {"question": t_id, "documents": [doc_es]}
                _ = generate_tickets(state, None, None)
        print("✅ Warmup complete.")

    async def run_single_request(self, ticket_id, mode):
        start_wall = time.perf_counter()
        
        # 1. Router stage
        t0 = time.perf_counter()
        route = await asyncio.to_thread(classify_query, ticket_id)
        tkt_id = await asyncio.to_thread(extract_ticket_id, ticket_id)
        router_ms = (time.perf_counter() - t0) * 1000
        
        # 2. Retrieval stage
        t0 = time.perf_counter()
        if mode == "local":
            doc = await asyncio.to_thread(self.retriever_local.search_ticket_by_id, tkt_id)
        else:
            doc = await self.retriever_mcp.search_ticket_by_id(tkt_id)
        retrieval_ms = (time.perf_counter() - t0) * 1000
        
        # 3. Reranker stage (None for exact ticket lookup)
        reranker_ms = 0.0
        
        # 4. LLM stage (None due to bypass)
        t0 = time.perf_counter()
        if doc:
            state = {"question": ticket_id, "documents": [doc]}
            res = await asyncio.to_thread(generate_tickets, state, None, None)
            ans = res.get("answer", "")
            success = len(ans) > 0
            resp_len = len(ans)
        else:
            success = False
            resp_len = 0
        llm_ms = (time.perf_counter() - t0) * 1000
        
        end_wall = time.perf_counter()
        total_ms = (end_wall - start_wall) * 1000
        
        return {
            "ticket_id": ticket_id,
            "start_time": start_wall,
            "end_time": end_wall,
            "latency_ms": total_ms,
            "router_ms": router_ms,
            "retrieval_ms": retrieval_ms,
            "reranker_ms": reranker_ms,
            "llm_ms": llm_ms,
            "success": success,
            "response_length": resp_len
        }

    async def run_concurrency_batch(self, queries, mode):
        tasks = [self.run_single_request(q, mode) for q in queries]
        batch_start = time.perf_counter()
        results = await asyncio.gather(*tasks)
        batch_end = time.perf_counter()
        
        # Collect wall-clock times relative to batch start
        for r in results:
            r["relative_start"] = (r["start_time"] - batch_start) * 1000
            r["relative_end"] = (r["end_time"] - batch_start) * 1000
            
        wall_clock_ms = (batch_end - batch_start) * 1000
        return results, wall_clock_ms

    async def run_benchmark(self):
        await self.warmup()
        
        raw_results = {"local": [], "elasticsearch": []}
        wall_times = {"local": [], "elasticsearch": []}
        
        # Store system metrics before and after each mode
        system_stats = {}
        
        for mode in ["local", "elasticsearch"]:
            print(f"\n📊 Starting Benchmark for Mode: {mode} (5 repeats of 50 concurrent requests)...")
            
            # Stats before benchmark
            py_cpu_before, py_mem_before = get_python_stats()
            es_cpu_before, es_mem_before = get_docker_stats()
            
            mode_results = []
            mode_wall_times = []
            
            for repeat in range(NUM_REPEATS):
                # Ensure new ticket IDs for each repeat to avoid duplicate queries
                # Repeat 0: 12000-12049, Repeat 1: 12050-12099, etc.
                start_idx = 12000 + (repeat * REQUESTS_PER_REPEAT)
                queries = [f"TKT-{start_idx + i}" for i in range(REQUESTS_PER_REPEAT)]
                
                print(f"  -> Repeat {repeat + 1}/5 (Tickets {queries[0]} - {queries[-1]})...")
                
                results, wall_ms = await self.run_concurrency_batch(queries, mode)
                mode_results.extend(results)
                mode_wall_times.append(wall_ms)
                
                # Update peak memory
                update_peak_mem()
                # A short rest to let system settle
                await asyncio.sleep(0.1)
                
            # Stats after benchmark
            py_cpu_after, py_mem_after = get_python_stats()
            es_cpu_after, es_mem_after = get_docker_stats()
            
            system_stats[mode] = {
                "py_cpu_before": py_cpu_before, "py_cpu_after": py_cpu_after,
                "py_mem_before": py_mem_before, "py_mem_after": py_mem_after,
                "es_cpu_before": es_cpu_before, "es_cpu_after": es_cpu_after,
                "es_mem_before": es_mem_before, "es_mem_after": es_mem_after,
            }
            
            raw_results[mode] = mode_results
            wall_times[mode] = mode_wall_times
            
        return raw_results, wall_times, system_stats

def compute_metrics(raw_reqs, wall_times):
    latencies = [r["latency_ms"] for r in raw_reqs]
    router_times = [r["router_ms"] for r in raw_reqs]
    retrieval_times = [r["retrieval_ms"] for r in raw_reqs]
    reranker_times = [r["reranker_ms"] for r in raw_reqs]
    llm_times = [r["llm_ms"] for r in raw_reqs]
    
    total_reqs = len(raw_reqs)
    successes = sum(1 for r in raw_reqs if r["success"])
    failures = total_reqs - successes
    
    # Throughput calculation: Total requests divided by total wall-clock time across all 5 repeats
    total_wall_clock_s = sum(wall_times) / 1000.0
    rps = total_reqs / total_wall_clock_s if total_wall_clock_s > 0 else 0
    
    avg_lat = np.mean(latencies)
    p50 = np.percentile(latencies, 50)
    p90 = np.percentile(latencies, 90)
    p95 = np.percentile(latencies, 95)
    p99 = np.percentile(latencies, 99)
    min_lat = np.min(latencies)
    max_lat = np.max(latencies)
    
    std_dev = np.std(latencies)
    variance = np.var(latencies)
    
    # Sort for fastest/slowest
    sorted_reqs = sorted(raw_reqs, key=lambda x: x["latency_ms"])
    fastest_5 = sorted_reqs[:5]
    slowest_5 = sorted_reqs[-5:]
    
    # Concurrency calculations relative to batch start
    rel_ends = [r["relative_end"] for r in raw_reqs]
    rel_starts = [r["relative_start"] for r in raw_reqs]
    time_to_first_resp = np.min(rel_ends)
    time_to_last_resp = np.max(rel_ends)
    avg_concurrent_completion = np.mean(rel_ends)
    
    return {
        "latencies": latencies,
        "router_times": router_times,
        "retrieval_times": retrieval_times,
        "reranker_times": reranker_times,
        "llm_times": llm_times,
        "avg_latency": avg_lat,
        "p50": p50,
        "p90": p90,
        "p95": p95,
        "p99": p99,
        "min_latency": min_lat,
        "max_latency": max_lat,
        "total_requests": total_reqs,
        "successful_requests": successes,
        "failed_requests": failures,
        "rps": rps,
        "std_dev": std_dev,
        "variance": variance,
        "fastest_5": [{"ticket_id": r["ticket_id"], "latency": r["latency_ms"]} for r in fastest_5],
        "slowest_5": [{"ticket_id": r["ticket_id"], "latency": r["latency_ms"]} for r in slowest_5],
        "total_wall_clock_ms": sum(wall_times),
        "avg_concurrent_completion_ms": avg_concurrent_completion,
        "time_to_first_response_ms": time_to_first_resp,
        "time_to_last_response_ms": time_to_last_resp
    }

def print_pretty_table(local_metrics, es_metrics, system_stats):
    print("\n" + "="*80)
    print("                      CONCURRENT BENCHMARK RESULTS TABLE")
    print("="*80)
    print(f"{'Metric':<35} | {'Local Mode (CSV)':<20} | {'Elasticsearch (MCP)':<20}")
    print("-"*80)
    
    # Latency Metrics
    print(f"{'Min Latency':<35} | {local_metrics['min_latency']:>16.2f} ms | {es_metrics['min_latency']:>16.2f} ms")
    print(f"{'Average Latency':<35} | {local_metrics['avg_latency']:>16.2f} ms | {es_metrics['avg_latency']:>16.2f} ms")
    print(f"{'Median Latency (P50)':<35} | {local_metrics['p50']:>16.2f} ms | {es_metrics['p50']:>16.2f} ms")
    print(f"{'P90 Latency':<35} | {local_metrics['p90']:>16.2f} ms | {es_metrics['p90']:>16.2f} ms")
    print(f"{'P95 Latency':<35} | {local_metrics['p95']:>16.2f} ms | {es_metrics['p95']:>16.2f} ms")
    print(f"{'P99 Latency':<35} | {local_metrics['p99']:>16.2f} ms | {es_metrics['p99']:>16.2f} ms")
    print(f"{'Max Latency':<35} | {local_metrics['max_latency']:>16.2f} ms | {es_metrics['max_latency']:>16.2f} ms")
    print("-"*80)
    
    # Throughput Metrics
    print(f"{'Total / Success / Failed':<35} | {local_metrics['total_requests']}/{local_metrics['successful_requests']}/{local_metrics['failed_requests']} | {es_metrics['total_requests']}/{es_metrics['successful_requests']}/{es_metrics['failed_requests']}")
    print(f"{'Throughput (Requests/Sec)':<35} | {local_metrics['rps']:>16.2f} /s | {es_metrics['rps']:>16.2f} /s")
    print("-"*80)
    
    # Stability
    print(f"{'Standard Deviation':<35} | {local_metrics['std_dev']:>16.2f} ms | {es_metrics['std_dev']:>16.2f} ms")
    print(f"{'Variance':<35} | {local_metrics['variance']:>16.2f} ms | {es_metrics['variance']:>16.2f} ms")
    print("-"*80)
    
    # Concurrency
    print(f"{'Total Wall Clock Time':<35} | {local_metrics['total_wall_clock_ms']:>16.2f} ms | {es_metrics['total_wall_clock_ms']:>16.2f} ms")
    print(f"{'Avg Concurrent Completion':<35} | {local_metrics['avg_concurrent_completion_ms']:>16.2f} ms | {es_metrics['avg_concurrent_completion_ms']:>16.2f} ms")
    print(f"{'Time until first response':<35} | {local_metrics['time_to_first_response_ms']:>16.2f} ms | {es_metrics['time_to_first_response_ms']:>16.2f} ms")
    print(f"{'Time until last response':<35} | {local_metrics['time_to_last_response_ms']:>16.2f} ms | {es_metrics['time_to_last_response_ms']:>16.2f} ms")
    print("-"*80)
    
    # Python system metrics
    l_stats = system_stats["local"]
    e_stats = system_stats["elasticsearch"]
    print(f"{'Python CPU Before -> After':<35} | {l_stats['py_cpu_before']:.1f}% -> {l_stats['py_cpu_after']:.1f}% | {e_stats['py_cpu_before']:.1f}% -> {e_stats['py_cpu_after']:.1f}%")
    print(f"{'Python RAM Before -> After':<35} | {l_stats['py_mem_before']:.1f}MB -> {l_stats['py_mem_after']:.1f}MB | {e_stats['py_mem_before']:.1f}MB -> {e_stats['py_mem_after']:.1f}MB")
    print(f"{'Elasticsearch CPU Before -> After':<35} | N/A | {e_stats['es_cpu_before']:.1f}% -> {e_stats['es_cpu_after']:.1f}%")
    print(f"{'Elasticsearch RAM Before -> After':<35} | N/A | {e_stats['es_mem_before']:.1f}MB -> {e_stats['es_mem_after']:.1f}MB")
    print("="*80)

async def run_benchmark_async():
    runner = BenchmarkRunner()
    raw_results, wall_times, system_stats = await runner.run_benchmark()
    
    # Compute metrics
    local_metrics = compute_metrics(raw_results["local"], wall_times["local"])
    es_metrics = compute_metrics(raw_results["elasticsearch"], wall_times["elasticsearch"])
    
    # System Peak memory
    global peak_python_mem
    
    # 1. Print console table
    print_pretty_table(local_metrics, es_metrics, system_stats)
    
    # Compute stage timings summary
    print("\n" + "="*80)
    print("                      STAGE TIMINGS BREAKDOWN (AVERAGE)")
    print("="*80)
    print(f"{'Stage':<25} | {'Local Mode (CSV)':<20} | {'Elasticsearch (MCP)':<20}")
    print("-"*80)
    print(f"{'Router Stage':<25} | {np.mean(local_metrics['router_times']):>16.4f} ms | {np.mean(es_metrics['router_times']):>16.4f} ms")
    print(f"{'Retrieval Stage':<25} | {np.mean(local_metrics['retrieval_times']):>16.4f} ms | {np.mean(es_metrics['retrieval_times']):>16.4f} ms")
    print(f"{'Reranker Stage':<25} | {np.mean(local_metrics['reranker_times']):>16.4f} ms | {np.mean(es_metrics['reranker_times']):>16.4f} ms")
    print(f"{'LLM Stage':<25} | {np.mean(local_metrics['llm_times']):>16.4f} ms | {np.mean(es_metrics['llm_times']):>16.4f} ms")
    print(f"{'Total End-to-End':<25} | {np.mean(local_metrics['latencies']):>16.4f} ms | {np.mean(es_metrics['latencies']):>16.4f} ms")
    print("="*80)
    
    # 2. Print Comparative Summary
    print("\n" + "="*40)
    print("          COMPARATIVE SUMMARY")
    print("="*40)
    print("Local Mode:")
    print(f"  P50 = {local_metrics['p50']:.2f} ms")
    print(f"  P95 = {local_metrics['p95']:.2f} ms")
    print(f"  P99 = {local_metrics['p99']:.2f} ms")
    print(f"  RPS = {local_metrics['rps']:.2f} req/s")
    print("\nElasticsearch Mode:")
    print(f"  P50 = {es_metrics['p50']:.2f} ms")
    print(f"  P95 = {es_metrics['p95']:.2f} ms")
    print(f"  P99 = {es_metrics['p99']:.2f} ms")
    print(f"  RPS = {es_metrics['rps']:.2f} req/s")
    
    # Speedup ratio and T-test for statistical significance
    speedup_p50 = float(es_metrics['p50'] / local_metrics['p50']) if local_metrics['p50'] > 0 else 0.0
    speedup_p95 = float(es_metrics['p95'] / local_metrics['p95']) if local_metrics['p95'] > 0 else 0.0
    
    # Two-sample t-test calculation:
    n1, n2 = len(local_metrics['latencies']), len(es_metrics['latencies'])
    v1, v2 = float(local_metrics['variance']), float(es_metrics['variance'])
    mean1, mean2 = float(local_metrics['avg_latency']), float(es_metrics['avg_latency'])
    t_val = float((mean2 - mean1) / np.sqrt((v1 / n1) + (v2 / n2)))
    
    # Critical value for df > 120 is roughly 1.96 for alpha=0.05
    sig = bool(abs(t_val) > 1.96)
    
    print("\n" + "="*40)
    print("        PERFORMANCE RATIOS")
    print("="*40)
    print(f"Local P50 Speedup: {speedup_p50:.2f}x faster than ES")
    print(f"Local P95 Speedup: {speedup_p95:.2f}x faster than ES")
    print(f"T-statistic: {t_val:.4f}")
    print(f"Is difference statistically significant (alpha=0.05)? {'YES' if sig else 'NO'}")
    print("="*40)

    # 3. Save raw request timings to benchmark_results.csv
    csv_path = "benchmark_results.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "mode", "ticket_id", "start_time", "end_time", "latency_ms",
            "router_ms", "retrieval_ms", "reranker_ms", "llm_ms", "success", "response_length"
        ])
        for r in raw_results["local"]:
            writer.writerow([
                "local", r["ticket_id"], r["start_time"], r["end_time"], r["latency_ms"],
                r["router_ms"], r["retrieval_ms"], r["reranker_ms"], r["llm_ms"], r["success"], r["response_length"]
            ])
        for r in raw_results["elasticsearch"]:
            writer.writerow([
                "elasticsearch", r["ticket_id"], r["start_time"], r["end_time"], r["latency_ms"],
                r["router_ms"], r["retrieval_ms"], r["reranker_ms"], r["llm_ms"], r["success"], r["response_length"]
            ])
    print(f"\nSaved raw timings to {csv_path}")

    # 4. Save summary statistics to benchmark_summary.json
    def clean_metrics_dict(m_dict):
        cleaned = {}
        for k, v in m_dict.items():
            if k == "latencies":
                continue
            if isinstance(v, (np.ndarray, list)):
                cleaned[k] = [float(x) if isinstance(x, (np.float32, np.float64)) else x for x in v]
            elif isinstance(v, (np.float32, np.float64)):
                cleaned[k] = float(v)
            elif isinstance(v, (np.int32, np.int64)):
                cleaned[k] = int(v)
            else:
                cleaned[k] = v
        return cleaned

    summary_data = {
        "local": clean_metrics_dict(local_metrics),
        "elasticsearch": clean_metrics_dict(es_metrics),
        "system_stats": system_stats,
        "peak_python_memory_mb": float(peak_python_mem),
        "statistical_significance": {
            "t_statistic": float(t_val),
            "is_significant": bool(sig),
            "speedup_p50": float(speedup_p50),
            "speedup_p95": float(speedup_p95)
        }
    }
    
    summary_path = "benchmark_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary_data, f, indent=4)
    print(f"Saved summary to {summary_path}")

    # 5. Generate Matplotlib plots
    plt.style.use('seaborn-v0_8-whitegrid' if 'seaborn-v0_8-whitegrid' in plt.style.available else 'default')
    
    # Plot 1: Latency Histograms
    plt.figure(figsize=(10, 6))
    plt.hist(local_metrics["latencies"], bins=30, alpha=0.6, label="Local Mode (CSV)", color="skyblue")
    plt.hist(es_metrics["latencies"], bins=30, alpha=0.6, label="Elasticsearch Mode (MCP)", color="salmon")
    plt.title("Latency Distribution Comparison (Exact Ticket ID Lookups)")
    plt.xlabel("Latency (ms)")
    plt.ylabel("Frequency")
    plt.legend()
    plt.tight_layout()
    plt.savefig("latency_histograms.png", dpi=200)
    plt.close()
    
    # Plot 2: Box plots
    plt.figure(figsize=(8, 6))
    plt.boxplot([local_metrics["latencies"], es_metrics["latencies"]], labels=["Local Mode (CSV)", "Elasticsearch Mode (MCP)"])
    plt.title("Latency Boxplot Comparison")
    plt.ylabel("Latency (ms)")
    plt.tight_layout()
    plt.savefig("latency_boxplots.png", dpi=200)
    plt.close()
    
    # Plot 3: Percentiles Plot
    percentile_labels = ["P50", "P90", "P95", "P99"]
    local_p = [local_metrics["p50"], local_metrics["p90"], local_metrics["p95"], local_metrics["p99"]]
    es_p = [es_metrics["p50"], es_metrics["p90"], es_metrics["p95"], es_metrics["p99"]]
    
    plt.figure(figsize=(10, 6))
    plt.plot(percentile_labels, local_p, marker='o', linestyle='-', linewidth=2, label="Local Mode (CSV)", color="skyblue")
    plt.plot(percentile_labels, es_p, marker='s', linestyle='-', linewidth=2, label="Elasticsearch Mode (MCP)", color="salmon")
    plt.title("Latency Percentile Comparison")
    plt.xlabel("Percentile")
    plt.ylabel("Latency (ms)")
    plt.yscale("log") # log scale helps see details when local is extremely fast
    plt.legend()
    plt.tight_layout()
    plt.savefig("percentile_plots.png", dpi=200)
    plt.close()
    
    print("Generated plots: latency_histograms.png, latency_boxplots.png, percentile_plots.png")

def main():
    asyncio.run(run_benchmark_async())

if __name__ == "__main__":
    main()
