"""
Reusable network interception utility for Fidelity browser automation.

Attaches to a Playwright page and captures XHR/fetch API traffic,
filtering to Fidelity domains. Useful for:
  - Discovering internal REST API endpoints
  - Extracting session auth mechanisms (cookies, CSRF tokens)
  - Building direct HTTP clients that bypass DOM automation

Usage:
    from fidelity.fidelity import FidelityAutomation
    from fidelity.network_capture import NetworkCapture

    fid = FidelityAutomation(headless=False, save_state=True)
    capture = NetworkCapture(fid.page)
    capture.start()

    fid.page.goto("https://digital.fidelity.com/...")
    # ... interact with page ...

    capture.stop()
    capture.export_json("captured_endpoints.json")
    capture.print_summary()
"""

import json
import time
import re
from dataclasses import dataclass, field, asdict
from typing import Optional
from urllib.parse import urlparse


# Fidelity domain patterns to capture
DEFAULT_FIDELITY_DOMAINS = [
    "fidelity.com",
    "fidelitystatic.com",
]

# Resource types to skip (not API calls)
SKIP_RESOURCE_TYPES = {
    "image",
    "stylesheet",
    "font",
    "media",
    "manifest",
    "other",
}

# URL path patterns to skip (static assets, analytics, tracking)
SKIP_URL_PATTERNS = [
    r"/analytics/",
    r"/beacon/",
    r"/tracking/",
    r"/pixel",
    r"\.js$",
    r"\.css$",
    r"\.woff",
    r"\.ttf",
    r"\.svg$",
    r"\.png$",
    r"\.jpg$",
    r"\.gif$",
    r"\.ico$",
    r"/favicon",
]


@dataclass
class CapturedRequest:
    """A single captured network request/response pair."""
    url: str
    method: str
    resource_type: str
    request_headers: dict = field(default_factory=dict)
    post_data: Optional[str] = None
    post_data_json: Optional[dict] = None
    response_status: Optional[int] = None
    response_headers: dict = field(default_factory=dict)
    response_body: Optional[str] = None
    response_json: Optional[dict] = None
    timestamp: float = 0.0
    duration_ms: float = 0.0
    # Derived fields
    url_path: str = ""
    url_domain: str = ""
    content_type: str = ""
    has_json_response: bool = False
    auth_mechanism: str = ""  # "cookie", "bearer", "csrf", etc.

    def __post_init__(self):
        parsed = urlparse(self.url)
        self.url_path = parsed.path
        self.url_domain = parsed.netloc


