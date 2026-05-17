import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from personal_ai_os.skills.executors import execute_skill_tool
from personal_ai_os.skills.runtime import make_tool_executor


@pytest.mark.asyncio
async def test_make_tool_executor_passes_redis_kwarg() -> None:
    settings = MagicMock()
    settings.tavily_api_key = ""
    claude = MagicMock()
    conn = None
    user_id = uuid.uuid4()
    redis = MagicMock()

    exec_tool = make_tool_executor(
        settings=settings,
        claude=claude,
        conn=conn,
        user_id=user_id,
        redis=redis,
    )
    out = await exec_tool("web_search", {"query": "test"})
    assert "TAVILY_API_KEY" in out
