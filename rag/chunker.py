import re

from rag.errors import RagError

WHITESPACE = re.compile(r"\s")

# best place to cut first: paragraph, line, sentence end, then any space
SEPARATORS = ("\n\n", "\n", ". ", " ")


def check_chunk_settings(size, overlap) -> None:
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise RagError(f"rag_chunk_size must be a positive integer, got {size!r}")
    if not isinstance(overlap, int) or isinstance(overlap, bool) or overlap < 0:
        raise RagError(f"rag_chunk_overlap must be a non-negative integer, got {overlap!r}")
    if overlap >= size:
        raise RagError(
            f"rag_chunk_overlap ({overlap}) must be smaller than rag_chunk_size ({size})"
        )


def find_cut(text: str, end: int, min_cuts: tuple[int, ...]) -> int:
    # last separator before `end`, so chunks finish on a natural boundary;
    # each min_cut is a wider search window, tried only if the previous one had no separator
    for min_cut in min_cuts:
        for separator in SEPARATORS:
            position = text.rfind(separator, min_cut, end)
            if position != -1:
                return position + len(separator)
    return end


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Split text into chunks of at most `size` characters; each one starts
    `overlap` characters before the previous one ended."""
    check_chunk_settings(size, overlap)

    text = text.strip()
    chunks = []
    start = 0

    while start < len(text):
        end = min(start + size, len(text))

        if end < len(text):
            # prefer a cut in the second half so chunks stay full; any cut must land past
            # the overlap, otherwise the next chunk would not move forward
            half = start + max(size // 2, overlap + 1)
            end = find_cut(text, end, (half, start + overlap + 1))

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = end - overlap
        # do not start mid-word: move past the next whitespace if there is one inside the overlap
        if overlap and not text[start - 1].isspace():
            gap = WHITESPACE.search(text, start, end)
            if gap:
                start = gap.end()

    return chunks
