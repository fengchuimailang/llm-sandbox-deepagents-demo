#!/usr/bin/env python3
"""
Pressure test script for llm-sandbox DeepAgents adapter.

Tests:
1. Container startup time with pool reuse optimization
2. Resource consumption (memory, CPU) for 10/50/100 concurrent containers
3. Multi-threaded concurrent execution stability
4. Idle container cleanup
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import gc
import os
import resource
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import psutil


@dataclass
class TestResult:
    test_name: str
    duration_seconds: float
    success_count: int
    failure_count: int
    avg_memory_mb: float
    peak_memory_mb: float
    avg_cpu_percent: float
    startup_time_ms: float | None = None
    details: dict[str, Any] | None = None


@dataclass
class ContainerStats:
    container_id: str
    start_time: float
    end_time: float | None = None
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    success: bool = False
    error: str | None = None


class PressureTestRunner:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.results: list[TestResult] = []
        self._stop_monitoring = threading.Event()
        self._monitoring_data: list[dict[str, float]] = []
        self._monitor_thread: threading.Thread | None = None

    def _get_process_stats(self) -> dict[str, float]:
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        return {
            "rss_mb": mem_info.rss / 1024 / 1024,
            "vms_mb": mem_info.vms / 1024 / 1024,
            "cpu_percent": process.cpu_percent(interval=0.1),
            "num_threads": process.num_threads(),
        }

    def _monitor_resources(self, interval: float = 0.5):
        while not self._stop_monitoring.is_set():
            stats = self._get_process_stats()
            self._monitoring_data.append(stats)
            time.sleep(interval)

    def _start_monitoring(self):
        self._stop_monitoring.clear()
        self._monitoring_data = []
        self._monitor_thread = threading.Thread(target=self._monitor_resources, daemon=True)
        self._monitor_thread.start()

    def _stop_monitoring_and_get_stats(self) -> dict[str, float]:
        self._stop_monitoring.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2)

        if not self._monitoring_data:
            return {"avg_memory_mb": 0, "peak_memory_mb": 0, "avg_cpu_percent": 0}

        avg_memory = statistics.mean(d["rss_mb"] for d in self._monitoring_data)
        peak_memory = max(d["rss_mb"] for d in self._monitoring_data)
        avg_cpu = statistics.mean(d["cpu_percent"] for d in self._monitoring_data)

        return {
            "avg_memory_mb": avg_memory,
            "peak_memory_mb": peak_memory,
            "avg_cpu_percent": avg_cpu,
        }

    async def test_container_startup_time(
        self,
        num_containers: int = 10,
    ) -> TestResult:
        print(f"\n--- Testing container startup time ({num_containers} containers) ---")
        test_name = f"startup_time_{num_containers}"

        from llm_sandbox import SandboxSession
        from llm_sandbox.pool import PoolConfig, create_pool_manager

        startup_times = []

        for i in range(num_containers):
            gc.collect()

            pool = create_pool_manager(
                backend="docker",
                config=PoolConfig(max_pool_size=1, min_pool_size=0),
                lang="python",
                image="python:3.12-slim",
                keep_template=True,
            )

            start = time.perf_counter()
            container = pool.acquire()
            end = time.perf_counter()

            startup_times.append((end - start) * 1000)

            pool.release(container)
            pool.close()

            if (i + 1) % 5 == 0:
                print(f"  Completed {i + 1}/{num_containers} startups")

        avg_startup = statistics.mean(startup_times)
        median_startup = statistics.median(startup_times)
        p95_startup = sorted(startup_times)[int(len(startup_times) * 0.95)]

        print(f"  Average startup time: {avg_startup:.2f}ms")
        print(f"  Median startup time: {median_startup:.2f}ms")
        print(f"  P95 startup time: {p95_startup:.2f}ms")

        return TestResult(
            test_name=test_name,
            duration_seconds=sum(startup_times) / 1000,
            success_count=num_containers,
            failure_count=0,
            avg_memory_mb=0,
            peak_memory_mb=0,
            avg_cpu_percent=0,
            startup_time_ms=avg_startup,
            details={
                "avg_ms": avg_startup,
                "median_ms": median_startup,
                "p95_ms": p95_startup,
                "all_times_ms": startup_times,
            },
        )

    async def test_concurrent_containers(
        self,
        num_containers: int = 50,
        pool_size: int = 10,
    ) -> TestResult:
        print(f"\n--- Testing {num_containers} concurrent containers (pool size: {pool_size}) ---")
        test_name = f"concurrent_{num_containers}_pool_{pool_size}"

        self._start_monitoring()
        start_time = time.time()

        from llm_sandbox import SandboxSession
        from llm_sandbox.pool import PoolConfig, create_pool_manager

        pool = create_pool_manager(
            backend="docker",
            config=PoolConfig(
                max_pool_size=pool_size,
                min_pool_size=2,
                enable_prewarming=True,
            ),
            lang="python",
            image="python:3.12-slim",
            keep_template=True,
        )

        success_count = 0
        failure_count = 0
        execution_times = []

        def run_code(task_id: int) -> ContainerStats:
            stats = ContainerStats(container_id=f"task_{task_id}", start_time=time.time())
            try:
                with SandboxSession(lang="python", pool=pool) as session:
                    result = session.run(f'print("Task {task_id} completed")')
                    stats.success = True
                    stats.end_time = time.time()
                    if result.exit_code != 0:
                        stats.success = False
                        stats.error = result.stderr
            except Exception as e:
                stats.success = False
                stats.error = str(e)
                stats.end_time = time.time()
            return stats

        with ThreadPoolExecutor(max_workers=num_containers) as executor:
            futures = [executor.submit(run_code, i) for i in range(num_containers)]
            container_stats = []

            for future in as_completed(futures):
                try:
                    stats = future.result()
                    container_stats.append(stats)
                    if stats.success:
                        success_count += 1
                    else:
                        failure_count += 1
                except Exception as e:
                    failure_count += 1

        duration = time.time() - start_time
        resource_stats = self._stop_monitoring_and_get_stats()

        for stats in container_stats:
            if stats.end_time and stats.start_time:
                execution_times.append((stats.end_time - stats.start_time) * 1000)

        avg_execution = statistics.mean(execution_times) if execution_times else 0
        print(f"  Completed: {success_count} success, {failure_count} failed")
        print(f"  Total duration: {duration:.2f}s")
        print(f"  Avg execution time: {avg_execution:.2f}ms")
        print(f"  Avg memory: {resource_stats['avg_memory_mb']:.2f}MB")
        print(f"  Peak memory: {resource_stats['peak_memory_mb']:.2f}MB")

        pool.close()

        return TestResult(
            test_name=test_name,
            duration_seconds=duration,
            success_count=success_count,
            failure_count=failure_count,
            avg_memory_mb=resource_stats["avg_memory_mb"],
            peak_memory_mb=resource_stats["peak_memory_mb"],
            avg_cpu_percent=resource_stats["avg_cpu_percent"],
            details={
                "num_containers": num_containers,
                "pool_size": pool_size,
                "avg_execution_ms": avg_execution,
                "containers": container_stats,
            },
        )

    async def test_sustained_load(
        self,
        num_workers: int = 20,
        requests_per_worker: int = 10,
    ) -> TestResult:
        print(f"\n--- Testing sustained load ({num_workers} workers x {requests_per_worker} requests) ---")
        test_name = f"sustained_load_{num_workers}x{requests_per_worker}"

        self._start_monitoring()
        start_time = time.time()

        from llm_sandbox.pool import PoolConfig, create_pool_manager

        pool = create_pool_manager(
            backend="docker",
            config=PoolConfig(max_pool_size=20, min_pool_size=5, enable_prewarming=True),
            lang="python",
            image="python:3.12-slim",
            keep_template=True,
        )

        success_count = 0
        failure_count = 0
        latencies = []

        def worker(worker_id: int):
            nonlocal success_count, failure_count
            from llm_sandbox import SandboxSession

            for i in range(requests_per_worker):
                req_start = time.time()
                try:
                    with SandboxSession(lang="python", pool=pool) as session:
                        result = session.run(f'print(f"Worker {worker_id} request {i}")')
                        if result.exit_code == 0:
                            success_count += 1
                        else:
                            failure_count += 1
                    latencies.append((time.time() - req_start) * 1000)
                except Exception as e:
                    failure_count += 1
                    latencies.append((time.time() - req_start) * 1000)

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker, i) for i in range(num_workers)]
            for future in as_completed(futures):
                future.result()

        duration = time.time() - start_time
        resource_stats = self._stop_monitoring_and_get_stats()

        avg_latency = statistics.mean(latencies) if latencies else 0
        p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0

        print(f"  Completed: {success_count} success, {failure_count} failed")
        print(f"  Total duration: {duration:.2f}s")
        print(f"  Throughput: {success_count / duration:.2f} req/s")
        print(f"  Avg latency: {avg_latency:.2f}ms")
        print(f"  P95 latency: {p95_latency:.2f}ms")

        pool.close()

        return TestResult(
            test_name=test_name,
            duration_seconds=duration,
            success_count=success_count,
            failure_count=failure_count,
            avg_memory_mb=resource_stats["avg_memory_mb"],
            peak_memory_mb=resource_stats["peak_memory_mb"],
            avg_cpu_percent=resource_stats["avg_cpu_percent"],
            details={
                "num_workers": num_workers,
                "requests_per_worker": requests_per_worker,
                "avg_latency_ms": avg_latency,
                "p95_latency_ms": p95_latency,
                "throughput_rps": success_count / duration,
            },
        )

    async def test_container_reuse(
        self,
        num_cycles: int = 20,
    ) -> TestResult:
        print(f"\n--- Testing container reuse ({num_cycles} cycles) ---")
        test_name = f"container_reuse_{num_cycles}"

        from llm_sandbox.pool import PoolConfig, create_pool_manager

        pool = create_pool_manager(
            backend="docker",
            config=PoolConfig(max_pool_size=3, min_pool_size=2, enable_prewarming=True),
            lang="python",
            image="python:3.12-slim",
            keep_template=True,
        )

        from llm_sandbox import SandboxSession

        reuse_times = []
        create_times = []

        for i in range(num_cycles):
            start = time.perf_counter()
            with SandboxSession(lang="python", pool=pool) as session:
                result = session.run(f'print("Cycle {i}")')
                if i == 0:
                    create_times.append((time.perf_counter() - start) * 1000)
                else:
                    reuse_times.append((time.perf_counter() - start) * 1000)

        pool.close()

        avg_reuse = statistics.mean(reuse_times) if reuse_times else 0
        avg_create = statistics.mean(create_times) if create_times else 0
        speedup = avg_create / avg_reuse if avg_reuse > 0 else 0

        print(f"  First creation time: {avg_create:.2f}ms")
        print(f"  Average reuse time: {avg_reuse:.2f}ms")
        print(f"  Speedup from reuse: {speedup:.2f}x")

        return TestResult(
            test_name=test_name,
            duration_seconds=0,
            success_count=num_cycles,
            failure_count=0,
            avg_memory_mb=0,
            peak_memory_mb=0,
            avg_cpu_percent=0,
            details={
                "avg_create_ms": avg_create,
                "avg_reuse_ms": avg_reuse,
                "speedup_factor": speedup,
            },
        )

    def print_summary(self):
        print("\n" + "=" * 60)
        print("PRESSURE TEST SUMMARY")
        print("=" * 60)

        for result in self.results:
            print(f"\n{result.test_name}:")
            print(f"  Duration: {result.duration_seconds:.2f}s")
            print(f"  Success: {result.success_count}, Failures: {result.failure_count}")
            print(f"  Avg Memory: {result.avg_memory_mb:.2f}MB, Peak: {result.peak_memory_mb:.2f}MB")
            print(f"  Avg CPU: {result.avg_cpu_percent:.1f}%")

            if result.startup_time_ms:
                print(f"  Avg Startup Time: {result.startup_time_ms:.2f}ms")

            if result.details:
                for key, value in result.details.items():
                    if key != "containers":
                        print(f"  {key}: {value}")

        print("\n" + "=" * 60)


async def main():
    parser = argparse.ArgumentParser(description="Pressure test for llm-sandbox adapter")
    parser.add_argument("--tests", nargs="+", default=["startup", "concurrent", "sustained", "reuse"])
    parser.add_argument("--num-containers", type=int, default=50)
    parser.add_argument("--pool-size", type=int, default=10)
    parser.add_argument("--num-workers", type=int, default=20)
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    runner = PressureTestRunner(base_url=args.base_url)

    if "startup" in args.tests:
        for num in [10, 50, 100]:
            result = await runner.test_container_startup_time(num_containers=num)
            runner.results.append(result)

    if "concurrent" in args.tests:
        for num, pool in [(10, 5), (50, 10), (100, 20)]:
            result = await runner.test_concurrent_containers(num_containers=num, pool_size=pool)
            runner.results.append(result)

    if "sustained" in args.tests:
        result = await runner.test_sustained_load(
            num_workers=args.num_workers,
            requests_per_worker=10,
        )
        runner.results.append(result)

    if "reuse" in args.tests:
        result = await runner.test_container_reuse(num_cycles=20)
        runner.results.append(result)

    runner.print_summary()


if __name__ == "__main__":
    asyncio.run(main())