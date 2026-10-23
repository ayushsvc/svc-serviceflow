from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit


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
