"""
Multi-turn conversation integration tests.

Tests that the agent can maintain context across multiple user messages
while the sandbox state is preserved through the filesystem.

Note: Each async_execute runs in its own container (sandbox pool reuse),
so variable persistence across tool calls requires filesystem storage.
"""

import pytest


@pytest.mark.asyncio
async def test_twoturn_file_persistence(sandbox_agent, thread_id):
    """
    Test two-turn conversation where:
    - Turn 1: Agent writes x=5 to a file in the sandbox
    - Turn 2: Agent reads the file, computes x*2, prints result

    This verifies:
    - Multi-turn agent conversation works
    - Filesystem persists between tool calls (shared pool)
    - Agent correctly chains file write → file read → compute
    """
    result1 = await sandbox_agent.ainvoke(
        {
            "messages": [
                "Save the value 5 to a variable x by writing it to /workspace/state.txt like 'x = 5'. "
                "Then read it back to confirm it was saved."
            ]
        },
        config={"configurable": {"thread_id": thread_id}},
    )

    msg1 = result1["messages"][-1]
    # Should confirm the file was written
    content1 = msg1.content.lower()
    assert "state.txt" in content1 or "saved" in content1 or "confirm" in content1, (
        f"Turn 1 should confirm file write. Got: {msg1.content[:300]}"
    )

    result2 = await sandbox_agent.ainvoke(
        {
            "messages": [
                "Read the value of x from /workspace/state.txt, multiply it by 2, "
                "and print the result."
            ]
        },
        config={"configurable": {"thread_id": thread_id}},
    )

    msg2 = result2["messages"][-1]
    content2 = msg2.content.lower()

    # Should contain the computed result (10) or the computation
    assert "10" in content2 or "x * 2" in content2 or "multiply" in content2, (
        f"Turn 2 should compute x*2=10. Got: {msg2.content[:300]}"
    )


@pytest.mark.asyncio
async def test_three_turn_accumulator(sandbox_agent, thread_id):
    """
    Test three-turn accumulator: add 1, add 2, print total.

    Verifies the agent can track state across multiple turns using
    the filesystem as a backing store.
    """
    # Turn 1: Initialize accumulator
    result1 = await sandbox_agent.ainvoke(
        {"messages": ["Write the number 10 to /workspace/counter.txt"]},
        config={"configurable": {"thread_id": thread_id}},
    )

    # Turn 2: Add 5
    result2 = await sandbox_agent.ainvoke(
        {
            "messages": [
                "Read the current value from /workspace/counter.txt, "
                "add 5 to it, and write the new total back to /workspace/counter.txt"
            ]
        },
        config={"configurable": {"thread_id": thread_id}},
    )

    # Turn 3: Print final value
    result3 = await sandbox_agent.ainvoke(
        {"messages": ["Read /workspace/counter.txt and print the final value"]},
        config={"configurable": {"thread_id": thread_id}},
    )

    msg3 = result3["messages"][-1]
    content3 = msg3.content.lower()

    # Final value should be 15 (10 + 5)
    assert "15" in content3, (
        f"Final counter should be 15. Got: {msg3.content[:300]}"
    )
