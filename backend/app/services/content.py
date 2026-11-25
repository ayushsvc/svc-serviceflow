from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit


MIN_DESCRIPTION_TEXT_LENGTH = 10


class _SafeHTML(HTMLParser):
    allowed_tags = {"p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li", "a"}
    suppressed_tags = {"embed", "iframe", "math", "noscript", "object", "script", "style", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.suppressed: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.suppressed:
            if tag in self.suppressed_tags:
                self.suppressed.append(tag)
            return
        if tag in self.suppressed_tags:
            self.suppressed.append(tag)
            return
        if tag not in self.allowed_tags:
            return
        if tag == "br":
            self.output.append("<br>")
            return
        if tag == "a":
            href = next((value for key, value in attrs if key == "href"), None)
            parsed = urlsplit((href or "").strip())
            if href and parsed.scheme.lower() in {"http", "https", "mailto"}:
                self.output.append(f'<a href="{escape(href.strip(), quote=True)}" rel="nofollow noopener">')
                return
        self.output.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if self.suppressed:
            if tag in self.suppressed:
                index = len(self.suppressed) - 1 - self.suppressed[::-1].index(tag)
                del self.suppressed[index:]
            return
        if tag in self.allowed_tags and tag != "br":
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.output.append(escape(data))


def sanitize_rich_text(value: str) -> str:
    parser = _SafeHTML()
    parser.feed(value)
    parser.close()
    return "".join(parser.output)


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "li", "p"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"li", "p"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def rich_text_to_plain(value: str) -> str:
    parser = _PlainText()
    parser.feed(value)
    parser.close()
    return " ".join("".join(parser.parts).split())


def sanitize_ticket_description(value: str) -> str:
    sanitized = sanitize_rich_text(value)
    plain_text = rich_text_to_plain(sanitized)
    if len(plain_text) < MIN_DESCRIPTION_TEXT_LENGTH:
        raise ValueError(
            f"Description must contain at least {MIN_DESCRIPTION_TEXT_LENGTH} characters of text."
        )
    return sanitized


POLITE_PHRASES = [
    r"\bthanks?\b",
    r"\bthank you\b",
    r"\ball (?:sorted|good|working|resolved|fixed)\b",
    r"\b(?:issue|it'?s?)\s+(?:is\s+)?(?:resolved|fixed|working)\b",
    r"\bworking now\b",
    r"\bappreciate (?:it|your help)\b",
    r"\bcheers\b",
    r"\bperfect\b",
    r"\bgreat\b",
    r"\bmuch appreciated\b",
    r"\bokay\b",
    r"\bok\b",
]


def is_polite_acknowledgment(value: str) -> bool:
    """Return True if message is a courteous closing/thank-you note rather than a new issue."""
    import re
    plain = rich_text_to_plain(value).strip().lower()
    if not plain or len(plain) > 150:
        return False
    # If the user is reporting it's still broken, NOT a polite acknowledgment
    if re.search(r"\b(?:not|still|doesn't|didn't|cannot|can't|broken|failed|error|wrong|reopen)\b", plain):
        return False
    return any(re.search(phrase, plain) for phrase in POLITE_PHRASES)
