"""
Integration test fixtures for DeepAgents + LLMSandbox adapter tests.

Requires a valid .env file with OpenAI-compatible LLM credentials.
"""

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

# Load .env file
from dotenv import load_dotenv

env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path)


@dataclass
class EnvConfig:
    api_key: str
    base_url: str
    model: str


@pytest.fixture(scope="session")
def env() -> EnvConfig:
    """Load and validate .env configuration."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "")

    if not api_key or api_key == "your_api_key_here":
        pytest.skip(".env OPENAI_API_KEY not configured")
    if not base_url:
        pytest.skip(".env OPENAI_BASE_URL not configured")
    if not model:
        pytest.skip(".env OPENAI_MODEL not configured")

    return EnvConfig(api_key=api_key, base_url=base_url, model=model)


@pytest.fixture(scope="session")
def llm_client(env: EnvConfig):
    """Create a langchain OpenAI-compatible chat model client."""
    from langchain.chat_models import init_chat_model

    model_str = f"openai:{env.model}"
    return init_chat_model(
        model=model_str,
        api_key=env.api_key,
        base_url=env.base_url,
    )


@pytest.fixture
def thread_id() -> str:
    """Generate a unique thread_id for each test to ensure isolation."""
    return f"test-thread-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def sandbox_factory():
    """
    Session-scoped factory for all integration tests.

    Pre-warms the pool once, all tests share the same factory/pool manager.
    Must run with docker group permissions via sg docker -c.
    """
    import docker

    try:
        client = docker.from_env()
        client.ping()
    except docker.errors.DockerException as e:
        pytest.skip(f"Docker not accessible: {e}")

    from llm_sandbox_deepagents_adapter import (
        LLMSandboxBackendConfig,
        get_factory,
    )

    config = LLMSandboxBackendConfig(
        enable_prewarming=True,
        default_timeout=30,
        idle_timeout=300.0,
        max_pool_size=5,
        min_pool_size=2,
    )

    factory = get_factory()

    yield factory

    # Cleanup on session end
    factory.close_all()


@pytest.fixture
def sandbox_backend(sandbox_factory, thread_id: str):
    """
    Per-test backend using the shared factory.

    Each test gets its own pool_key (= thread_id) so backends are isolated,
    but they all share the same factory and pool manager for efficiency.
    """
    from llm_sandbox_deepagents_adapter import LLMSandboxBackendConfig

    config = LLMSandboxBackendConfig(
        enable_prewarming=False,
        default_timeout=30,
        idle_timeout=120.0,
        max_pool_size=3,
        min_pool_size=1,
    )

    backend = sandbox_factory.create_backend(pool_key=thread_id, config=config)

    yield backend

    backend.close()


@pytest.fixture
def sandbox_agent(llm_client, sandbox_backend):
    """
    Create a DeepAgent backed by the LLMSandbox backend.

    Uses the `backend=` parameter to pass the LLMSandboxBackend, which
    implements SandboxBackendProtocol and exposes execute + file tools.
    """
    from deepagents import create_deep_agent

    agent = create_deep_agent(
        model=llm_client,
        backend=sandbox_backend,
        debug=False,
    )

    return agent
