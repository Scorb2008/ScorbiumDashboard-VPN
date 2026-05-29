import html
from typing import Optional


def escape_html(text: Optional[str]) -> str:
    """Экранирует HTML-символы для безопасного вывода."""
    if not text:
        return ""
    return html.escape(str(text), quote=True)


def html_code(text: Optional[str]) -> str:
    """Оборачивает текст в <code> с HTML-экранированием."""
    return f"<code>{escape_html(text)}</code>"


def truncate(text: Optional[str], length: int = 100, suffix: str = "...") -> str:
    """Обрезает текст до указанной длины."""
    if not text:
        return ""
    text = str(text)
    if len(text) <= length:
        return text
    return text[: length - len(suffix)] + suffix


def sanitize_search_query(query: str, max_length: int = 50) -> str:
    """Санитизирует поисковый запрос от ReDoS атак."""
    if not query:
        return ""
    query = query.strip()[:max_length]
    dangerous_chars = ["%", "_", "[", "]", "^", "\\"]
    for char in dangerous_chars:
        query = query.replace(char, "?")
    return query
