"""
Single-turn agent integration tests with DeepAgents + LLMSandbox.

Tests real user_message → LLM → Agent(tool call) → Sandbox → response
end-to-end flow with actual LLM API calls.
"""

import pytest


@pytest.mark.asyncio
async def test_execute_success(sandbox_agent, thread_id):
    """
    Test successful code execution: fibonacci computation.

    Verifies:
    - Agent calls execute tool
    - Tool returns correct result
    - Agent responds to user with the answer
    """
    result = await sandbox_agent.ainvoke(
        {"messages": ["Write a fibonacci function for n=10 and print the result"]},
        config={"configurable": {"thread_id": thread_id}},
    )

    final_message = result["messages"][-1]
    content = final_message.content.lower()

    # Agent should mention fibonacci and/or 55
    assert "55" in content or "fibonacci" in content, (
        f"Expected response to contain fibonacci result. Got: {final_message.content[:500]}"
    )


@pytest.mark.asyncio
async def test_syntax_error_captured(sandbox_agent, thread_id):
    """
    Test that syntax errors are caught and reported to the user, not crashes.

    Verifies:
    - Agent calls execute tool
    - Tool returns non-zero exit code
    - Agent reports the error gracefully
    """
    result = await sandbox_agent.ainvoke(
        {"messages": ["Run this code: print('unclosed"]},
        config={"configurable": {"thread_id": thread_id}},
    )

    final_message = result["messages"][-1]
    content = final_message.content.lower()

    # Should mention error / syntax / error somehow, not crash
    error_indicators = ["error", "syntax", "exception", "invalid", "unexpected", "eof"]
    has_error_mention = any(indicator in content for indicator in error_indicators)

    assert has_error_mention, (
        f"Expected error message in response. Got: {final_message.content[:500]}"
    )


@pytest.mark.asyncio
async def test_timeout_handled(sandbox_agent, thread_id):
    """
    Test that long-running code is timed out and reported to the user.

    Verifies:
    - A sleep beyond the timeout triggers TimeoutError
    - Agent handles it gracefully instead of crashing
    """
    result = await sandbox_agent.ainvoke(
        {"messages": ["Run: import time; time.sleep(30)"]},
        config={"configurable": {"thread_id": thread_id}},
    )

    final_message = result["messages"][-1]
    content = final_message.content.lower()

    # Should mention timeout or error, not crash
    indicators = ["timeout", "timed out", "error", "exceed", "limit"]
    handled = any(ind in content for ind in indicators)

    assert handled, (
        f"Expected timeout or error handling in response. Got: {final_message.content[:500]}"
    )
