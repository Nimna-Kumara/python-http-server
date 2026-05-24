import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote_plus

from config import settings


class ParseError(Exception):
    def __init__(self, message: str, status_hint: int = 400):
        self.status_hint = status_hint
        super().__init__(message)

    
# Data model

@dataclass
class HTTPRequest:
    method: str
    path: str
    version: str
    headers: Dict[str, str] 
    body: bytes
    query_params: Dict[str, str]
    path_params: Dict[str, str]
    client_ip: str
    raw: bytes


    @property
    def content_type(self) -> str:
        raw = self.headers.get("content-type", "")
        return raw.split(";")[0].strip().lower()

    @property
    def content_length(self) -> int:
        try:
            return int(self.headers.get("content-length", "0"))
        except ValueError:
            return 0

    @property
    def is_keep_alive(self) -> bool:
        conn = self.headers.get("connection", "").lower()
        if self.version == "HTTP/1.1":
            return conn != "close"
        return conn == "keep-alive"

    @property
    def accept(self) -> List[str]:
        raw = self.headers.get("accept", "*/*")
        return [part.split(";")[0].strip() for part in raw.split(",")]

    @property
    def cookies(self) -> Dict[str, str]:
        jar: Dict[str, str] = {}
        raw = self.headers.get("cookie", "")
        for pair in raw.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, _, v = pair.partition("=")
                jar[k.strip()]  = v.strip()  
        return jar

    def json(self):
        import json
        if not self.body:
            raise ValueError("Request body is empty.")
        if "json" not in self.content_type and self.content_type != "":
            raise ValueError(
                f"Expected application/json, got {self.content_type!r}"
            )
        return json.loads(self.body.decode("utf-8"))


    def form_data(self) -> Dict[str, str]:
        if "application/x-www-form-urlencoded" not in self.content_type:
            raise ValueError(
                f"Expected application/x-www-form-urlencoded, "
                f"got {self.content_type!r}"
            )
        return _parse_query_pairs(self.body.decode("utf-8", errors="replace"))

    def __repr__(self) -> str:
        return (
            f"<HTTPRequest {self.method} {self.path!r}"
            f"ver={self.version} bod={len(self.body)}B>"
        )


