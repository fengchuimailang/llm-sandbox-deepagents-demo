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


@pytest.fixture
def sandbox_backend(thread_id: str):
    """
    Create a LLMSandboxBackend for a given thread_id (= pool_key).

    Each test gets its own pool so concurrent tests don't interfere.
    Must run with docker group permissions via sg docker -c.
    """
    import docker

    # Verify docker access before creating backend
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
        enable_prewarming=False,
        default_timeout=30,
        idle_timeout=120.0,
        max_pool_size=5,
        min_pool_size=1,
    )

    factory = get_factory()
    backend = factory.create_backend(pool_key=thread_id, config=config)

    yield backend

    backend.close()


@pytest.fixture
def sandbox_agent(llm_client, sandbox_backend):
    """
    Create a DeepAgent with the sandbox_backend as its only tool.

    The agent exposes execute + file operations to the LLM.
    """
    from deepagents import create_deep_agent

    agent = create_deep_agent(
        model=llm_client,
        tools=[sandbox_backend],
        debug=False,
    )

    return agent
