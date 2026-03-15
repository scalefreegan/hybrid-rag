"""Voyage AI embedding client for pointy-rag."""

import time

import voyageai

from pointy_rag.config import get_settings

_client: voyageai.Client | None = None


def get_voyage_client() -> voyageai.Client:
    """Get or create singleton Voyage AI client."""
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.voyage_api_key:
            raise RuntimeError(
                "VOYAGE_API_KEY not set — configure in .env or environment"
            )
        _client = voyageai.Client(api_key=settings.voyage_api_key)
    return _client


def embed_texts(
    texts: list[str],
    model: str = "voyage-4-lite",
    max_retries: int = 3,
    batch_size: int = 128,
) -> list[list[float]]:
    """Generate embeddings for a list of texts.

    Args:
        texts: List of text strings to embed
        model: Voyage AI model (voyage-4-lite produces 1024-dim vectors)
        max_retries: Retry attempts with exponential backoff
        batch_size: Texts per API call (Voyage limit: 128)

    Returns:
        List of embedding vectors (each 1024 floats for voyage-4-lite)

    Raises:
        Exception: If all retry attempts fail
    """
    if not texts:
        return []

    client = get_voyage_client()
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        retry_count = 0
        last_error: Exception | None = None

        while retry_count < max_retries:
            try:
                result = client.embed(texts=batch, model=model)
                all_embeddings.extend(result.embeddings)
                break
            except Exception as e:
                retry_count += 1
                last_error = e
                if retry_count < max_retries:
                    wait_time = 2 ** (retry_count - 1)
                    time.sleep(wait_time)
                else:
                    msg = f"Failed after {max_retries} retries: {e}"
                    raise Exception(msg) from last_error

    return all_embeddings


def embed_query(query: str, model: str = "voyage-4-lite") -> list[float]:
    """Embed a single query string. Convenience wrapper around embed_texts."""
    results = embed_texts([query], model=model)
    return results[0]


def reset_client() -> None:
    """Reset the singleton client (for testing)."""
    global _client
    _client = None
