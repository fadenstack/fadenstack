"""An error a web server answered with is said in words, not in its HTML.

Chatting with a model the gateway reached over plain HTTP on the machines'
model-TLS port, the chat showed "upstream 400: <html> <head><title>400 The
plain HTTP request was sent to HTTPS port</title></head> <body>..." -- nginx's
error page, markup and all (the end-to-end test of 0.4.2, 11 October 2026).
"""

from __future__ import annotations

from faden_gateway.services.gateway.llm_adapter import _error_payload

NGINX_400 = (
    "<html>\r\n<head><title>400 The plain HTTP request was sent to HTTPS port</title></head>\r\n<body>\r\n"
    "<center><h1>400 Bad Request</h1></center>\r\n<center>The plain HTTP request was sent to HTTPS port</center>\r\n"
    "<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n"
)


def _message(text: str) -> str:
    return _error_payload("server_error", text)["error"]["message"]


def test_a_page_in_a_streamed_refusal_becomes_its_title() -> None:
    assert _message(f"upstream 400: {NGINX_400}") == "upstream 400: 400 The plain HTTP request was sent to HTTPS port"


def test_a_page_inside_a_library_error_becomes_its_title() -> None:
    assert _message(f"litellm.BadRequestError: OpenAIException - {NGINX_400}") == (
        "litellm.BadRequestError: OpenAIException - 400 The plain HTTP request was sent to HTTPS port"
    )


def test_a_page_without_a_title_is_reduced_to_its_text() -> None:
    page = "<!DOCTYPE html><html><body><h1>502 Bad Gateway</h1><p>The &quot;model&quot; is down</p></body></html>"
    assert _message(f"upstream 502: {page}") == 'upstream 502: 502 Bad Gateway The "model" is down'


def test_an_ordinary_message_is_left_alone() -> None:
    text = "The model did not start its reply within 180 s; it may be busy."
    assert _message(text) == text
    assert _message("max_tokens must be < 4096") == "max_tokens must be < 4096"
