from fastapi.responses import JSONResponse

from app.api.v1.healthy import healthy


class _BrokenSession:
    async def execute(self, _query):
        raise RuntimeError("db down")


class _WorkingSession:
    async def execute(self, _query):
        return 1


async def test_health_api_returns_ok_for_working_db():
    result = await healthy(db=_WorkingSession())
    assert result == {"status": "ok", "db": "connected"}


async def test_health_api_returns_503_for_failed_db():
    result = await healthy(db=_BrokenSession())
    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
    assert result.body == b'{"status":"error","db":"unavailable"}'