class RequestParser:

    _REQUEST_LINE_RE = re.compile(
        r"^([A-Z]{1,16}) "
        r"(\S+) "
        r"(HTTP/[0-9]\.[0-9])$"
    )

    _SUPPORTED_VERSIONS = {"HTTP/1.0", "HTTP/1.1"}

    _MAX_HEADERS = 100

    def parse(self, raw: bytes, client_ip: str = "") -> HTTPRequest:
        if not raw:
            raise ParseError("Empty request.", status_hint=400)

        if len(raw) > settings.MAX_REQUEST_SIZE:
            raise ParseError(
                f"Request size {len(raw)} exceeds limit "
                f"{settings.MAX_REQUEST_SIZE}.",
                status_hint=413
            )

        head_bytes, sep, body_bytes = raw.partition(b"\r\n\r\n")
        if not sep:
            raise ParseError(
                "Missing header/body separator (\\r\\n\\r\\n)",
                status_hint=400
            )

        try:
            head = head_bytes.decode("latin-1")
        except Exception as exc:
            raise ParseError(f"Cannot decode request head: {exc}") from exc

        lines = head.split("\r\n")
        if not lines:
            raise ParseError("Request head is empty.", status_hint=400)

        # Request line
        method, raw_target, version = self._parse_request_line(lines[0])

        # Headers
        headers = self._parse_headers(lines[1:])

        # Request target → path + query string 
        path, query_params = self._parse_target(raw_target)

        # Body 
        body = self._read_body(body_bytes, headers, method)

        return HTTPRequest(
            method=method,
            path=path,
            version=version,
            headers=headers,
            body=body,
            query_params=query_params,
            client_ip=client_ip,
            raw=raw
        )


        # private helpers

    def _parse_request_line(self, line: str) -> Tuple[str, str, str]:
        line = line.strip()
        if not line:
            raise ParseError("Request-line is empty", status_hint=400)

        m = self._REQUEST_LINE_RE.match(line)
        if not m:
            raise ParseError(
                f"Malformed request-line: {line!r}", status_hint=400
            )
        
        method, target, version = m.group(1), m.group(2), m.group(3)

        if version not in self._SUPPORTED_VERSIONS:
            raise ParseError(
                f"Unsupported HTTP version: {version!r}",
                status_hint=505
            )

        if len(method) > 16:
           raise ParseError(
            f"Method token too long: {method!r}", status_hint=400
           ) 

        return method, target, version
                
    
    def _parse_headers(self, lines: List[str]) -> Dict[str, str]:

        if len(lines) > self._MAX_HEADERS:
            raise ParseError(
                f"Too many headers (max {self._MAX_HEADERS})", 
                status_hint=431
            )

        headers: Dict[str, str] = {}
        current_key: Optional[str] = None

        for raw_line in lines:
            if not raw_line:
                break  # blank line marks end of headers

            # Folded continuation line (RFC 7230 §3.2.6 — obsolete but common)
            if raw_line[0] in (" ", "\t") and current_key:
                headers[current_key] = (
                    headers[current_key] + " " + raw_line.strip()
                )
                continue

            if ":" not in raw_line:
                raise ParseError(
                    f"Invalid header line (no colon): {raw_line!r}",
                    status_hint = 400
                )    

            key, _, value = raw_line.partition(":")

            key = key.strip()
            if " " in key or "\t" in key:
                raise ParseError(
                    f"Header name contains whitespace: {key!r}",
                    status_hint=400
                )

            current_key = key.lower()
            headers[current_key] = value.strip()

        return headers


    def _parse_target(self, target: str) -> Tuple[str, Dict[str, str]]:
        if target == "*":
            return "*", {}

        if target.startswith("http://") or target.startswith("https://"):
            after_scheme = target.split("//", 1)[1]
            _, _, rest = after_scheme.partition("/")
            target = "/" + rest

        if "?" in target:
            raw_path, _, raw_qs = target.partition("?")
        else:
            raw_path, raw_qs = target, ""

        try:
            path = unquote_plus(raw_path, encoding="utf-8")
        except Exception:
            path = raw_path

        while "//" in path:
            path = path.replace("//", "/")
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")

        querry_params = _parse_query_pairs(raw_qs) if raw_qs else {}

        return path or "/", querry_params


    def _read_body(
        self,
        body_bytes: bytes,
        headers: Dict[str, str],
        method: str
    ) -> bytes:
        _BODYLESS = {"GET","HEAD", "DELETE", "OPTIONS", "TRACE"}
        if method in _BODYLESS:
            return b""

        te = headers.get("transfer-encoding", "").lower()
        if "chunked" in te:
            raise ParseError(
                "Chunked transfer-encoding is not supported.",
                status_hint=501
            )

        try:
            declared_length = int(headers.get("content-length", "0"))
        except ValueError:
            raise ParseError(
                "Content-Length is not a valid integer.", status_hint=400
            )

        if declared_length < 0:
            raise ParseError(
                "Content-Length must not be negative.", status_hint=400
            )
        if declared_length == 0:
            return b""
        if declared_length > settings.MAX_REQUEST_SIZE:
            raise ParseError(
                f"Content-Length {declared_length} exceeds server limit "
                f"{settings.MAX_REQUEST_SIZE}.",
                status_hint=413
            )
        return body_bytes[:declared_length]


# Module-level helper  
def _parse_query_pairs(qs: str) -> Dict[str, str]:
    params: Dict[str, str] = {}
    if not qs:
        return params

    for pair in qs.split("&"):
        if not pair:
            continue
        if "=" in pair:
            k, _, v = pair.partition("=")
        else:
            k, v = pair, ""

        try:
            key = unquote_plus(k, encoding="utf-8")
            val = unquote_plus(v, encoding="utf-8")
        except Exception:
            key, val = k, v

        params[key] = val
    return params
