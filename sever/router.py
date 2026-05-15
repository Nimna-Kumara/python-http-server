import re
from typing import Callable, Dict, Tuple, Optional, List


# Custom exceptions 
class RouterError(Exception):
    """Base exception for router errors"""
    pass


class NotFoundError(RouterError):
    """Raised when no route matches the request path."""
    def __init__(self, path: str):
        self.path = path
        super().__init__(f"404 Not Found: {path!r}")


class MethodNotAllowedError(RouterError):
    """Raised when the path matches but the HTTP method does not."""
    def __init__(self, method: str, path: str, allowed: List[str]):
        self.method = method
        self.path = path
        self.allowed = allowed
        super().__init__(
            f"405 Method Not Allowed: {method} {path!r}"
            f"allowed: {', '.join(allowed)}"
        )


# Route record
class Route:

    _PARAM_RE = re.compile(r"\{(\w+)\}")
    # r"..."  -> raw string
    # \{ -> '{', \} -> '}'
    # \w -> any word character [a-zA-Z0-9_]
    # + -> one or more
    # means: Find text like {something} and capture the "something".

    def __init__(self, method: str, path: str, handler: Callable):
        self.method = method.upper()
        self.raw_path = path
        self.handler: Callable = handler
        self.param_names: List[str] = []
        self.pattern: re.Pattern = self._compile(path)

    def _compile(self, path: str) -> re.Pattern:

        parts = self._PARAM_RE.split(path)
        regex_parts = []
        for i, part in enumerate(parts):
            if i % 2 == 0:
                regex_parts.append(re.escape(part))
            else:
                self.param_names.append(part)
                regex_parts.append(f"(?P<{part}>[^/]+)")
        
        pattern = "^" + "".join(regex_parts) + "$"
        return re.compile(pattern)

    def match(self, path: str) -> Optional[Dict[str, str]]:
        m = self.pattern.match(path)
        if m is None:
            return None
        return m.groupdict()

    def __repr__(self) -> str: 
        return f"<Route {self.method} {self.raw_path!r}>"



# Router
class Router:
    
    def __init__(self):
        self._routes: List[Route] = []

    def add_route(self, method: str, path: str, handler: Callable) -> Router:
        method = method.upper()
        route = Route(method, path, handler)
        self._routes.append(route)
        return self

    def _decorator(self, method: str, path: str) -> Callable:
        def decorator(fn: Callable) -> Callable:
            self.add_route(method, path, fn)
            return fn
        return decorator

    def get(self, path: str) -> Callable:
        return self._decorator('GET', path)

    def post(self, path: str) -> Callable:
        return self._decorator('POST', path)

    def put(self, path: str) -> Callable:
        return self._decorator('PUT', path)

    def delete(self, path: str) -> Callable:
        return self._decorator('DELETE', path)

    def patch(self, path: str) -> Callable:
        return self._decorator('PATCH', path)

    def head(self, path: str) -> Callable:
        return self._decorator('HEAD', path)

    def options(self, path: str) -> Callable:
        return self._decorator('OPTIONS', path) 

    def route(self, request) -> Tuple[Callable, Dict[str, str]]:
        
        method = request.method.upper()
        path = self._normalize_path(request.path) 
        
        path_matched_methods: List[str] = []

        for route in self._routes:
            path_params = route.match(path)
            if path_params is None:
                continue

            if route.method == method or route.method == "ANY":
                return route.handler, path_params

            path_matched_methods.append(route.method)

        if path_matched_methods:
            if "GET" in path_matched_methods and "HEAD" not in path_matched_methods:
                path_matched_methods.append("HEAD")
            if "OPTIONS" not in path_matched_methods:
                path_matched_methods.append("OPTIONS")
            raise MethodNotAllowedError(method, path, sorted(path_matched_methods)) 
        
        raise NotFoundError(path)

    def handle(self, request, builder):
        if request.method.upper() == "OPTIONS":
            allowed = self._allowed_methods_for(request.path)
            response = builder.ok(b"")
            response.headers["Allow"] = ", ".join(sorted(allowed))
            response.headers["Content-Length"] = "0"
            return response

        handler, path_params = self.route(request)
        request.path_params = path_params

        if request.method.upper() == "HEAD":
            response = handler(request, builder)
            response.body = b""
            return response

        return handler(request, builder)

    def _allowed_methods_for(self, path: str) -> List[str]:
        normalized = self._normalize_path(path)
        methods = []
        for route in self._routes:
            if route.match(normalized) is not None:
                methods.append(route.method)
        if "GET" in methods and "HEAD" not in methods:
            methods.append("HEAD")
        methods.append("OPTIONS")
        return list(set(methods))

    def routes(self) -> List[Route]:
        return list(self._routes)

    def url_for(self, handler: Callable, **path_params: str) -> str:
        for route in self._routes:
            if route.handler is handler:
                path = route.raw_path
                for name, value in path_params.items():
                    path = path.replace(f"{{{name}}}", str(value))
                return path
        raise KeyError(f"No route registered for handler {handler!r}")

    @staticmethod
    def _normalize_path(path: str) -> str:
        path = path.split("?")[0]

        while "//" in path:
            path = path.replace("//", "/")

        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        
        return path or "/"

    def __repr__(self) -> str:
        lines = [f"<Router - {len(self.routes)} route(s)>"]
        for r in self._routes:
            lines.append(f" {r.method:<8} {r.raw_path}")
        return "\n".join(lines)

        