"""Voyage AI embedding client for pointy-rag."""

import threading
import time

import voyageai

from pointy_rag.config import get_settings

_client: voyageai.Client | None = None
_client_lock = threading.Lock()

# Error substrings that indicate non-retryable auth/permission failures.
_AUTH_ERROR_PATTERNS = ("401", "403", "unauthorized", "forbidden", "invalid api key")


def get_voyage_client() -> voyageai.Client:
    """Get or create singleton Voyage AI client (thread-safe)."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        # Double-check after acquiring lock.
        if _client is not None:
            return _client
        settings = get_settings()
        if not settings.voyage_api_key:
            raise RuntimeError(
                "VOYAGE_API_KEY not set — configure in .env or environment"
            )
        _client = voyageai.Client(api_key=settings.voyage_api_key)
        return _client


def _is_auth_error(exc: Exception) -> bool:
    """Check if an exception indicates a non-retryable auth failure."""
    msg = str(exc).lower()
    return any(p in msg for p in _AUTH_ERROR_PATTERNS)


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
        RuntimeError: If all retry attempts fail or auth error occurs.
        TypeError: If texts contains non-string values.
    """
    if not texts:
        return []

    for i, t in enumerate(texts):
        if not isinstance(t, str):
            raise TypeError(f"texts[{i}] must be str, got {type(t).__name__}")

    client = get_voyage_client()
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        retry_count = 0
        last_error: Exception | None = None

        while retry_count < max_retries:
            try:
                result = client.embed(texts=batch, model=model)
            except Exception as e:
                last_error = e
                # Don't retry auth errors — they won't succeed on retry.
                if _is_auth_error(e):
                    raise RuntimeError(
                        "Voyage API authentication failed — check VOYAGE_API_KEY"
                    ) from e
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = 2 ** (retry_count - 1)
                    time.sleep(wait_time)
                else:
                    raise RuntimeError(
                        f"Embedding failed after {max_retries} retries "
                        f"(batch {i // batch_size + 1})"
                    ) from last_error
                continue

            # Validate response count outside try/except so mismatches
            # propagate immediately without retrying.
            if len(result.embeddings) != len(batch):
                raise RuntimeError(
                    f"Voyage API returned {len(result.embeddings)} embeddings "
                    f"for {len(batch)} texts (batch {i // batch_size + 1})"
                )
            all_embeddings.extend(result.embeddings)
            break

    return all_embeddings


def embed_query(query: str, model: str = "voyage-4-lite") -> list[float]:
    """Embed a single query string. Convenience wrapper around embed_texts."""
    results = embed_texts([query], model=model)
    return results[0]


def reset_client() -> None:
    """Reset the singleton client (for testing)."""
    global _client
    with _client_lock:
        _client = None
