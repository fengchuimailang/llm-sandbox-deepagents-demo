# AGENTS.md

Multi-user, multi-session code execution service using llm-sandbox as the lightweight sandbox backend for LangChain DeepAgents.

## Project Overview

**Core Goal**: Build a production-ready sandbox service that replaces Daytona with llm-sandbox for lighter weight.

**Target Architecture**:
- DeepAgents FastAPI service runs inside Docker container
- llm-sandbox provides containerized code execution
- Docker socket mounted for container management (no DinD)
- Bind mounts for persistent workspace directories

## Current State

**Phase**: Testing - Docker Integration Testing

**Completed**:
- ✅ Core adapter (LLMSandboxBackend) with execute/upload/download
- ✅ FastAPI service with basic multi-user support
- ✅ Container pool for pre-warming
- ✅ Docker architecture (Dockerfile, docker-compose.yml)
- ✅ Custom multi-language sandbox image

**In Progress**:
- 🔄 Level 1-6 Docker integration testing

**TODO**:
- [x] Complete all SandboxBackendProtocol methods
- [x] Workspace persistence via bind mount
- [x] Custom sandbox image with multi-language support
- [x] Docker Compose deployment
- [ ] Level 1: Infrastructure testing
- [ ] Level 2: API endpoint testing
- [ ] Level 3: File operations testing
- [ ] Level 4: Protocol completeness testing
- [ ] Level 5: Multi-user isolation testing
- [ ] Level 6: Persistence testing
- [ ] DeepAgents integration with proper tool binding
- [ ] Security hardening (resource limits, timeouts)

## Tech Stack

| Component | Technology |
|-----------|------------|
| Sandbox Backend | llm-sandbox (Docker) |
| Agent Framework | LangChain DeepAgents |
| API Framework | FastAPI |
| Runtime | Python 3.12+ |
| Container Runtime | Docker (socket mounted) |

## Docker Architecture

### Images
- `llm-sandbox-multilang` - Custom multi-language sandbox (python:3.12-slim + Node.js, Go, Java, etc.)
- `llm-sandbox-service` - FastAPI application container

### Containers
- `sandbox-service` - Main FastAPI service
- `sandbox-warmer` - Optional container pre-warming

### Volumes
- `docker.sock` - Docker socket for container management
- `./workspace` → `/workspace` - Shared workspace
- `./user_workspaces` → `/user_workspaces` - Per-user isolated workspaces

## Key Files

| File | Purpose |
|------|---------|
| `llm_sandbox_deepagents_adapter/llm_sandbox_backend.py` | Core adapter implementing SandboxBackendProtocol |
| `demos/sandbox_service.py` | FastAPI service entry point |
| `docker/Dockerfile.sandbox` | Custom multi-language sandbox image |
| `docker/Dockerfile.service` | FastAPI service image |
| `docker-compose.yml` | Production deployment config |
| `tests/test_level*.py` | Level 1-6 Docker integration tests |

## SandboxBackendProtocol Implementation Status

| Method | Status | Notes |
|--------|--------|-------|
| `execute(command, timeout)` | ✅ Done | Returns ExecuteResponse |
| `upload_files(files)` | ✅ Done | base64 encoded |
| `download_files(paths)` | ✅ Done | base64 encoded |
| `id` property | ✅ Done | UUID based |
| `close()` | ✅ Done | |
| `ls(path)` | ✅ Done | via subprocess ls |
| `read(path, offset, limit)` | ✅ Done | |
| `write(path, content)` | ✅ Done | |
| `edit(path, old, new, replace_all)` | ✅ Done | |
| `grep(pattern, path, glob)` | ✅ Done | |
| `glob(pattern, path)` | ✅ Done | |

## Testing

Testing follows a layered approach from simple to complex, validating on full Docker architecture.

### Test Levels

