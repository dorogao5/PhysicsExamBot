from src.utils.text import chunk_markdown_by_size, normalize_topic_key, sanitize_fts_query


def test_normalize_topic_key() -> None:
    assert normalize_topic_key("Уравнения Максвелла в вакууме") == "уравнения_максвелла_в_вакууме"


def test_sanitize_fts_query() -> None:
    assert sanitize_fts_query("E, dD/dt + ток") == "e OR dd OR dt OR ток"


def test_chunk_markdown_by_size() -> None:
    pages = [
        (1, "A" * 350),
        (2, "B" * 360),
        (3, "C" * 370),
    ]
    chunks = chunk_markdown_by_size(pages, min_chars=600, max_chars=900)
    assert len(chunks) == 2
    assert chunks[0].start_page == 1
    assert chunks[0].end_page == 2
    assert chunks[1].start_page == 3
