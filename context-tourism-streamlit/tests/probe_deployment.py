"""Non-authenticated public health check for the deployed Streamlit pilot."""
from __future__ import annotations

import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "https://appio-cf7w2tss4hxwxqd82wmkka.streamlit.app/"


def fetch(url: str):
    request = Request(url, headers={"User-Agent": "ContextTourismPilotHealthCheck/1.0"})
    with urlopen(request, timeout=25) as response:
        body = response.read(150000).decode("utf-8", "replace")
        return response.status, response.geturl(), body


def main():
    try:
        status, final_url, html = fetch(BASE)
        print(f"Public URL HTTP: {status}")
        print(f"Final URL: {final_url}")
        print(f"HTML title sample: {html[:220]!r}")
        if "login" in final_url.lower() or "auth" in final_url.lower():
            print("RESULT: LOGIN_REQUIRED (not a publicly open app)")
            return 2
        if status != 200:
            print("RESULT: UNAVAILABLE")
            return 3
        health_status, health_url, health_body = fetch(BASE.rstrip("/") + "/_stcore/health")
        print(f"Streamlit health HTTP: {health_status}; URL: {health_url}")
        print(f"Health response: {health_body[:200]!r}")
        if health_status == 200 and health_body.strip().lower() == "ok":
            print("RESULT: HEALTHY_SERVER_ONLY (UI interactions not verified)")
            return 0
        print("RESULT: HEALTH_ENDPOINT_UNCONFIRMED")
        return 4
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"RESULT: NETWORK_OR_ACCESS_ERROR: {type(exc).__name__}: {exc}")
        return 5


if __name__ == "__main__":
    sys.exit(main())
