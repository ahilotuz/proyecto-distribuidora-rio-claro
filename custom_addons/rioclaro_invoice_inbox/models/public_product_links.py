"""Safe links for product recommendations backed by the public catalog."""

import html
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from markupsafe import Markup


def link_public_products(
    answer: str,
    rows: list[dict[str, Any]],
    *,
    base_url: str | None = None,
    line_break: Literal["<br/>", "\n"] = "<br/>",
    target_blank: bool = True,
) -> Markup:
    """Link exact published product names while escaping all generated text.

    Only Odoo shop paths are accepted. When ``base_url`` is supplied, links are
    made absolute using that configured site origin; any host in a product URL
    is discarded.
    """
    if line_break not in {"<br/>", "\n"}:
        raise ValueError("Unsupported product-link line break.")
    origin = _site_origin(base_url) if base_url is not None else ""
    links: dict[str, str] = {}
    for row in rows:
        name = row.get("name")
        url = row.get("url")
        if not isinstance(name, str) or not name.strip() or not isinstance(url, str):
            continue
        parsed_url = urlsplit(url)
        if (
            parsed_url.scheme not in {"", "http", "https"}
            or parsed_url.username
            or parsed_url.password
        ):
            continue
        path = parsed_url.path
        if (
            not path.startswith("/shop/")
            or path == "/shop/"
            or any(segment in {".", ".."} for segment in path.split("/"))
        ):
            continue
        href = f"{origin}{path}"
        if len(href) > 512:
            continue
        escaped_name = html.escape(name)
        escaped_href = html.escape(href, quote=True)
        attributes = ' target="_blank" rel="noopener noreferrer"' if target_blank else ""
        links[escaped_name] = f'<a href="{escaped_href}"{attributes}>{escaped_name}</a>'

    safe_answer = html.escape(answer)
    if links:
        alternatives = "|".join(
            re.escape(name) for name in sorted(links, key=len, reverse=True)
        )
        product_pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)")
        safe_answer = product_pattern.sub(
            lambda match: links[match.group(0)], safe_answer
        )
    return Markup(safe_answer.replace("\n", line_break))


def _site_origin(base_url: str) -> str:
    """Validate the configured Odoo base URL and return its scheme and host."""
    parsed_url = urlsplit(base_url.strip())
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise ValueError("The configured Odoo base URL is invalid.")
    return f"{parsed_url.scheme}://{parsed_url.netloc}"
