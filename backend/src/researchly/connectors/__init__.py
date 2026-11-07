"""Connector registry (spec §8.1): the keyless Tier-1 and Tier-2 sources."""

from researchly.connectors.arxiv import ArxivConnector
from researchly.connectors.base import Connector
from researchly.connectors.biorxiv import BiorxivConnector
from researchly.connectors.crossref import CrossrefConnector
from researchly.connectors.datacite import DataCiteConnector
from researchly.connectors.dblp import DblpConnector
from researchly.connectors.europepmc import EuropePmcConnector
from researchly.connectors.hf import HfConnector
from researchly.connectors.http import PoliteClient
from researchly.connectors.openaire import OpenAireConnector
from researchly.connectors.openalex import OpenAlexConnector
from researchly.connectors.opencitations import OpenCitationsConnector
from researchly.connectors.openreview import OpenReviewConnector
from researchly.connectors.pubmed import PubMedConnector


def build_connectors(client: PoliteClient) -> dict[str, Connector]:
    connectors: list[Connector] = [
        OpenAlexConnector(client),
        ArxivConnector(client),
        CrossrefConnector(client),
        OpenCitationsConnector(client),
        DataCiteConnector(client),
        EuropePmcConnector(client),
        PubMedConnector(client),
        HfConnector(client),
        BiorxivConnector(client),
        OpenReviewConnector(client),
        DblpConnector(client),
        OpenAireConnector(client),
    ]
    return {c.name: c for c in connectors}
