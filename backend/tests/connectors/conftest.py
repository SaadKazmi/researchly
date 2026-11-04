import pytest


def _drop_set_cookie(response):
    """NCBI sets an `ncbi_sid` session cookie on every answer: never keep one in a cassette."""
    response["headers"] = {k: v for k, v in response["headers"].items() if k.lower() != "set-cookie"}
    return response


@pytest.fixture(scope="module")
def vcr_config():
    return {
        "before_record_response": _drop_set_cookie,
        # no record_mode here: pytest-recording defaults to "none" (a missing cassette fails the test) and a
        # pinned value would override --record-mode, so recording could never be asked for
        "filter_headers": ["authorization", "x-api-key", "cookie", "set-cookie", "user-agent"],
        "filter_query_parameters": ["api_key", "mailto", "email"],  # a mailto is the user's own address
        "decode_compressed_response": True,
        "match_on": ("method", "scheme", "host", "port", "path", "query", "body"),
    }


@pytest.fixture
async def polite(request):
    """A PoliteClient for cassette tests: instant in replay, real rate limits when recording live."""
    from researchly.connectors.http import PoliteClient

    recording = request.config.getoption("--record-mode") not in (None, "none")

    async def no_sleep(_: float) -> None:
        pass  # a recorded 429/5xx must never stall replay

    client = PoliteClient() if recording else PoliteClient(limits={"default": (0.0, 1)}, sleep=no_sleep)
    async with client:
        yield client