class NetworkCapture:
    """
    Captures XHR/fetch network traffic from a Playwright page.

    Attaches event listeners to intercept all network requests and responses,
    filters to Fidelity domains, and stores structured data for analysis.
    """

    def __init__(self, page, filter_domains: list[str] = None, skip_static: bool = True):
        """
        Parameters
        ----------
        page : playwright.sync_api.Page
            The Playwright page to attach listeners to.
        filter_domains : list[str], optional
            Domain suffixes to capture. Defaults to Fidelity domains.
        skip_static : bool
            If True, skip images, CSS, fonts, etc.
        """
        self.page = page
        self.filter_domains = filter_domains or DEFAULT_FIDELITY_DOMAINS
        self.skip_static = skip_static
        self.captured: list[CapturedRequest] = []
        self._pending: dict[str, CapturedRequest] = {}  # url+method -> CapturedRequest
        self._active = False
        self._request_handler = None
        self._response_handler = None

    def start(self):
        """Start capturing network traffic."""
        if self._active:
            return
        self._active = True
        self._request_handler = lambda req: self._on_request(req)
        self._response_handler = lambda resp: self._on_response(resp)
        self.page.on("request", self._request_handler)
        self.page.on("response", self._response_handler)

    def stop(self):
        """Stop capturing network traffic."""
        if not self._active:
            return
        self._active = False
        self.page.remove_listener("request", self._request_handler)
        self.page.remove_listener("response", self._response_handler)

    def clear(self):
        """Clear all captured requests."""
        self.captured.clear()
        self._pending.clear()

    def _should_capture(self, url: str, resource_type: str) -> bool:
        """Check if this request should be captured."""
        # Domain filter
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if not any(domain.endswith(d) for d in self.filter_domains):
            return False

        # Skip static resources
        if self.skip_static and resource_type in SKIP_RESOURCE_TYPES:
            return False

        # Skip known static URL patterns
        if self.skip_static:
            path = parsed.path.lower()
            for pattern in SKIP_URL_PATTERNS:
                if re.search(pattern, path):
                    return False

        return True

    def _on_request(self, request):
        """Handle a network request event."""
        url = request.url
        resource_type = request.resource_type

        if not self._should_capture(url, resource_type):
            return

        # Parse POST data
        post_data = request.post_data
        post_data_json = None
        if post_data:
            try:
                post_data_json = json.loads(post_data)
            except (json.JSONDecodeError, TypeError):
                pass

        # Extract auth info from headers
        headers = dict(request.headers)
        auth_mechanism = self._detect_auth(headers)

        cap = CapturedRequest(
            url=url,
            method=request.method,
            resource_type=resource_type,
            request_headers=headers,
            post_data=post_data,
            post_data_json=post_data_json,
            timestamp=time.time(),
            auth_mechanism=auth_mechanism,
        )

        # Store as pending until response arrives
        key = f"{request.method}:{url}"
        self._pending[key] = cap

    def _on_response(self, response):
        """Handle a network response event."""
        key = f"{response.request.method}:{response.request.url}"
        cap = self._pending.pop(key, None)
        if cap is None:
            return

        cap.response_status = response.status
        cap.response_headers = dict(response.headers)
        cap.duration_ms = (time.time() - cap.timestamp) * 1000

        # Extract content type
        content_type = response.headers.get("content-type", "")
        cap.content_type = content_type

        # Try to get response body
        try:
            if "json" in content_type or "javascript" in content_type:
                body = response.text()
                cap.response_body = body
                try:
                    cap.response_json = json.loads(body)
                    cap.has_json_response = True
                except (json.JSONDecodeError, TypeError):
                    pass
            elif "text" in content_type or "html" in content_type or "xml" in content_type:
                body = response.text()
                # Only store if reasonable size (< 50KB)
                if len(body) < 50_000:
                    cap.response_body = body
        except Exception:
            # Some responses can't be read (streaming, etc.)
            pass

        self.captured.append(cap)

    def _detect_auth(self, headers: dict) -> str:
        """Detect authentication mechanism from request headers."""
        mechanisms = []

        if "authorization" in headers:
            auth = headers["authorization"]
            if auth.lower().startswith("bearer"):
                mechanisms.append("bearer")
            else:
                mechanisms.append("auth-header")

        if "x-csrf-token" in headers or "x-xsrf-token" in headers:
            mechanisms.append("csrf")

        if "cookie" in headers:
            mechanisms.append("cookie")

        return "+".join(mechanisms) if mechanisms else "none"

    # --- Query methods ---

    def get_api_requests(self) -> list[CapturedRequest]:
        """Return only requests that look like API calls (JSON responses or POST with data)."""
        return [
            r for r in self.captured
            if r.has_json_response
            or r.post_data_json is not None
            or r.method in ("POST", "PUT", "PATCH", "DELETE")
        ]

    def find(self, url_pattern: str) -> list[CapturedRequest]:
        """Find captured requests matching a URL pattern (regex)."""
        regex = re.compile(url_pattern, re.IGNORECASE)
        return [r for r in self.captured if regex.search(r.url)]

    def find_by_response_key(self, key: str) -> list[CapturedRequest]:
        """Find requests whose JSON response contains a specific key (top-level)."""
        results = []
        for r in self.captured:
            if r.response_json and isinstance(r.response_json, dict):
                if key in r.response_json:
                    results.append(r)
        return results

    def get_unique_endpoints(self) -> dict[str, list[CapturedRequest]]:
        """Group captured requests by URL path (ignoring query params)."""
        groups: dict[str, list[CapturedRequest]] = {}
        for r in self.captured:
            path = r.url_path
            groups.setdefault(path, []).append(r)
        return groups

    def get_auth_summary(self) -> dict:
        """Summarize authentication mechanisms seen across all requests."""
        mechs: dict[str, int] = {}
        csrf_tokens = set()
        cookie_names = set()

        for r in self.captured:
            mechs[r.auth_mechanism] = mechs.get(r.auth_mechanism, 0) + 1

            # Collect CSRF tokens
            for header in ("x-csrf-token", "x-xsrf-token"):
                if header in r.request_headers:
                    csrf_tokens.add(r.request_headers[header])

            # Collect cookie names
            if "cookie" in r.request_headers:
                for part in r.request_headers["cookie"].split(";"):
                    name = part.strip().split("=")[0]
                    cookie_names.add(name)

        return {
            "mechanisms": mechs,
            "csrf_tokens": list(csrf_tokens),
            "cookie_names": sorted(cookie_names),
        }

    # --- Export methods ---

    def export_json(self, path: str, api_only: bool = True):
        """Export captured requests to a JSON file."""
        requests = self.get_api_requests() if api_only else self.captured

        data = {
            "capture_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_captured": len(self.captured),
            "api_requests": len(self.get_api_requests()),
            "auth_summary": self.get_auth_summary(),
            "requests": [asdict(r) for r in requests],
        }

        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

        print(f"Exported {len(requests)} requests to {path}")

    def print_summary(self):
        """Print a human-readable summary of captured traffic."""
        api_requests = self.get_api_requests()
        endpoints = self.get_unique_endpoints()
        auth = self.get_auth_summary()

        print(f"\n{'='*70}")
        print(f"  NETWORK CAPTURE SUMMARY")
        print(f"{'='*70}")
        print(f"\n  Total requests captured: {len(self.captured)}")
        print(f"  API requests (JSON/POST): {len(api_requests)}")
        print(f"  Unique URL paths: {len(endpoints)}")

        # Auth summary
        print(f"\n  Authentication:")
        for mech, count in auth["mechanisms"].items():
            print(f"    {mech}: {count} requests")
        if auth["csrf_tokens"]:
            print(f"    CSRF tokens found: {len(auth['csrf_tokens'])}")
        if auth["cookie_names"]:
            print(f"    Cookie names ({len(auth['cookie_names'])}): {', '.join(list(auth['cookie_names'])[:10])}")

        # API endpoints
        print(f"\n  API Endpoints:")
        for req in api_requests:
            status = req.response_status or "???"
            method = req.method
            path = req.url_path
            duration = f"{req.duration_ms:.0f}ms"
            content = ""
            if req.response_json and isinstance(req.response_json, dict):
                keys = list(req.response_json.keys())[:5]
                content = f" keys={keys}"
            elif req.response_json and isinstance(req.response_json, list):
                content = f" [{len(req.response_json)} items]"
            print(f"    [{status}] {method:6} {path[:80]}  ({duration}){content}")

        print(f"\n{'='*70}\n")
