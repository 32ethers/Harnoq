import json
import urllib.error
import urllib.request


class AuthenticatedMCP:
    def __init__(self, app, flask_app=None, base_url=None):
        self.app, self.flask_app, self.base_url = app, flask_app, base_url

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            return await self.app(scope, receive, send)
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw = headers.get(b"authorization", b"").decode("latin-1")
        status, message = 401, "Bearer JWT required"
        if raw.lower().startswith("bearer "):
            if self.flask_app is not None:
                response = self.flask_app.test_client().get("/auth/me", headers={"Authorization": raw})
                status = response.status_code
                message = (response.get_json(silent=True) or {}).get("error", "unauthorized")
            else:
                request = urllib.request.Request(self.base_url.rstrip("/") + "/auth/me", headers={"Authorization": raw})
                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        status = response.status
                except urllib.error.HTTPError as error:
                    status = error.code
                except urllib.error.URLError:
                    status, message = 503, "authentication service unavailable"
            if status == 200:
                return await self.app(scope, receive, send)
        body = json.dumps({"error": message}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
