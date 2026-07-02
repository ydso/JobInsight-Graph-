from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any

from .config import Settings


class Neo4jQueryError(RuntimeError):
    pass


class Neo4jQueryClient:
    def __init__(self, settings: Settings):
        settings.require_neo4j_password()
        self._settings = settings

    def run(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload = {
            "statement": statement,
            "parameters": parameters or {},
        }
        response = self._request(payload)

        errors = response.get("errors") or []
        if errors:
            message = "; ".join(error.get("message", str(error)) for error in errors)
            raise Neo4jQueryError(message)

        data = response.get("data") or {}
        fields = data.get("fields") or []
        values = data.get("values") or []
        return [dict(zip(fields, row)) for row in values]

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self._settings.neo4j_query_api_url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=UTF-8",
                "Authorization": self._basic_auth_header(),
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._settings.neo4j_timeout_seconds) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise Neo4jQueryError(f"Neo4j HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise Neo4jQueryError(f"Cannot connect to Neo4j Query API: {exc.reason}") from exc

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise Neo4jQueryError(f"Invalid JSON response from Neo4j: {text[:300]}") from exc

    def _basic_auth_header(self) -> str:
        token = f"{self._settings.neo4j_user}:{self._settings.neo4j_password}"
        encoded = base64.b64encode(token.encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"
