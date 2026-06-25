import html
import re

# Block-level tags that should produce a line break when removed
_BLOCK_TAGS = re.compile(
    r'<\s*/?\s*(?:div|p|br|hr|li|tr|th|td|h[1-6]|blockquote|pre|ul|ol|table|thead|tbody|tfoot|section|article|header|footer|nav|main|aside|details|summary|figcaption|figure)\b[^>]*/?>',
    re.IGNORECASE
)


def clean_html(value):
    """Strip HTML tags and unescape entities, preserving line structure."""
    if not isinstance(value, str):
        return value
    # Quick check: skip processing if no HTML-like content
    if '<' not in value and '&' not in value:
        return value
    # Replace block-level tags with newlines
    text = _BLOCK_TAGS.sub('\n', value)
    # Remove remaining inline tags (span, strong, em, a, etc.)
    text = re.sub(r'<[^>]+>', '', text)
    # Unescape HTML entities (&amp; -> &, &nbsp; -> space, etc.)
    text = html.unescape(text)
    # Collapse multiple blank lines into at most one, trim each line
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return '\n'.join(lines)
