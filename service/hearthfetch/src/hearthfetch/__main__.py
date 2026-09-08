"""Entry point.

Reads configuration from the environment, wires the real adapters, and serves.
Nothing here is model-influenced: every endpoint, credential, model name, and
limit comes from the deployment.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
from importlib.resources import files

from hearthfetch.api import ToolService, build_server
from hearthfetch.config import PREFIX, Config
from hearthfetch.llm import LiteLLMModel
from hearthfetch.pipeline import Pipeline
from hearthfetch.search import SearxngProvider

def _openapi() -> dict:
    """Read the spec from inside the package.

    Not a path relative to __file__: that resolves in the source tree and points
    outside site-packages once installed, so the container would start and then
    fail to serve the document OpenWebUI registers against.
    """
    return json.loads((files("hearthfetch") / "openapi.json").read_text(encoding="utf-8"))


def _resolver(host: str, port: int) -> list[str]:
    """Resolve to every address, so `fetch` can reject a mixed answer.

    Returning only the first would let a host that resolves to one public and
    one private address slip the private one past the check on a later attempt.
    """
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def _required(name: str) -> str:
    value = os.environ.get(PREFIX + name, "")
    if not value:
        print(f"{PREFIX}{name} is required", file=sys.stderr)
        raise SystemExit(2)
    return value


def main() -> None:
    config = Config.from_env()
    token = _required("TOOL_TOKEN")
    handle_key = bytes.fromhex(_required("HANDLE_KEY"))
    searxng_url = _required("SEARXNG_URL")
    if not config.litellm_base_url:
        print(f"{PREFIX}LITELLM_BASE_URL is required", file=sys.stderr)
        raise SystemExit(2)

    model = LiteLLMModel(
        base_url=config.litellm_base_url,
        api_key=_required("LITELLM_API_KEY"),
    )
    service = ToolService(
        config=config,
        pipeline=Pipeline(
            config=config,
            distiller_model=model,
            # Same transport, separate configuration: the classifier is a
            # different job with a different model, budget and timeout.
            classifier_model=model,
        ),
        search_provider=SearxngProvider(
            base_url=searxng_url,
            timeout_seconds=config.fetch_policy.timeout_seconds,
        ),
        handle_key=handle_key,
        resolver=_resolver,
        connector=_connector,
    )

    server = build_server(
        service,
        token=token,
        openapi=_openapi(),
        host=os.environ.get(PREFIX + "HOST", "0.0.0.0"),
        port=int(os.environ.get(PREFIX + "PORT", "8080")),
    )

    def stop(signum, _frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.serve_forever()


def _connector(target, address, *, timeout):
    """Connect to the validated address, carrying the hostname separately.

    This is where connect-time validation is actually enforced: the socket goes
    to the IP `fetch` checked, while SNI and the Host header carry the original
    name. There is no second DNS lookup for a rebinding attack to win.
    """
    import http.client
    import ssl

    from hearthfetch.fetch import RawResponse

    headers = {
        "Host": target.host if target.port in (80, 443) else f"{target.host}:{target.port}",
        "User-Agent": "hearthfetch",
        # No Accept-Encoding: no compression means no decompression bomb.
        "Accept": "text/html,text/plain",
        "Accept-Encoding": "identity",
    }
    if target.scheme == "https":
        context = ssl.create_default_context()
        connection = http.client.HTTPSConnection(
            address, target.port, timeout=timeout, context=context
        )
        # Certificate and SNI follow the hostname, not the address we dialled.
        connection.host = target.host
    else:
        connection = http.client.HTTPConnection(address, target.port, timeout=timeout)
    try:
        connection.putrequest("GET", target.path, skip_host=True, skip_accept_encoding=True)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders()
        response = connection.getresponse()
        # One byte over the cap is enough to know it is over the cap.
        body = response.read(2_000_001)
        return RawResponse(
            status=response.status,
            headers={k: v for k, v in response.getheaders()},
            body=body,
        )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