```
Level 1: Infrastructure (基础)
├── docker-compose up/down
├── Service health check
└── Network connectivity

Level 2: API Endpoints (接口)
├── /health
├── /execute
└── /stats

Level 3: File Operations (文件操作)
├── /write_file
├── /read_file
├── /upload_files
└── /download_files

Level 4: Protocol Completeness (协议完整)
├── ls()
├── edit()
├── grep()
└── glob()

Level 5: Multi-user Isolation (多用户隔离)
├── User file isolation
├── User container isolation
└── Concurrent request isolation

Level 6: Persistence (持久化)
├── Container restart file retention
├── Container reuse state retention
└── bind mount verification
```

### Test Files

| File | Level | Purpose |
|------|-------|---------|
| `tests/test_level1_infrastructure.py` | 1 | Infrastructure |
| `tests/test_level2_api.py` | 2 | API endpoints |
| `tests/test_level3_file_ops.py` | 3 | File operations |
| `tests/test_level4_protocol.py` | 4 | Protocol completeness |
| `tests/test_level5_multi_user.py` | 5 | Multi-user isolation |
| `tests/test_level6_persistence.py` | 6 | Persistence |
| `tests/pressure_test.py` | - | Performance |

### Running Tests

```bash
# Prerequisites
docker build -f docker/Dockerfile.sandbox -t llm-sandbox-multilang:latest ./docker
docker compose up -d
curl http://localhost:8000/health  # confirm ready

# Run by level
pytest tests/test_level1_infrastructure.py -v
pytest tests/test_level2_api.py -v
pytest tests/test_level3_file_ops.py -v
pytest tests/test_level4_protocol.py -v
pytest tests/test_level5_multi_user.py -v
pytest tests/test_level6_persistence.py -v

# Run all levels
pytest tests/test_level*.py -v

# Pressure/load tests
python tests/pressure_test.py --tests startup reuse concurrent
```

## Deployment

### Development
```bash
uv sync
source .venv/bin/activate
python demos/sandbox_service.py
```

### Production (Docker)
```bash
# Build custom sandbox image
docker build -f docker/Dockerfile.sandbox -t llm-sandbox-multilang:latest ./docker

# Start all services
docker compose up -d
```

## Configuration

Environment variables for container pool:
- `SANDBOX_IMAGE`: Docker image (default: llm-sandbox-multilang:latest)
- `SANDBOX_POOL_SIZE`: Max containers (default: 20)
- `SANDBOX_MIN_POOL`: Min pre-warmed (default: 5)
- `SANDBOX_IDLE_TIMEOUT`: Seconds before cleanup (default: 300)
- `SANDBOX_WORKSPACE`: Workspace directory (default: /workspace)

## Session Isolation

Each `thread_id` gets:
1. Its own SandboxBackend instance from the pool
2. Dedicated workspace directory (via bind mount)
3. Independent container from the pool

Session lifecycle:
1. First request → allocate container, mount workspace
2. Subsequent requests → reuse same container
3. Idle timeout → return container to pool
4. Explicit delete → destroy container immediately

## Multi-Language Support

Primary: Python
Additional: Node.js, Go, Java, C++, Shell

Execute via shell command rather than lang parameter:
```python
# Instead of: session.run(code, lang="node")
# Use: session.run("node -e 'console.log(1)'")
```

## Security Considerations

- Container runs with restricted capabilities
- Network isolation (no network mode or very restricted)
- Read-only root filesystem where possible
- Resource limits (memory, CPU, oom-score-adj)
- Execution timeout (default 30 min, configurable)
- Workspace isolation via bind mount subdirectories

## Dependencies

See `pyproject.toml` for full list. Key dependencies:
- `llm-sandbox[docker]>=0.3.0`
- `deepagents>=0.5.0`
- `fastapi>=0.100.0`
- `uvicorn[standard]>=0.23.0`

## Build Commands

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest tests/ -v

# Run pressure tests
uv run python tests/pressure_test.py --tests startup

# Build Docker images
docker build -f docker/Dockerfile.sandbox -t llm-sandbox-multilang:latest ./docker
docker build -f docker/Dockerfile.service -t llm-sandbox-service:latest .
```