# Pure unit tests — no Docker, no browser.
from rpa.playwright_tasks import _css_escape_attr


def test_css_escape_attr_passthrough():
    assert _css_escape_attr("tanaka@example.com") == "tanaka@example.com"


def test_css_escape_attr_escapes_double_quote():
    value = 'tanaka"x@example.com'
    assert _css_escape_attr(value) == 'tanaka\\"x@example.com'


def test_css_escape_attr_escapes_backslash():
    value = "a\\b"
    assert _css_escape_attr(value) == "a\\\\b"
