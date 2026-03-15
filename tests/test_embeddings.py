"""Tests for the Voyage AI embedding client."""

from unittest.mock import MagicMock, patch

import pytest

from pointy_rag.embeddings import embed_query, embed_texts, get_voyage_client, reset_client


def _make_mock_client(embedding_dim: int = 4) -> MagicMock:
    """Return a mock voyageai.Client whose .embed() yields deterministic vectors."""
    client = MagicMock()

    def fake_embed(texts: list[str], model: str) -> MagicMock:
        result = MagicMock()
        result.embeddings = [[float(i)] * embedding_dim for i in range(len(texts))]
        return result

    client.embed.side_effect = fake_embed
    return client


@pytest.fixture(autouse=True)
def reset_singleton():
    """Ensure the client singleton is reset before and after every test."""
    reset_client()
    yield
    reset_client()


# ---------------------------------------------------------------------------
# embed_texts
# ---------------------------------------------------------------------------


def test_embed_texts_empty():
    result = embed_texts([])
    assert result == []


def test_embed_texts_single():
    mock_client = _make_mock_client()
    with patch("pointy_rag.embeddings.get_voyage_client", return_value=mock_client):
        result = embed_texts(["hello world"])
    assert len(result) == 1
    assert isinstance(result[0], list)
    mock_client.embed.assert_called_once()


def test_embed_texts_batch():
    """More than batch_size texts should trigger multiple API calls."""
    mock_client = _make_mock_client()
    texts = [f"text {i}" for i in range(300)]

    with patch("pointy_rag.embeddings.get_voyage_client", return_value=mock_client):
        result = embed_texts(texts, batch_size=128)

    assert len(result) == 300
    # 300 texts / 128 batch = 3 calls (128 + 128 + 44)
    assert mock_client.embed.call_count == 3


def test_embed_texts_retry():
    """First call raises an exception; second call succeeds."""
    mock_client = MagicMock()
    call_count = 0

    def flaky_embed(texts: list[str], model: str) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("transient error")
        result = MagicMock()
        result.embeddings = [[0.1, 0.2] for _ in texts]
        return result

    mock_client.embed.side_effect = flaky_embed

    with (
        patch("pointy_rag.embeddings.get_voyage_client", return_value=mock_client),
        patch("pointy_rag.embeddings.time.sleep"),  # don't actually sleep
    ):
        result = embed_texts(["hello"], max_retries=3)

    assert len(result) == 1
    assert mock_client.embed.call_count == 2


def test_embed_texts_all_retries_fail():
    """All retry attempts fail — should raise an Exception."""
    mock_client = MagicMock()
    mock_client.embed.side_effect = ConnectionError("always fails")

    with (
        patch("pointy_rag.embeddings.get_voyage_client", return_value=mock_client),
        patch("pointy_rag.embeddings.time.sleep"),
    ):
        with pytest.raises(Exception, match="Failed after 3 retries"):
            embed_texts(["hello"], max_retries=3)

    assert mock_client.embed.call_count == 3


# ---------------------------------------------------------------------------
# embed_query
# ---------------------------------------------------------------------------


def test_embed_query():
    mock_client = _make_mock_client()
    with patch("pointy_rag.embeddings.get_voyage_client", return_value=mock_client):
        result = embed_query("what is RAG?")

    assert isinstance(result, list)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# get_voyage_client
# ---------------------------------------------------------------------------


def test_get_voyage_client_missing_key():
    """Raises RuntimeError when VOYAGE_API_KEY is empty."""
    mock_settings = MagicMock()
    mock_settings.voyage_api_key = ""

    with patch("pointy_rag.embeddings.get_settings", return_value=mock_settings):
        with pytest.raises(RuntimeError, match="VOYAGE_API_KEY not set"):
            get_voyage_client()


def test_client_singleton():
    """Second call to get_voyage_client returns the same instance."""
    mock_settings = MagicMock()
    mock_settings.voyage_api_key = "test-key-abc"

    with (
        patch("pointy_rag.embeddings.get_settings", return_value=mock_settings),
        patch("pointy_rag.embeddings.voyageai.Client") as MockClient,
    ):
        MockClient.return_value = MagicMock()
        client1 = get_voyage_client()
        client2 = get_voyage_client()

    assert client1 is client2
    MockClient.assert_called_once()


def test_reset_client():
    """After reset, a new client is created on the next call."""
    mock_settings = MagicMock()
    mock_settings.voyage_api_key = "test-key-xyz"

    instance1 = MagicMock()
    instance2 = MagicMock()

    with (
        patch("pointy_rag.embeddings.get_settings", return_value=mock_settings),
        patch("pointy_rag.embeddings.voyageai.Client") as MockClient,
    ):
        MockClient.side_effect = [instance1, instance2]
        client1 = get_voyage_client()
        reset_client()
        client2 = get_voyage_client()

    assert client1 is not client2
    assert client1 is instance1
    assert client2 is instance2
    assert MockClient.call_count == 2
