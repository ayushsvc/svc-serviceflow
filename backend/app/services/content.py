from html.parser import HTMLParser


class _SafeHTML(HTMLParser):
    allowed_tags = {"p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li", "a"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self.allowed_tags:
            return
        if tag == "br":
            self.output.append("<br>")
            return
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href and href.lower().startswith(("http://", "https://", "mailto:")):
                safe_href = href.replace('"', "&quot;")
                self.output.append(f'<a href="{safe_href}" rel="nofollow noopener">')
                return
        self.output.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.allowed_tags and tag != "br":
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.output.append(data)


def sanitize_rich_text(value: str) -> str:
    parser = _SafeHTML()
    parser.feed(value)
    parser.close()
    return "".join(parser.output)
