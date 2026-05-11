from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from llm_sandbox import SandboxSession

if TYPE_CHECKING:
    from deepagents.backends.protocol import (
        ExecuteResponse,
        FileDownloadResponse,
        FileUploadResponse,
    )

logger = logging.getLogger(__name__)


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


class LLMSandboxBackend:
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

    @property
    def id(self) -> str:
        return self._id

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

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> "ExecuteResponse":
        from deepagents.backends.protocol import ExecuteResponse

        self._ensure_workspace()
        timeout = timeout or self._config.default_timeout
        try:
            result = self._session.run(
                command,
                timeout=timeout,
            )
            output = result.stdout or ""
            if result.stderr:
                output += "\n" + result.stderr
            return ExecuteResponse(
                output=output,
                exit_code=result.exit_code,
                truncated=False,
            )
        except Exception as e:
            logger.exception("Execute failed")
            return ExecuteResponse(
                output=str(e),
                exit_code=-1,
                truncated=False,
            )

    def _run_subprocess(self, bash_command: str) -> tuple[str, int]:
        """Run a bash command and return (output, exit_code)."""
        safe_cmd = bash_command.replace("'", "'\\''")
        cmd = f"import subprocess; r = subprocess.run(['bash', '-c', '{safe_cmd}'], capture_output=True, text=True); print(r.stdout or '', end=''); exit(r.returncode)"
        result = self._session.run(cmd)
        return result.stdout, result.exit_code

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


__all__ = [
    "LLMSandboxBackend",
    "LLMSandboxBackendFactory",
    "LLMSandboxBackendConfig",
    "get_factory",
    "FileInfo",
    "LsResult",
    "ReadResult",
    "FileData",
    "WriteResult",
    "EditResult",
    "GrepMatch",
    "GrepResult",
    "GlobResult",
]