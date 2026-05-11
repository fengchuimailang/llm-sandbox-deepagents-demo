#!/usr/bin/env python3
"""
Protocol compliance tests for LLMSandboxBackend.
Tests all SandboxBackendProtocol methods.
"""

import base64
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest


def create_pool_and_backend():
    """Create a pool and backend with proper session management."""
    from llm_sandbox import SandboxSession
    from llm_sandbox.pool import PoolConfig, create_pool_manager
    from llm_sandbox_deepagents_adapter import LLMSandboxBackend, LLMSandboxBackendConfig

    pool = create_pool_manager(
        backend="docker",
        config=PoolConfig(max_pool_size=5, min_pool_size=1),
        lang="python",
        image="python:3.12-slim",
        keep_template=True,
    )

    config = LLMSandboxBackendConfig(workspace_dir="/workspace")

    class BackendWrapper:
        def __init__(self, pool, config):
            self._pool = pool
            self._config = config
            self._session = None
            self._backend = None

        def __enter__(self):
            self._session = SandboxSession(lang="python", pool=self._pool)
            self._session.open()  # Open session before use
            self._backend = LLMSandboxBackend(sandbox_session=self._session, config=self._config)
            return self._backend

        def __exit__(self, *args):
            if self._backend:
                try:
                    self._backend.close()
                except Exception:
                    pass
            if self._session:
                try:
                    self._session.close()
                except Exception:
                    pass
            self._pool.close()
            return False

    return BackendWrapper(pool, config)


class TestProtocolCompliance:
    """Test all SandboxBackendProtocol methods."""

    def test_execute_basic(self):
        """Test basic command execution."""
        with create_pool_and_backend() as backend:
            # Use Python code directly
            result = backend.execute("print('hello')")
            assert result.output.strip() == "hello"
            assert result.exit_code == 0

    def test_execute_with_error(self):
        """Test command that returns error."""
        with create_pool_and_backend() as backend:
            result = backend.execute("raise ValueError('test error')")
            assert result.exit_code == 1

    def test_execute_multiline(self):
        """Test multiline command."""
        with create_pool_and_backend() as backend:
            result = backend.execute("""
x = 1
y = 2
print(x + y)
""")
            assert "3" in result.output

    def test_id_property(self):
        """Test id property returns UUID."""
        with create_pool_and_backend() as backend:
            assert backend.id is not None
            assert len(backend.id) == 36  # UUID format

    def test_upload_download_roundtrip(self):
        """Test upload and download roundtrip."""
        with create_pool_and_backend() as backend:
            original_content = b"hello world binary content"
            results = backend.upload_files([("/workspace/test_roundtrip.bin", original_content)])
            assert results[0].error is None

            dl_results = backend.download_files(["/workspace/test_roundtrip.bin"])
            assert dl_results[0].content == original_content

    def test_write_read_file(self):
        """Test write and read file operations."""
        with create_pool_and_backend() as backend:
            content = "Hello, World!\nLine 2\nLine 3"

            write_result = backend.write("/workspace/test_rw.txt", content)
            assert write_result.error is None
            assert write_result.path == "/workspace/test_rw.txt"

            read_result = backend.read("/workspace/test_rw.txt")
            assert read_result.error is None
            assert read_result.file_data.content == content

    def test_write_read_with_offset(self):
        """Test read with offset and limit."""
        with create_pool_and_backend() as backend:
            content = "0123456789" * 10  # 100 chars

            backend.write("/workspace/test_offset.txt", content)

            result = backend.read("/workspace/test_offset.txt", offset=0, limit=10)
            assert result.file_data.content == "0123456789"

            result = backend.read("/workspace/test_offset.txt", offset=50, limit=10)
            assert result.file_data.content == "0123456789"

    def test_ls(self):
        """Test ls command."""
        with create_pool_and_backend() as backend:
            backend.execute("""
import os
for name in ['ls_test1.txt', 'ls_test2.txt', 'ls_test3.txt']:
    open(f'/workspace/{name}', 'w').close()
""")

            result = backend.ls("/workspace")
            assert result.error is None
            assert result.entries is not None
            names = [e.name for e in result.entries]
            assert "ls_test1.txt" in names
            assert "ls_test2.txt" in names
            assert "ls_test3.txt" in names

    def test_edit_simple(self):
        """Test simple text edit."""
        with create_pool_and_backend() as backend:
            backend.write("/workspace/test_edit.txt", "Hello World")
            result = backend.edit("/workspace/test_edit.txt", "World", "Universe")
            assert result.error is None
            assert result.occurrences == 1

            read_result = backend.read("/workspace/test_edit.txt")
            assert read_result.file_data.content == "Hello Universe"

    def test_edit_replace_all(self):
        """Test edit with replace_all=True."""
        with create_pool_and_backend() as backend:
            backend.write("/workspace/test_edit_all.txt", "foo bar foo baz foo")
            result = backend.edit("/workspace/test_edit_all.txt", "foo", "QUX", replace_all=True)
            assert result.occurrences == 3

            read_result = backend.read("/workspace/test_edit_all.txt")
            assert read_result.file_data.content == "QUX bar QUX baz QUX"

    def test_grep_basic(self):
        """Test grep for pattern."""
        with create_pool_and_backend() as backend:
            backend.write("/workspace/grep_test.txt", "line1: hello\nline2: world\nline3: hello world")
            result = backend.grep("hello", "/workspace")
            assert result.error is None
            assert len(result.matches) >= 1

    def test_glob(self):
        """Test glob pattern matching."""
        with create_pool_and_backend() as backend:
            backend.execute("""
import os
for name in ['glob1.txt', 'glob2.txt', 'glob3.py']:
    open(f'/workspace/{name}', 'w').close()
""")
            result = backend.glob("*.txt", "/workspace")
            assert result.error is None
            assert len(result.matches) >= 2


