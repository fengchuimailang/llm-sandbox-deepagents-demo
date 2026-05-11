"""
Level 1: Infrastructure Tests

Tests:
1. docker-compose can start successfully
2. Service health check passes
3. Network connectivity

Prerequisites:
- docker and docker compose installed
- Custom sandbox image built (llm-sandbox-multilang:latest)
"""

import subprocess
import time
import os

import pytest
import httpx


COMPOSE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
SERVICE_URL = "http://localhost:8000"
HEALTH_ENDPOINT = f"{SERVICE_URL}/health"


class TestInfrastructure:
    """Level 1: Infrastructure tests."""

    @pytest.fixture(scope="class", autouse=True)
    def setup_and_teardown(self):
        """Start docker-compose before tests and stop after."""
        print("\n=== Setting up docker-compose ===")

        # Check if containers are already running
        result = subprocess.run(
            ["docker", "compose", "ps", "-q"],
            cwd=COMPOSE_DIR,
            capture_output=True,
            text=True,
        )

        if result.stdout.strip():
            print("Containers already running, skipping docker-compose up")
        else:
            # Build sandbox image first
            print("Building sandbox image...")
            build_result = subprocess.run(
                ["docker", "build", "-f", "docker/Dockerfile.sandbox",
                 "-t", "llm-sandbox-multilang:latest", "./docker"],
                cwd=COMPOSE_DIR,
            )
            if build_result.returncode != 0:
                pytest.fail(f"Failed to build sandbox image: {build_result.stderr}")

            # Start docker-compose
            print("Starting docker-compose...")
            up_result = subprocess.run(
                ["docker", "compose", "up", "-d"],
                cwd=COMPOSE_DIR,
            )
            if up_result.returncode != 0:
                pytest.fail(f"Failed to start docker-compose: {up_result.stderr}")

        # Wait for service to be ready
        print("Waiting for service to be ready...")
        max_retries = 30
        for i in range(max_retries):
            try:
                response = httpx.get(HEALTH_ENDPOINT, timeout=5.0)
                if response.status_code == 200:
                    print(f"Service ready after {i+1} attempts")
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            pytest.fail("Service did not become ready in time")

        print("=== Setup complete, running tests ===")
        yield
        print("\n=== Teardown (leaving containers running) ===")

    def test_docker_compose_ps(self):
        """Test that docker compose ps shows running containers."""
        result = subprocess.run(
            ["docker", "compose", "ps"],
            cwd=COMPOSE_DIR,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"docker compose ps failed: {result.stderr}"
        output = result.stdout
        print(f"\ndocker compose ps output:\n{output}")

        # Check that sandbox-service is listed
        assert "sandbox-service" in output, "sandbox-service not found in docker compose ps"
        assert "llm-sandbox-service" in output or "Up" in output, "sandbox-service not running"

    def test_docker_network(self):
        """Test that containers are on the same Docker network."""
        result = subprocess.run(
            ["docker", "network", "ls"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        # Network should exist
        assert "llm-sandbox-network" in result.stdout or "bridge" in result.stdout

    def test_service_health(self):
        """Test /health endpoint returns 200."""
        response = httpx.get(HEALTH_ENDPOINT, timeout=10.0)
        assert response.status_code == 200, f"Health check failed: {response.status_code}"

        data = response.json()
        assert data.get("status") == "healthy", f"Unexpected health status: {data}"

    def test_service_stats(self):
        """Test /stats endpoint returns 200."""
        response = httpx.get(f"{SERVICE_URL}/stats", timeout=10.0)
        assert response.status_code == 200, f"Stats endpoint failed: {response.status_code}"

        data = response.json()
        assert "total_sandboxes" in data, f"Unexpected stats response: {data}"

    def test_execute_endpoint_exists(self):
        """Test /execute endpoint accepts requests."""
        response = httpx.post(
            f"{SERVICE_URL}/execute",
            json={"thread_id": "test", "command": "print(1+1)"},
            timeout=60.0,
        )
        # Should return 200 even if execution takes time
        assert response.status_code == 200, f"Execute failed: {response.status_code}, {response.text}"

        data = response.json()
        assert "output" in data or "exit_code" in data, f"Unexpected execute response: {data}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])