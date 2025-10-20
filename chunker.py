from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class Chunk:
    """Represents a deterministic slice of a file."""

    content: str
    start_line: int
    end_line: int
    index: int
    chunk_id: str
    sha256: str


def _calculate_chunk_size(context_length: int, max_lines: int) -> int:
    # Use at most 30% of the available context for content and keep the chunk size stable.
    token_budget = max(1, int(context_length * 0.3))
    # Assume a conservative 5 tokens per line to avoid overshooting.
    estimated_lines = max(50, token_budget // 5)
    return max(1, min(max_lines, estimated_lines))


def generate_chunks(
    *,
    content: str,
    file_path: Path,
    relative_path: str,
    context_length: int,
    max_lines: int = 1000,
    overlap_ratio: float = 0.05,
) -> List[Chunk]:
    """Split a file into deterministic, overlapping line-based chunks."""

    lines = content.splitlines()
    total_lines = len(lines)

    if total_lines == 0:
        return [
            Chunk(
                content="",
                start_line=1,
                end_line=1,
                index=0,
                chunk_id=f"{relative_path}#{hashlib.sha256(content.encode('utf-8')).hexdigest()}@0",
                sha256=hashlib.sha256(content.encode('utf-8')).hexdigest(),
            )
        ]

    chunk_size = _calculate_chunk_size(context_length, max_lines)
    overlap = max(0, int(round(chunk_size * overlap_ratio)))
    file_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()

    chunks: List[Chunk] = []
    start_idx = 0
    chunk_index = 0

    while start_idx < total_lines:
        end_idx = min(total_lines, start_idx + chunk_size)
        chunk_lines = lines[start_idx:end_idx]
        start_line = start_idx + 1
        end_line = end_idx
        chunk_id = f"{relative_path}#{file_hash}@{chunk_index}"

        chunks.append(
            Chunk(
                content="\n".join(chunk_lines),
                start_line=start_line,
                end_line=end_line,
                index=chunk_index,
                chunk_id=chunk_id,
                sha256=file_hash,
            )
        )

        if end_idx == total_lines:
            break

        start_idx = end_idx - overlap if overlap > 0 else end_idx
        chunk_index += 1

    return chunks
