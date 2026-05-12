"""
Concurrent Multi-User Tests for LLMSandboxBackend

Tests concurrent access from multiple users across threads.
"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pytest

from llm_sandbox_deepagents_adapter import (
    LLMSandboxBackendConfig,
    get_factory,
)


@dataclass
class UserTaskResult:
    user_id: str
    success: bool
    output: str = ""
    error: str = ""
    duration_ms: float = 0.0


class TestConcurrentMultiUser:
    """Test concurrent access from multiple users sharing one backend."""

    @pytest.fixture
    def factory(self):
        return get_factory()

    @pytest.fixture
    def config(self):
        return LLMSandboxBackendConfig(
            lang="python",
            max_pool_size=10,
            min_pool_size=3,
            idle_timeout=300.0,
            enable_prewarming=False,  # Disable prewarm to avoid slow startup
            default_timeout=30,
        )

    @pytest.mark.asyncio
    async def test_async_5_concurrent_users(self, factory, config):
        """Test 5 concurrent users sharing ONE backend instance.

        This is the production scenario: one backend/pool serves many users.
        5 users is enough to verify concurrency without overwhelming the system.
        """
        num_users = 5
        user_ids = [f"shared_user_{i}" for i in range(num_users)]

        async with factory.create_backend(config=config) as backend:
            async def run_user(user_id: str) -> UserTaskResult:
                start = time.monotonic()
                try:
                    result = await backend.async_execute(
                        f"print(f'Hello from {user_id}')",
                        timeout=30,
                    )
                    duration = (time.monotonic() - start) * 1000
                    return UserTaskResult(
                        user_id=user_id,
                        success=(result.exit_code == 0),
                        output=result.output or "",
                        duration_ms=duration,
                    )
                except Exception as e:
                    duration = (time.monotonic() - start) * 1000
                    return UserTaskResult(
                        user_id=user_id,
                        success=False,
                        error=str(e),
                        duration_ms=duration,
                    )

            results = await asyncio.gather(*[run_user(uid) for uid in user_ids])

        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]

        print(f"\n[async 5 concurrent shared] {len(successes)}/{num_users} succeeded")
        for r in successes:
            print(f"  OK {r.user_id}: {r.duration_ms:.0f}ms")
        if failures:
            for f in failures:
                print(f"  FAIL {f.user_id}: {f.error[:100]}")

        assert len(successes) == num_users, f"{len(failures)} users failed"

    @pytest.mark.asyncio
    async def test_async_same_user_10_concurrent_requests(self, factory, config):
        """Test one user making 10 concurrent requests to the SAME backend."""
        user_id = "same_user_stress"

        async with factory.create_backend(config=config) as backend:
            async def run_request(req_id: int) -> UserTaskResult:
                start = time.monotonic()
                try:
                    result = await backend.async_execute(
                        f"print(f'Request {req_id}')",
                        timeout=30,
                    )
                    duration = (time.monotonic() - start) * 1000
                    return UserTaskResult(
                        user_id=f"{user_id}_{req_id}",
                        success=(result.exit_code == 0),
                        duration_ms=duration,
                    )
                except Exception as e:
                    duration = (time.monotonic() - start) * 1000
                    return UserTaskResult(
                        user_id=f"{user_id}_{req_id}",
                        success=False,
                        error=str(e),
                        duration_ms=duration,
                    )

            results = await asyncio.gather(*[run_request(i) for i in range(10)])

        successes = [r for r in results if r.success]
        print(f"\n[same user 10 concurrent] {len(successes)}/10 succeeded")
        assert len(successes) == 10, f"{10 - len(successes)} requests failed"

    @pytest.mark.asyncio
    async def test_concurrent_write_and_read(self, factory, config):
        """Test concurrent write and read operations from multiple users."""
        async with factory.create_backend(config=config) as backend:
            async def user_write_read(user_id: str) -> UserTaskResult:
                start = time.monotonic()
                try:
                    # Write
                    write_result = await backend.async_write(
                        f"/workspace/concurrent_{user_id}.txt",
                        f"Content from {user_id}",
                    )
                    if write_result.error:
                        return UserTaskResult(user_id=user_id, success=False, error=write_result.error, duration_ms=0)

                    # Read
                    read_result = await backend.async_read(
                        f"/workspace/concurrent_{user_id}.txt",
                    )
                    if read_result.error:
                        return UserTaskResult(user_id=user_id, success=False, error=read_result.error, duration_ms=0)

                    duration = (time.monotonic() - start) * 1000
                    return UserTaskResult(
                        user_id=user_id,
                        success=True,
                        duration_ms=duration,
                    )
                except Exception as e:
                    duration = (time.monotonic() - start) * 1000
                    return UserTaskResult(
                        user_id=user_id,
                        success=False,
                        error=str(e),
                        duration_ms=duration,
                    )

            results = await asyncio.gather(*[user_write_read(f"user_{i}") for i in range(5)])

        successes = [r for r in results if r.success]
        print(f"\n[concurrent write+read] {len(successes)}/5 succeeded")
        assert len(successes) == 5


class TestThreadSafety:
    """Test thread-safety of the backend."""

    @pytest.fixture
    def factory(self):
        return get_factory()

    @pytest.fixture
    def config(self):
        return LLMSandboxBackendConfig(
            lang="python",
            max_pool_size=10,
            min_pool_size=3,
            idle_timeout=300.0,
            enable_prewarming=False,
            default_timeout=30,
        )

    def test_sync_concurrent_threads(self, factory, config):
        """Test shared backend accessed from multiple threads concurrently."""
        backend = factory.create_backend(config=config)

        def thread_worker(thread_id: int) -> UserTaskResult:
            user_id = f"thread_{thread_id}"
            start = time.monotonic()
            try:
                result = backend.execute(f"print(f'Thread {thread_id}')", timeout=30)
                duration = (time.monotonic() - start) * 1000
                return UserTaskResult(
                    user_id=user_id,
                    success=(result.exit_code == 0),
                    duration_ms=duration,
                )
            except Exception as e:
                duration = (time.monotonic() - start) * 1000
                return UserTaskResult(
                    user_id=user_id,
                    success=False,
                    error=str(e),
                    duration_ms=duration,
                )

        num_threads = 5
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(thread_worker, i) for i in range(num_threads)]
            results = [f.result() for f in as_completed(futures)]

        backend.close()

        successes = [r for r in results if r.success]
        print(f"\n[sync threads] {len(successes)}/{num_threads} succeeded")
        for r in successes:
            print(f"  OK {r.user_id}: {r.duration_ms:.0f}ms")
        assert len(successes) == num_threads, f"{num_threads - len(successes)} threads failed"

    def test_concurrent_stats_access(self, factory, config):
        """Test that get_stats() is safe to call concurrently from many threads."""
        backend = factory.create_backend(config=config)

        def get_stats_worker(worker_id: int) -> tuple[bool, str]:
            try:
                stats = backend.get_stats()
                return True, str(stats)
            except Exception as e:
                return False, str(e)

        num_threads = 20
        results = []
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(get_stats_worker, i) for i in range(num_threads)]
            for future in as_completed(futures):
                results.append(future.result())

        backend.close()

        successes = [r for r in results if r[0]]
        print(f"\n[stats concurrent] {len(successes)}/{num_threads} succeeded")
        assert len(successes) == num_threads, f"{num_threads - len(successes)} stats calls failed"


class TestSandboxManagerConcurrency:
    """Test the SandboxManager's handling of concurrent multi-user access.

    This tests the real-world scenario where the FastAPI service handles
    concurrent requests from different users, each getting their own sandbox.
    """

    @pytest.fixture
    def factory(self):
        return get_factory()

    @pytest.fixture
    def config(self):
        return LLMSandboxBackendConfig(
            lang="python",
            max_pool_size=10,
            min_pool_size=2,
            idle_timeout=60.0,
            enable_prewarming=False,
            default_timeout=30,
        )

    def test_rapid_user_sandbox_creation(self, factory, config):
        """Test creating backends for 3 different users rapidly.

        Each user gets their own backend with smaller pool to avoid
        overwhelming the system.
        """
        num_users = 3
        # Small pool per backend to avoid overwhelming the system
        small_config = LLMSandboxBackendConfig(
            lang="python",
            max_pool_size=3,
            min_pool_size=1,
            idle_timeout=60.0,
            enable_prewarming=False,
            default_timeout=30,
        )

        def create_and_execute(user_id: str) -> UserTaskResult:
            start = time.monotonic()
            try:
                backend = factory.create_backend(config=small_config)
                result = backend.execute(
                    f"print(f'Sandbox for {user_id}')",
                    timeout=30,
                )
                backend.close()
                duration = (time.monotonic() - start) * 1000
                return UserTaskResult(
                    user_id=user_id,
                    success=(result.exit_code == 0),
                    duration_ms=duration,
                )
            except Exception as e:
                duration = (time.monotonic() - start) * 1000
                return UserTaskResult(
                    user_id=user_id,
                    success=False,
                    error=str(e),
                    duration_ms=duration,
                )

        results = []
        with ThreadPoolExecutor(max_workers=num_users) as executor:
            futures = [executor.submit(create_and_execute, f"manager_user_{i}") for i in range(num_users)]
            for future in as_completed(futures):
                results.append(future.result())

        successes = [r for r in results if r.success]
        print(f"\n[rapid user creation] {len(successes)}/{num_users} succeeded")
        for r in successes:
            print(f"  OK {r.user_id}: {r.duration_ms:.0f}ms")
        if len(successes) < num_users:
            for r in results:
                if not r.success:
                    print(f"  FAIL {r.user_id}: {r.error[:80]}")

        assert len(successes) >= num_users * 0.8, f"More than 20% failure rate"

    @pytest.mark.asyncio
    async def test_concurrent_different_users(self, factory, config):
        """Test 5 different users accessing their own sandboxes concurrently.

        Uses small pool per backend to avoid overwhelming the system with
        5 users × 5 min_pool_size = 25 simultaneous containers.
        """
        small_config = LLMSandboxBackendConfig(
            lang="python",
            max_pool_size=5,
            min_pool_size=1,
            idle_timeout=60.0,
            enable_prewarming=False,
            default_timeout=30,
        )

        async def user_task(user_id: str) -> UserTaskResult:
            start = time.monotonic()
            try:
                async with factory.create_backend(config=small_config) as backend:
                    result = await backend.async_execute(
                        f"print(f'User {user_id} here')",
                        timeout=30,
                    )
                duration = (time.monotonic() - start) * 1000
                return UserTaskResult(
                    user_id=user_id,
                    success=(result.exit_code == 0),
                    duration_ms=duration,
                )
            except Exception as e:
                duration = (time.monotonic() - start) * 1000
                return UserTaskResult(
                    user_id=user_id,
                    success=False,
                    error=str(e),
                    duration_ms=duration,
                )

        results = await asyncio.gather(*[
            user_task(f"async_user_{i}") for i in range(5)
        ])

        successes = [r for r in results if r.success]
        print(f"\n[async 5 different users] {len(successes)}/5 succeeded")
        for r in successes:
            print(f"  OK {r.user_id}: {r.duration_ms:.0f}ms")
        assert len(successes) == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])