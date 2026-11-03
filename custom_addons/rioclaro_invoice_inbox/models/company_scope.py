"""Resolve explicit company mentions for private Telegram queries."""

import re
import unicodedata
from collections.abc import Iterable


def _normalize_company_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    without_abbreviation_periods = re.sub(
        r"(?<=\w)\.(?=\w)|(?<=\w)\.(?=\s|$)",
        "",
        without_marks,
    )
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in without_abbreviation_periods
        )
        .split()
    )


def resolve_company_selection(
    question: str,
    companies: Iterable[tuple[int, str]],
    allowed_company_ids: Iterable[int],
) -> tuple[str, tuple[int, ...]]:
    """Return matched authorized companies or a required/forbidden status.

    Company names are matched as whole phrases, ignoring case, accents, and
    punctuation. The result is ``("ok", ids)``, ``("required", ())``, or
    ``("forbidden", ())``.
    """
    normalized_question = f" {_normalize_company_name(question)} "
    candidates = [
        (company_id, _normalize_company_name(name))
        for company_id, name in companies
        if _normalize_company_name(name)
    ]
    matches = {
        company_id
        for company_id, name in candidates
        if f" {name} " in normalized_question
    }
    if not matches:
        return "required", ()
    if not matches.issubset(set(allowed_company_ids)):
        return "forbidden", ()
    return "ok", tuple(sorted(matches))
