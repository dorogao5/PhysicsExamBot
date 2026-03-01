from __future__ import annotations

import re
from pathlib import Path


_QUESTION_PREFIX_RE = re.compile(r"^\d+\.\s*")


def load_comsa_questions(path: Path) -> list[str]:
    """Load questions from comsa_questions.md, one per non-empty line.

    Returns the full text of each question (with the number prefix stripped).
    """
    text = path.read_text(encoding="utf-8")
    questions: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        questions.append(line)
    return questions
