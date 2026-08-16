"""Build readable Chinese subtitles from the final narration transcript.

The public video is a synthetic competition demo. Subtitle timing is deliberately
deterministic: narration chunks are split at Chinese punctuation and allocated
against the measured audio duration, so rebuilding the video does not depend on
an external transcription service or a hidden model.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def chunks(text: str, limit: int = 26) -> list[str]:
    sentences = [item.strip() for item in re.split(r"(?<=[。！？；])", text) if item.strip()]
    result: list[str] = []
    for sentence in sentences:
        clauses = [item.strip() for item in re.split(r"(?<=[，：])", sentence) if item.strip()]
        current = ""
        for clause in clauses:
            if current and len(current) + len(clause) > limit:
                result.append(current)
                current = ""
            current += clause
        if current:
            result.append(current)
    return result


def timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def build(source: Path, output: Path, duration: float) -> None:
    paragraphs = [item.strip() for item in source.read_text(encoding="utf-8").split("\n\n") if item.strip()]
    lines = [line for paragraph in paragraphs for line in chunks(paragraph)]
    total_chars = sum(len(line) for line in lines)
    current = 0.0
    records: list[str] = []
    for index, line in enumerate(lines, start=1):
        end = duration if index == len(lines) else current + duration * len(line) / total_chars
        records.extend([str(index), f"{timestamp(current)} --> {timestamp(end)}", line, ""])
        current = end
    output.write_text("\n".join(records), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("video/narration.txt"))
    parser.add_argument("--output", type=Path, default=Path("video/subtitles.srt"))
    parser.add_argument("--duration", type=float, required=True)
    args = parser.parse_args()
    build(args.source, args.output, args.duration)


if __name__ == "__main__":
    main()
