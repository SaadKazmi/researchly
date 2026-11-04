import httpx
import pytest
from vcr.errors import CannotOverwriteExistingCassetteException


@pytest.mark.vcr
def test_missing_cassette_blocks_the_network():
    with pytest.raises(CannotOverwriteExistingCassetteException):
        httpx.get("https://example.org/")