class TestMultiUserIsolation:
    """Test user isolation in multi-user scenario."""

    def test_user_sandbox_isolation(self):
        """Test that different users get isolated sandboxes."""
        from llm_sandbox import SandboxSession
        from llm_sandbox.pool import PoolConfig, create_pool_manager
        from llm_sandbox_deepagents_adapter import LLMSandboxBackend, LLMSandboxBackendConfig

        pool = create_pool_manager(
            backend="docker",
            config=PoolConfig(max_pool_size=10, min_pool_size=2),
            lang="python",
            image="python:3.12-slim",
            keep_template=True,
        )

        try:
            with SandboxSession(lang="python", pool=pool) as session1:
                with SandboxSession(lang="python", pool=pool) as session2:
                    backend1 = LLMSandboxBackend(
                        session1,
                        config=LLMSandboxBackendConfig(workspace_dir="/workspace/user1")
                    )
                    backend2 = LLMSandboxBackend(
                        session2,
                        config=LLMSandboxBackendConfig(workspace_dir="/workspace/user2")
                    )

                    backend1.write("/workspace/user1/file.txt", "user1_content")
                    result1 = backend1.read("/workspace/user1/file.txt")
                    assert "user1_content" in result1.file_data.content

                    result2 = backend2.read("/workspace/user1/file.txt")
                    assert result2.error is not None
        finally:
            pool.close()


class TestMultiThreading:
    """Test thread safety."""

    def test_concurrent_execute(self):
        """Test concurrent execute calls."""
        from llm_sandbox import SandboxSession
        from llm_sandbox.pool import PoolConfig, create_pool_manager
        from llm_sandbox_deepagents_adapter import LLMSandboxBackend, LLMSandboxBackendConfig

        pool = create_pool_manager(
            backend="docker",
            config=PoolConfig(max_pool_size=20, min_pool_size=5),
            lang="python",
            image="python:3.12-slim",
            keep_template=True,
        )

        try:
            def worker(i):
                with SandboxSession(lang="python", pool=pool) as session:
                    backend = LLMSandboxBackend(session, config=LLMSandboxBackendConfig())
                    result = backend.execute(f"print({i})")
                    return (i, result)

            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(worker, i) for i in range(20)]
                results = [f.result() for f in as_completed(futures)]

            assert len(results) == 20
            for i, result in results:
                assert result.exit_code == 0
        finally:
            pool.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])