from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from dataclasses import dataclass, field
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable

from llm_sandbox import SandboxSession

if TYPE_CHECKING:
    from deepagents.backends.protocol import (
        ExecuteResponse,
        FileDownloadResponse,
        FileUploadResponse,
    )

logger = logging.getLogger(__name__)


# ============================================================================
# Error Classification
# ============================================================================

class SandboxError(Exception):
    """Base exception for sandbox operations."""
    pass


class TimeoutError(SandboxError):
    """Operation timed out."""
    pass


class ResourceExhaustedError(SandboxError):
    """Sandbox pool exhausted or resources unavailable."""
    pass


class SyntaxError(SandboxError):
    """Code syntax error detected."""
    pass


class PermissionError(SandboxError):
    """Permission denied for operation."""
    pass


class FileNotFoundError(SandboxError):
    """File not found."""
    pass


class ExecutionError(SandboxError):
    """General execution error."""
    pass


def classify_error(exc: Exception) -> type[SandboxError]:
    """Classify an exception into a specific SandboxError type."""
    error_msg = str(exc).lower()
    
    if "timeout" in error_msg or "timed out" in error_msg:
        return TimeoutError
    elif "pool" in error_msg or "resource" in error_msg or "exhaust" in error_msg:
        return ResourceExhaustedError
    elif "syntax" in error_msg or "parse" in error_msg or "invalid" in error_msg:
        return SyntaxError
    elif "permission" in error_msg or "denied" in error_msg or "access" in error_msg:
        return PermissionError
    elif "not found" in error_msg or "no such file" in error_msg:
        return FileNotFoundError
    else:
        return ExecutionError


# ============================================================================
# Retry Decorator
# ============================================================================

def async_retry(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    retry_on: tuple[type[Exception], ...] = (Exception,),
):
    """Async retry decorator with exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except retry_on as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(
                            f"Retry {attempt + 1}/{max_attempts} for {func.__name__} "
                            f"after {delay:.1f}s: {e}"
                        )
                        await asyncio.sleep(delay)
            raise last_exception
        return wrapper
    return decorator


def sync_retry(
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    retry_on: tuple[type[Exception], ...] = (Exception,),
):
    """Sync retry decorator with exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except retry_on as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logger.warning(
                            f"Retry {attempt + 1}/{max_attempts} for {func.__name__} "
                            f"after {delay:.1f}s: {e}"
                        )
                        time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator


# ============================================================================
# Configuration & Data Classes
# ============================================================================

@dataclass
class LLMSandboxBackendConfig:
    lang: str = "python"
    image: str | None = None
    keep_template: bool = True
    max_pool_size: int = 10
    min_pool_size: int = 2
    idle_timeout: float = 300.0
    acquisition_timeout: float = 30.0
    enable_prewarming: bool = True
    default_timeout: int = 1800
    max_container_lifetime: float = 3600.0
    max_container_uses: int = 100
    pool_exhaustion_strategy: str = "WAIT"
    workspace_dir: str = "/workspace"


@dataclass
class FileInfo:
    name: str
    is_dir: bool
    size: int = 0

    def get(self, key: str, default=None):
        """Allow dict-like access for deepagents filesystem middleware compatibility."""
        if key == "path":
            return self.name
        return getattr(self, key, default)


@dataclass
class LsResult:
    entries: list[FileInfo] | None = None
    error: str | None = None


@dataclass
class FileData:
    content: str
    truncated: bool
    offset: int = 0
    limit: int = 2000

    def __getitem__(self, key: str):
        """Allow dict-like subscript access for deepagents filesystem middleware."""
        return getattr(self, key)


@dataclass
class ReadResult:
    file_data: FileData | None = None
    error: str | None = None


@dataclass
class WriteResult:
    path: str | None = None
    error: str | None = None


@dataclass
class EditResult:
    path: str | None = None
    occurrences: int | None = None
    error: str | None = None


@dataclass
class GrepMatch:
    file_path: str
    line_content: str
    line_number: int = 0


@dataclass
class GrepResult:
    matches: list[GrepMatch] | None = None
    error: str | None = None


