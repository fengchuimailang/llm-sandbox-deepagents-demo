from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from llm_sandbox_deepagents_adapter.llm_sandbox_backend import (
    LLMSandboxBackend,
    LLMSandboxBackendConfig,
    LLMSandboxBackendFactory,
    get_factory,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class UserSandbox:
    user_id: str
    backend: LLMSandboxBackend
    created_at: float
    last_accessed: float
    request_count: int = 0


class SandboxManager:
    def __init__(self, config: LLMSandboxBackendConfig | None = None):
        self._factory = get_factory()
        self._config = config or LLMSandboxBackendConfig()
        self._user_sandboxes: dict[str, UserSandbox] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=50)
        self._idle_timeout = 300.0
        self._max_sandboxes_per_user = 5

    def _hash_user_id(self, user_id: str) -> str:
        return hashlib.sha256(user_id.encode()).hexdigest()[:16]

    def get_or_create_sandbox(self, user_id: str) -> UserSandbox:
        with self._lock:
            sandbox_key = self._hash_user_id(user_id)

            if sandbox_key in self._user_sandboxes:
                sandbox = self._user_sandboxes[sandbox_key]
                sandbox.last_accessed = time.time()
                sandbox.request_count += 1
                return sandbox

            if len(self._user_sandboxes) >= self._config.max_pool_size * 0.8:
                self._cleanup_idle_sandboxes()

            backend = self._factory.create_backend(
                pool_key=f"pool_{sandbox_key}",
                config=self._config,
            )

            sandbox = UserSandbox(
                user_id=user_id,
                backend=backend,
                created_at=time.time(),
                last_accessed=time.time(),
            )
            self._user_sandboxes[sandbox_key] = sandbox
            logger.info(f"Created new sandbox for user {user_id[:8]}...")
            return sandbox

    def _cleanup_idle_sandboxes(self) -> int:
        current_time = time.time()
        removed = 0
        keys_to_remove = []

        for key, sandbox in self._user_sandboxes.items():
            if current_time - sandbox.last_accessed > self._idle_timeout:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            sandbox = self._user_sandboxes.pop(key, None)
            if sandbox:
                try:
                    sandbox.backend.close()
                except Exception:
                    pass
                removed += 1
                logger.info(f"Cleaned up idle sandbox for user {sandbox.user_id[:8]}...")

        return removed

    def release_sandbox(self, user_id: str) -> bool:
        with self._lock:
            sandbox_key = self._hash_user_id(user_id)
            sandbox = self._user_sandboxes.pop(sandbox_key, None)
            if sandbox:
                try:
                    sandbox.backend.close()
                except Exception:
                    pass
                return True
            return False

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_sandboxes": len(self._user_sandboxes),
                "max_pool_size": self._config.max_pool_size,
                "idle_timeout": self._idle_timeout,
                "sandboxes": [
                    {
                        "user_id": s.user_id[:8] + "...",
                        "created_at": s.created_at,
                        "last_accessed": s.last_accessed,
                        "request_count": s.request_count,
                    }
                    for s in self._user_sandboxes.values()
                ],
            }

    def shutdown(self) -> None:
        with self._lock:
            for sandbox in self._user_sandboxes.values():
                try:
                    sandbox.backend.close()
                except Exception:
                    pass
            self._user_sandboxes.clear()
        self._executor.shutdown(wait=True)


_manager: SandboxManager | None = None
_factory: LLMSandboxBackendFactory | None = None


def get_manager() -> SandboxManager:
    global _manager
    if _manager is None:
        _manager = SandboxManager()
    return _manager


