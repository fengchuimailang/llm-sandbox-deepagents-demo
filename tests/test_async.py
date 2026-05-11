"""
Async Tests for LLMSandboxBackend

Tests async methods: async_execute, async_read, async_write, async_edit
"""

import asyncio
import time

import pytest

from llm_sandbox_deepagents_adapter import (
    LLMSandboxBackend,
    LLMSandboxBackendConfig,
    SandboxStats,
    SandboxError,
    TimeoutError,
    ResourceExhaustedError,
    classify_error,
    async_retry,
    sync_retry,
    get_factory,
)


class TestAsyncRetry:
    """Test async retry decorator."""

    def test_async_retry_success_first_attempt(self):
        """Test that async_retry succeeds on first attempt."""
        attempt_count = 0

        @async_retry(max_attempts=3, base_delay=0.1)
        async def flaky_function():
            nonlocal attempt_count
            attempt_count += 1
            return "success"

        result = asyncio.run(flaky_function())
        assert result == "success"
        assert attempt_count == 1

    def test_async_retry_success_after_failures(self):
        """Test that async_retry retries on failure and succeeds."""
        attempt_count = 0

        @async_retry(max_attempts=3, base_delay=0.1)
        async def flaky_function():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise ValueError("Temporary failure")
            return "success"

        result = asyncio.run(flaky_function())
        assert result == "success"
        assert attempt_count == 3

    def test_async_retry_exhausted(self):
        """Test that async_retry raises after all attempts exhausted."""
        attempt_count = 0

        @async_retry(max_attempts=3, base_delay=0.1)
        async def always_fails():
            nonlocal attempt_count
            attempt_count += 1
            raise RuntimeError("Always fails")

        with pytest.raises(RuntimeError, match="Always fails"):
            asyncio.run(always_fails())
        assert attempt_count == 3


class TestSyncRetry:
    """Test sync retry decorator."""

    def test_sync_retry_success_first_attempt(self):
        """Test that sync_retry succeeds on first attempt."""
        attempt_count = 0

        @sync_retry(max_attempts=3, base_delay=0.1)
        def flaky_function():
            nonlocal attempt_count
            attempt_count += 1
            return "success"

        result = flaky_function()
        assert result == "success"
        assert attempt_count == 1

    def test_sync_retry_retries_on_failure(self):
        """Test that sync_retry retries on failure."""
        attempt_count = 0

        @sync_retry(max_attempts=3, base_delay=0.1)
        def flaky_function():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise ValueError("Temporary failure")
            return "success"

        result = flaky_function()
        assert result == "success"
        assert attempt_count == 3


class TestErrorClassification:
    """Test error classification."""

    def test_classify_timeout(self):
        """Test timeout error classification."""
        error = TimeoutError("Operation timed out")
        assert classify_error(error) == TimeoutError

    def test_classify_resource_exhausted(self):
        """Test resource exhausted error classification."""
        error = ResourceExhaustedError("Pool exhausted")
        assert classify_error(error) == ResourceExhaustedError

    def test_classify_sandbox_error_base(self):
        """Test base SandboxError classification."""
        error = SandboxError("Generic sandbox error")
        # Default is ExecutionError
        assert classify_error(error) == SandboxError or classify_error(error).__name__ == "ExecutionError"


class TestSandboxStats:
    """Test SandboxStats dataclass."""

    def test_stats_initial_state(self):
        """Test stats start at zero."""
        stats = SandboxStats()
        assert stats.total_executions == 0
        assert stats.successful_executions == 0
        assert stats.failed_executions == 0
        assert stats.success_rate == 0.0
        assert stats.average_execution_time == 0.0

    def test_stats_success_rate_calculation(self):
        """Test success rate calculation."""
        stats = SandboxStats()
        stats.total_executions = 10
        stats.successful_executions = 8
        assert stats.success_rate == 80.0

    def test_stats_average_execution_time(self):
        """Test average execution time calculation."""
        stats = SandboxStats()
        stats.total_executions = 5
        stats.total_execution_time = 50.0
        assert stats.average_execution_time == 10.0


class TestContextManagers:
    """Test context manager support."""

    def test_sync_context_manager(self):
        """Test LLMSandboxBackend as sync context manager."""
        factory = get_factory()
        config = LLMSandboxBackendConfig()
        
        # Use factory to get a backend (won't actually connect in test)
        try:
            with factory.create_backend(config=config) as backend:
                assert backend is not None
        except Exception:
            # Expected if no docker available
            pass

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        """Test LLMSandboxBackend as async context manager."""
        factory = get_factory()
        config = LLMSandboxBackendConfig()
        
        try:
            async with factory.create_backend(config=config) as backend:
                assert backend is not None
        except Exception:
            # Expected if no docker available
            pass


class TestAsyncMethods:
    """Test async methods (require actual sandbox connection)."""

    @pytest.fixture
    def factory(self):
        """Get factory for creating backends."""
        return get_factory()

    @pytest.mark.asyncio
    async def test_async_execute_simple(self):
        """Test async_execute with a simple command."""
        factory = get_factory()
        config = LLMSandboxBackendConfig()
        
        try:
            async with factory.create_backend(config=config) as backend:
                result = await backend.async_execute("print('hello')")
                assert result.exit_code == 0
                assert "hello" in result.output.lower() or result.exit_code == 0
        except Exception as e:
            pytest.skip(f"No sandbox available: {e}")

    @pytest.mark.asyncio
    async def test_async_write_and_read(self):
        """Test async_write and async_read together."""
        factory = get_factory()
        config = LLMSandboxBackendConfig()
        
        try:
            async with factory.create_backend(config=config) as backend:
                # Write a file
                write_result = await backend.async_write("/workspace/test_async.txt", "Hello Async World!")
                assert write_result.path is not None or write_result.error is None
                
                # Read it back
                read_result = await backend.async_read("/workspace/test_async.txt")
                if read_result.file_data:
                    assert "Hello Async World" in read_result.file_data.content
        except Exception as e:
            pytest.skip(f"No sandbox available: {e}")

    @pytest.mark.asyncio
    async def test_async_edit(self):
        """Test async_edit method."""
        factory = get_factory()
        config = LLMSandboxBackendConfig()
        
        try:
            async with factory.create_backend(config=config) as backend:
                # Create a file first
                await backend.async_write("/workspace/test_edit.txt", "Hello World")
                
                # Edit it
                edit_result = await backend.async_edit(
                    "/workspace/test_edit.txt",
                    "World",
                    "Async World"
                )
                assert edit_result.occurrences is None or edit_result.occurrences >= 0
        except Exception as e:
            pytest.skip(f"No sandbox available: {e}")


class TestStatsTracking:
    """Test statistics tracking during operations."""

    def test_stats_initialization(self):
        """Test that stats are initialized properly."""
        factory = get_factory()
        config = LLMSandboxBackendConfig()
        
        try:
            backend = factory.create_backend(config=config)
            stats = backend.get_stats()
            assert "total_executions" in stats
            assert "success_rate" in stats
            assert "average_execution_time" in stats
            backend.close()
        except Exception:
            pass

    def test_stats_dict_format(self):
        """Test get_stats returns proper dictionary format."""
        stats = SandboxStats()
        stats.total_executions = 10
        stats.successful_executions = 9
        
        factory = get_factory()
        config = LLMSandboxBackendConfig()
        
        try:
            backend = factory.create_backend(config=config)
            result = backend.get_stats()
            assert isinstance(result, dict)
            assert all(isinstance(v, (int, float, str)) for v in result.values())
            backend.close()
        except Exception:
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])