@dataclass
class GlobResult:
    matches: list[FileInfo] | None = None
    error: str | None = None


# ============================================================================
# Statistics
# ============================================================================

@dataclass
class SandboxStats:
    """Statistics for sandbox operations."""
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    total_execution_time: float = 0.0
    total_retries: int = 0
    timeout_count: int = 0
    resource_exhausted_count: int = 0
    syntax_error_count: int = 0
    permission_error_count: int = 0
    file_not_found_count: int = 0
    other_error_count: int = 0
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate as a percentage."""
        if self.total_executions == 0:
            return 0.0
        return (self.successful_executions / self.total_executions) * 100
    
    @property
    def average_execution_time(self) -> float:
        """Calculate average execution time in seconds."""
        if self.total_executions == 0:
            return 0.0
        return self.total_execution_time / self.total_executions


# ============================================================================
# Main Backend Class
# ============================================================================

class LLMSandboxBackend:
    """Main sandbox backend with async support, error classification, and metrics."""
    
    def __init__(
        self,
        *,
        sandbox_session: SandboxSession,
        config: LLMSandboxBackendConfig | None = None,
    ):
        self._session = sandbox_session
        self._config = config or LLMSandboxBackendConfig()
        self._id = str(uuid.uuid4())
        self._workspace_initialized = False
        self._stats = SandboxStats()
        self._lock = asyncio.Lock() if hasattr(asyncio, 'Lock') else None

    @property
    def id(self) -> str:
        return self._id

    @property
    def stats(self) -> SandboxStats:
        """Get sandbox statistics."""
        return self._stats

    def get_stats(self) -> dict[str, Any]:
        """Get statistics as a dictionary (for external API)."""
        return {
            "total_executions": self._stats.total_executions,
            "successful_executions": self._stats.successful_executions,
            "failed_executions": self._stats.failed_executions,
            "success_rate": round(self._stats.success_rate, 2),
            "average_execution_time": round(self._stats.average_execution_time, 3),
            "total_retries": self._stats.total_retries,
            "timeout_count": self._stats.timeout_count,
            "resource_exhausted_count": self._stats.resource_exhausted_count,
            "syntax_error_count": self._stats.syntax_error_count,
            "permission_error_count": self._stats.permission_error_count,
            "file_not_found_count": self._stats.file_not_found_count,
            "other_error_count": self._stats.other_error_count,
        }

    def _increment_error_stat(self, error_type: type[Exception]) -> None:
        """Increment error-specific counter."""
        error_name = error_type.__name__
        if "Timeout" in error_name:
            self._stats.timeout_count += 1
        elif "ResourceExhausted" in error_name:
            self._stats.resource_exhausted_count += 1
        elif "Syntax" in error_name:
            self._stats.syntax_error_count += 1
        elif "Permission" in error_name:
            self._stats.permission_error_count += 1
        elif "FileNotFound" in error_name:
            self._stats.file_not_found_count += 1
        else:
            self._stats.other_error_count += 1

    def _ensure_workspace(self) -> None:
        """Ensure workspace directory exists."""
        if self._workspace_initialized:
            return
        try:
            cmd = "import subprocess; subprocess.run(['bash', '-c', 'mkdir -p /workspace'], check=True)"
            self._session.run(cmd)
            self._workspace_initialized = True
        except Exception:
            pass

    def _run_subprocess(self, bash_command: str) -> tuple[str, int]:
        """Run a bash command and return (output, exit_code)."""
        safe_cmd = bash_command.replace("'", "'\\''")
        cmd = f"import subprocess; r = subprocess.run(['bash', '-c', '{safe_cmd}'], capture_output=True, text=True); print(r.stdout or '', end=''); exit(r.returncode)"
        result = self._session.run(cmd)
        return result.stdout, result.exit_code

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
        retry: bool = True,
    ) -> "ExecuteResponse":
        """Execute a command synchronously."""
        from deepagents.backends.protocol import ExecuteResponse

        self._ensure_workspace()
        timeout = timeout or self._config.default_timeout
        start_time = time.time()
        
        try:
            result = self._session.run(
                command,
                timeout=timeout,
            )
            output = result.stdout or ""
            if result.stderr:
                output += "\n" + result.stderr
            
            elapsed = time.time() - start_time
            self._stats.total_executions += 1
            self._stats.successful_executions += 1
            self._stats.total_execution_time += elapsed
            
            return ExecuteResponse(
                output=output,
                exit_code=result.exit_code,
                truncated=False,
            )
        except Exception as e:
            elapsed = time.time() - start_time
            self._stats.total_executions += 1
            self._stats.failed_executions += 1
            self._stats.total_execution_time += elapsed
            
            error_type = classify_error(e)
            self._increment_error_stat(error_type)
            
            if retry and isinstance(e, (TimeoutError, ResourceExhaustedError)):
                self._stats.total_retries += 1
            
            logger.exception("Execute failed")
            return ExecuteResponse(
                output=str(e),
                exit_code=-1,
                truncated=False,
            )

    async def async_execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> "ExecuteResponse":
        """Execute a command asynchronously."""
        from deepagents.backends.protocol import ExecuteResponse

        self._ensure_workspace()
        timeout = timeout or self._config.default_timeout
        start_time = time.time()
        
        try:
            # Check if session supports run_async
            if hasattr(self._session, 'run_async'):
                result = await asyncio.wait_for(
                    self._session.run_async(command, timeout=timeout),
                    timeout=timeout + 5  # Slight buffer for safety
                )
            else:
                # Fallback to sync execution in a thread pool
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: self._session.run(command, timeout=timeout)
                )
            
            output = result.stdout or ""
            if result.stderr:
                output += "\n" + result.stderr
            
            elapsed = time.time() - start_time
            async with asyncio.Lock() if self._lock else contextlib.nullcontext():
                self._stats.total_executions += 1
                self._stats.successful_executions += 1
                self._stats.total_execution_time += elapsed
            
            return ExecuteResponse(
                output=output,
                exit_code=result.exit_code,
                truncated=False,
            )
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            async with asyncio.Lock() if self._lock else contextlib.nullcontext():
                self._stats.total_executions += 1
                self._stats.failed_executions += 1
                self._stats.total_execution_time += elapsed
                self._stats.timeout_count += 1
            
            return ExecuteResponse(
                output=f"Operation timed out after {timeout}s",
                exit_code=-1,
                truncated=False,
            )
        except Exception as e:
            elapsed = time.time() - start_time
            error_type = classify_error(e)
            
            async with asyncio.Lock() if self._lock else contextlib.nullcontext():
                self._stats.total_executions += 1
                self._stats.failed_executions += 1
                self._stats.total_execution_time += elapsed
                self._increment_error_stat(error_type)
            
            logger.exception("Async execute failed")
            return ExecuteResponse(
                output=str(e),
                exit_code=-1,
                truncated=False,
            )

    def upload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list["FileUploadResponse"]:
        from deepagents.backends.protocol import FileOperationError, FileUploadResponse

        self._ensure_workspace()
        results = []
        for path, content in files:
            try:
                if not path.startswith("/"):
                    target_path = f"{self._config.workspace_dir}/{path}"
                else:
                    target_path = path

                encoded = base64.b64encode(content).decode("utf-8")
                safe_path = target_path.replace("'", "'\\''")
                cmd = f"import base64; open('{safe_path}', 'wb').write(base64.b64decode('{encoded}'))"
                self._session.run(cmd)
                results.append(FileUploadResponse(path=path))
            except Exception as e:
                logger.warning(f"Upload failed for {path}: {e}")
                results.append(
                    FileUploadResponse(
                        path=path,
                        error=FileOperationError.PERMISSION_DENIED,
                    )
                )
        return results

    def download_files(
        self, paths: list[str]
    ) -> list["FileDownloadResponse"]:
        from deepagents.backends.protocol import FileDownloadResponse, FileOperationError

        results = []
        for path in paths:
            try:
                safe_path = path.replace("'", "'\\''")
                cmd = f"import base64; print(base64.b64encode(open('{safe_path}', 'rb').read()).decode('utf-8'))"
                encoded_content = self._session.run(cmd)
                content = base64.b64decode(encoded_content.stdout.strip())
                results.append(FileDownloadResponse(path=path, content=content))
            except Exception as e:
                logger.warning(f"Download failed for {path}: {e}")
                results.append(
                    FileDownloadResponse(
                        path=path,
                        error=FileOperationError.FILE_NOT_FOUND,
                    )
                )
        return results

    def ls(self, path: str) -> LsResult:
        self._ensure_workspace()
        try:
            safe_path = path.replace("'", "'\\''")
            cmd = f"import subprocess; r = subprocess.run(['bash', '-c', f'ls -la \\'{safe_path}\\''], capture_output=True, text=True); print(r.stdout or '', end='')"
            result = self._session.run(cmd)

            entries = []
            lines = result.stdout.strip().split("\n")
            for line in lines:
                if not line or line.startswith("total") or line.startswith("."):
                    continue
                parts = line.split()
                if len(parts) >= 8:
                    is_dir = parts[0].startswith("d")
                    name = " ".join(parts[7:])
                    entries.append(FileInfo(name=name, is_dir=is_dir, size=0))
            return LsResult(entries=entries)
        except Exception as e:
            logger.warning(f"ls failed for {path}: {e}")
            return LsResult(error=str(e))

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        try:
            safe_path = file_path.replace("'", "'\\''")
            if offset > 0:
                cmd = f"import subprocess; r = subprocess.run(['bash', '-c', f'dd if=\\'{safe_path}\\' bs=1 skip={offset} count={limit} 2>/dev/null'], capture_output=True, text=True); print(r.stdout or '', end='')"
            else:
                cmd = f"import subprocess; r = subprocess.run(['bash', '-c', f'cat \\'{safe_path}\\''], capture_output=True, text=True); print(r.stdout[:{limit}] if r.stdout else '', end='')"
            result = self._session.run(cmd)
            content = result.stdout
            truncated = len(content) >= limit
            return ReadResult(
                file_data=FileData(
                    content=content,
                    truncated=truncated,
                    offset=offset,
                    limit=limit,
                )
            )
        except Exception as e:
            logger.warning(f"read failed for {file_path}: {e}")
            return ReadResult(error=str(e))

    async def async_read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Read a file asynchronously."""
        return await asyncio.get_event_loop().run_in_executor(
            None, self.read, file_path, offset, limit
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        self._ensure_workspace()
        try:
            if not file_path.startswith("/"):
                target_path = f"{self._config.workspace_dir}/{file_path}"
            else:
                target_path = file_path

            safe_path = target_path.replace("'", "'\\''")
            encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
            cmd = f"import base64; open('{safe_path}', 'w', encoding='utf-8').write(base64.b64decode('{encoded}').decode('utf-8'))"
            self._session.run(cmd)
            return WriteResult(path=target_path)
        except Exception as e:
            logger.warning(f"write failed for {file_path}: {e}")
            return WriteResult(error=str(e))

    async def async_write(self, file_path: str, content: str) -> WriteResult:
        """Write a file asynchronously."""
        return await asyncio.get_event_loop().run_in_executor(
            None, self.write, file_path, content
        )

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        self._ensure_workspace()
        try:
            safe_path = file_path.replace("'", "'\\''")
            encoded_old = base64.b64encode(old_string.encode("utf-8")).decode("utf-8")
            encoded_new = base64.b64encode(new_string.encode("utf-8")).decode("utf-8")

            replace_flag = "-g" if replace_all else ""
            cmd = f"""
import base64
old = base64.b64decode('{encoded_old}').decode('utf-8')
new = base64.b64decode('{encoded_new}').decode('utf-8')
with open('{safe_path}', 'r', encoding='utf-8') as f:
    content = f.read()
import re
pattern = re.escape(old)
if '{replace_all}'.lower() == 'true':
    occurrences = len(re.findall(pattern, content))
    new_content = re.sub(pattern, new, content)
else:
    match = re.search(pattern, content)
    occurrences = 1 if match else 0
    new_content = re.sub(pattern, new, content, count=1) if match else content
with open('{safe_path}', 'w', encoding='utf-8') as f:
    f.write(new_content)
print(occurrences)
"""
            result = self._session.run(cmd)
            try:
                occurrences = int(result.stdout.strip())
            except ValueError:
                occurrences = 0
            return EditResult(path=file_path, occurrences=occurrences)
        except Exception as e:
            logger.warning(f"edit failed for {file_path}: {e}")
            return EditResult(error=str(e))

    async def async_edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Edit a file asynchronously."""
        return await asyncio.get_event_loop().run_in_executor(
            None, self.edit, file_path, old_string, new_string, replace_all
        )

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        self._ensure_workspace()
        try:
            search_path = (path or "/workspace").replace("'", "'\\''")
            safe_pattern = pattern.replace("'", "'\\''")

            cmd = f"import subprocess; r = subprocess.run(['bash', '-c', f'grep -r -n \\'{safe_pattern}\\' \\'{search_path}\\' 2>/dev/null'], capture_output=True, text=True); print(r.stdout or '', end='')"
            result = self._session.run(cmd)

            matches = []
            for line in result.stdout.strip().split("\n"):
                if line and ":" in line:
                    parts = line.split(":", 2)
                    if len(parts) >= 2:
                        file_path = parts[0]
                        line_number = int(parts[1]) if parts[1].isdigit() else 0
                        line_content = parts[2] if len(parts) == 3 else ""
                        matches.append(GrepMatch(
                            file_path=file_path,
                            line_content=line_content,
                            line_number=line_number
                        ))
            return GrepResult(matches=matches)
        except Exception as e:
            logger.warning(f"grep failed for pattern '{pattern}': {e}")
            return GrepResult(error=str(e))

    def glob(self, pattern: str, path: str = "/workspace") -> GlobResult:
        self._ensure_workspace()
        try:
            safe_path = path.replace("'", "'\\''")
            safe_pattern = pattern.replace("'", "'\\''")
            cmd = f"import subprocess; r = subprocess.run(['bash', '-c', f'ls -d \\'{safe_path}/{safe_pattern}\\' 2>/dev/null || true'], capture_output=True, text=True); print(r.stdout or '', end='')"
            result = self._session.run(cmd)

            entries = []
            for line in result.stdout.strip().split("\n"):
                if line:
                    name = line.split("/")[-1]
                    full_path = line.replace("'", "'\\''")
                    is_dir_cmd = f"import subprocess; r = subprocess.run(['bash', '-c', f'test -d \\'{full_path}\\' && echo 1 || echo 0'], capture_output=True, text=True); print(r.stdout.strip() or '0', end='')"
                    is_dir_result = self._session.run(is_dir_cmd)
                    is_dir = is_dir_result.stdout.strip() == "1"
                    entries.append(FileInfo(name=name, is_dir=is_dir, size=0))
            return GlobResult(matches=entries)
        except Exception as e:
            logger.warning(f"glob failed for {pattern}: {e}")
            return GlobResult(error=str(e))

    def close(self) -> None:
        if hasattr(self._session, "close"):
            try:
                self._session.close()
            except Exception:
                pass

    # -------------------------------------------------------------------
    # Async wrappers for SandboxBackendProtocol (a*-prefixed methods)
    # -------------------------------------------------------------------

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.write, file_path, content
        )

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.read, file_path, offset, limit
        )

    async def als(self, path: str) -> LsResult:
        return await asyncio.get_event_loop().run_in_executor(None, self.ls, path)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.edit, file_path, old_string, new_string, replace_all
        )

    async def aglob(self, pattern: str, path: str = "/workspace") -> GlobResult:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.glob, pattern, path
        )

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.grep, pattern, path, glob
        )

    async def agrep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.grep, pattern, path, glob
        )

    async def als_info(self, path: str) -> LsResult:
        return await asyncio.get_event_loop().run_in_executor(None, self.ls, path)

    async def aglob_info(self, pattern: str, path: str = "/workspace") -> GlobResult:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.glob, pattern, path
        )

    async def glob_info(self, pattern: str, path: str = "/workspace") -> GlobResult:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.glob, pattern, path
        )

    async def grep_raw(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.grep, pattern, path, glob
        )

    async def aupload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list["FileUploadResponse"]:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.upload_files, files
        )

    async def adownload_files(self, paths: list[str]) -> list["FileDownloadResponse"]:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.download_files, paths
        )

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResult:
        return await asyncio.get_event_loop().run_in_executor(
            None, self.execute, command, timeout
        )

    def ls_info(self, path: str) -> list["FileInfo"]:
        """List directory with detailed file info (returns list directly)."""
        result = self.ls(path)
        return result.entries

    # -------------------------------------------------------------------
    # Context managers
    # -------------------------------------------------------------------

    def __enter__(self) -> "LLMSandboxBackend":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    async def __aenter__(self) -> "LLMSandboxBackend":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# ============================================================================
# Factory
# ============================================================================

class LLMSandboxBackendFactory:
    _pools: dict[str, Any] = {}
    _lock_factory: Any = None

    def __init__(self):
        import threading
        self._lock_factory = threading.Lock()

    def _create_pool_manager(
        self, config: LLMSandboxBackendConfig
    ) -> Any:
        from llm_sandbox.pool import (
            ExhaustionStrategy,
            PoolConfig,
            create_pool_manager,
        )

        strategy_map = {
            "WAIT": ExhaustionStrategy.WAIT,
            "FAIL_FAST": ExhaustionStrategy.FAIL_FAST,
            "TEMPORARY": ExhaustionStrategy.TEMPORARY,
        }

        pool_config = PoolConfig(
            max_pool_size=config.max_pool_size,
            min_pool_size=config.min_pool_size,
            idle_timeout=config.idle_timeout,
            acquisition_timeout=config.acquisition_timeout,
            max_container_lifetime=config.max_container_lifetime,
            max_container_uses=config.max_container_uses,
            exhaustion_strategy=strategy_map.get(
                config.pool_exhaustion_strategy, ExhaustionStrategy.WAIT
            ),
            enable_prewarming=config.enable_prewarming,
        )

        return create_pool_manager(
            backend="docker",
            config=pool_config,
            lang=config.lang,
            image=config.image,
            keep_template=config.keep_template,
        )

    def get_pool(
        self,
        pool_key: str,
        config: LLMSandboxBackendConfig | None = None,
    ) -> Any:
        config = config or LLMSandboxBackendConfig()

        with self._lock_factory:
            if pool_key not in self._pools:
                self._pools[pool_key] = self._create_pool_manager(config)
            return self._pools[pool_key]

    def create_backend(
        self,
        *,
        pool_key: str = "default",
        config: LLMSandboxBackendConfig | None = None,
    ) -> LLMSandboxBackend:
        config = config or LLMSandboxBackendConfig()
        pool = self.get_pool(pool_key, config)

        session = SandboxSession(lang=config.lang, pool=pool)
        session.open()
        return LLMSandboxBackend(
            sandbox_session=session,
            config=config,
        )

    def close_all(self) -> None:
        with self._lock_factory:
            for pool in self._pools.values():
                pool.close()
            self._pools.clear()


_factory = LLMSandboxBackendFactory()


def get_factory() -> LLMSandboxBackendFactory:
    return _factory


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    # Classes
    "LLMSandboxBackend",
    "LLMSandboxBackendFactory",
    "LLMSandboxBackendConfig",
    "SandboxStats",
    # Data classes
    "FileInfo",
    "LsResult",
    "ReadResult",
    "FileData",
    "WriteResult",
    "EditResult",
    "GrepMatch",
    "GrepResult",
    "GlobResult",
    # Error classes
    "SandboxError",
    "TimeoutError",
    "ResourceExhaustedError",
    "SyntaxError",
    "PermissionError",
    "FileNotFoundError",
    "ExecutionError",
    # Functions
    "get_factory",
    "classify_error",
    "async_retry",
    "sync_retry",
]