def get_config() -> LLMSandboxBackendConfig:
    return LLMSandboxBackendConfig(
        lang="python",
        max_pool_size=20,
        min_pool_size=5,
        idle_timeout=300.0,
        enable_prewarming=True,
        default_timeout=1800,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _factory, _manager
    config = get_config()
    _factory = get_factory()
    _manager = SandboxManager(config)

    asyncio.create_task(_periodic_cleanup())

    logger.info("Sandbox service started")
    yield

    if _manager:
        _manager.shutdown()
    if _factory:
        _factory.close_all()
    logger.info("Sandbox service stopped")


async def _periodic_cleanup():
    while True:
        await asyncio.sleep(60)
        if _manager:
            removed = _manager._cleanup_idle_sandboxes()
            if removed > 0:
                logger.info(f"Periodic cleanup removed {removed} idle sandboxes")


app = FastAPI(
    title="LLM Sandbox Service",
    description="Multi-user sandbox service for DeepAgents using llm-sandbox",
    version="0.1.0",
    lifespan=lifespan,
)


class ExecuteRequest(BaseModel):
    user_id: str
    command: str
    timeout: int | None = None


class ExecuteResponse(BaseModel):
    output: str
    exit_code: int | None
    truncated: bool


class WriteFileRequest(BaseModel):
    user_id: str
    file_path: str
    content: str


class ReadFileRequest(BaseModel):
    user_id: str
    file_path: str
    offset: int = 0
    limit: int = 2000


class FileOperationResponse(BaseModel):
    success: bool
    path: str | None = None
    error: str | None = None


class StatsResponse(BaseModel):
    total_sandboxes: int
    max_pool_size: int
    idle_timeout: float
    sandboxes: list[dict[str, Any]]


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.post("/execute", response_model=ExecuteResponse)
async def execute_command(request: ExecuteRequest):
    try:
        manager = get_manager()
        sandbox = manager.get_or_create_sandbox(request.user_id)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: sandbox.backend.execute(request.command, timeout=request.timeout),
        )
        return ExecuteResponse(
            output=result.output,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )
    except Exception as e:
        logger.exception(f"Execute failed for user {request.user_id[:8]}...")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/write_file", response_model=FileOperationResponse)
async def write_file(request: WriteFileRequest):
    try:
        manager = get_manager()
        sandbox = manager.get_or_create_sandbox(request.user_id)

        result = sandbox.backend.write(request.file_path, request.content)
        if result.error:
            return FileOperationResponse(success=False, path=request.file_path, error=result.error)
        return FileOperationResponse(success=True, path=request.file_path)
    except Exception as e:
        logger.exception(f"Write failed for user {request.user_id[:8]}...")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/read_file")
async def read_file(request: ReadFileRequest):
    try:
        manager = get_manager()
        sandbox = manager.get_or_create_sandbox(request.user_id)

        result = sandbox.backend.read(
            request.file_path,
            offset=request.offset,
            limit=request.limit,
        )
        if result.error:
            return JSONResponse(
                status_code=400,
                content={"success": False, "file_path": request.file_path, "error": result.error},
            )
        return {
            "success": True,
            "content": result.file_data.content if result.file_data else "",
            "truncated": result.file_data.truncated if result.file_data else False,
        }
    except Exception as e:
        logger.exception(f"Read failed for user {request.user_id[:8]}...")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/upload_files")
async def upload_files(user_id: str, files: list[tuple[str, bytes]]):
    try:
        manager = get_manager()
        sandbox = manager.get_or_create_sandbox(user_id)

        results = sandbox.backend.upload_files(files)
        return {
            "results": [
                {"path": r.path, "error": r.error}
                for r in results
            ]
        }
    except Exception as e:
        logger.exception(f"Upload failed for user {user_id[:8]}...")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/download_files")
async def download_files(user_id: str, paths: list[str]):
    try:
        manager = get_manager()
        sandbox = manager.get_or_create_sandbox(user_id)

        results = sandbox.backend.download_files(paths)
        return {
            "results": [
                {
                    "path": r.path,
                    "content": base64.b64encode(r.content).decode() if r.content else None,
                    "error": r.error,
                }
                for r in results
            ]
        }
    except Exception as e:
        logger.exception(f"Download failed for user {user_id[:8]}...")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats", response_model=StatsResponse)
async def get_stats():
    try:
        manager = get_manager()
        stats = manager.get_stats()
        return StatsResponse(**stats)
    except Exception as e:
        logger.exception("Failed to get stats")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/sandbox/{user_id}")
async def delete_sandbox(user_id: str):
    try:
        manager = get_manager()
        success = manager.release_sandbox(user_id)
        if success:
            return {"success": True, "message": "Sandbox released"}
        return {"success": False, "message": "Sandbox not found"}
    except Exception as e:
        logger.exception(f"Failed to delete sandbox for user {user_id[:8]}...")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)