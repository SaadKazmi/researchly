import pytest


@pytest.fixture(scope="module")
def vcr_config():
    return {
        "filter_headers": ["authorization", "x-api-key", "cookie", "set-cookie"],
        "filter_query_parameters": ["api_key", "mailto", "email"],  # a mailto is the user's own address
        "decode_compressed_response": True,
        "match_on": ("method", "scheme", "host", "port", "path", "query", "body"),
    }
