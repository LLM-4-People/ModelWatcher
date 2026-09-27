"""Test: a provider's homepage is derived from its API URL without mangling the host.

Catches finding F12: favicons.root_url() kept the last two dot-separated labels of the
netloc, so an IP host became an invalid URL (https://127.0.0.1:9/v1 -> https://0.1:9) and
multi-part suffixes collapsed (api.example.co.uk -> co.uk). The same URL is the provider
link in /api/providers, so both the favicon fetch and the dashboard link were wrong.
"""
import pytest

from backend.favicons import root_url
from backend.state import is_ip_literal


@pytest.mark.parametrize("api_url, homepage", [
    # Hosts without a registrable domain are the server itself: kept whole, port included
    ("https://127.0.0.1:9/v1", "https://127.0.0.1:9"),
    ("http://10.0.0.5/v1", "http://10.0.0.5"),
    ("https://[::1]:8443/v1", "https://[::1]:8443"),
    ("http://localhost:11434/v1", "http://localhost:11434"),
    ("http://ollama:11434/v1", "http://ollama:11434"),
    # Multi-part public suffixes stay whole
    ("https://api.example.co.uk/v1", "https://example.co.uk"),
    ("https://api.example.com.au/v1", "https://example.com.au"),
    # Hosted (private) suffixes: the customer's name is the site, not the host platform
    ("https://foo.vercel.app/api", "https://foo.vercel.app"),
    # The common cases from the old docstring
    ("https://api.deepseek.com/v1", "https://deepseek.com"),
    ("https://nano-gpt.com/api/v1", "https://nano-gpt.com"),
    ("https://inference.api.novita.ai/v3", "https://novita.ai"),
    # A bare host gets https; credentials never leak into the link
    ("api.openai.com/v1", "https://openai.com"),
    ("https://user:secret@api.example.com/v1", "https://example.com"),
    # The port belongs to the API server: kept on the same host, dropped on the parent domain
    ("https://deepseek.com:8443/v1", "https://deepseek.com:8443"),
    ("https://api.deepseek.com:8443/v1", "https://deepseek.com"),
])
def test_root_url(api_url, homepage):
    assert root_url(api_url) == homepage


@pytest.mark.parametrize("host, is_ip", [
    ("127.0.0.1", True), ("::1", True), ("2001:db8::1", True),
    ("localhost", False), ("0.1", False), ("example.com", False), ("", False), ("[::1]", False),
])
def test_is_ip_literal(host, is_ip):
    assert is_ip_literal(host) is is_ip
