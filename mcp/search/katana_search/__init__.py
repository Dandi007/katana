"""katana 域内检索 —— 每个治理域自持索引，共享无状态 embedding API。

设计：work folder wf-77510c `design-search-decentralization.md`
条款：docs/constitution/002-data-plane-privacy.md
"""

from katana_search.api import DomainSearch, SearchOutcome
from katana_search.embed import EmbeddingClient, EmbeddingUnavailable, default_client
from katana_search.index import SearchIndex, chunk_markdown

__all__ = [
    "DomainSearch",
    "SearchOutcome",
    "SearchIndex",
    "EmbeddingClient",
    "EmbeddingUnavailable",
    "chunk_markdown",
    "default_client",
]
