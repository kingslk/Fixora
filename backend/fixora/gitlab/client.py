from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlparse

import httpx


class GitLabError(RuntimeError):
    pass


class GitLabClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        ssl_verify: bool = False,
        ca_bundle: str | None = None,
        timeout: float = 30,
    ) -> None:
        root = base_url.rstrip("/")
        self.api_base = root if root.endswith("/api/v4") else f"{root}/api/v4"
        self.token = token
        self.client = httpx.Client(
            base_url=self.api_base,
            headers={"PRIVATE-TOKEN": token},
            # ssl_verify=False 时 verify 为 False；给了 ca_bundle 才走自定义 CA。
            verify=ca_bundle if ssl_verify and ca_bundle else ssl_verify,
            timeout=timeout,
        )

    def close(self) -> None:
        self.client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.client.request(method, path, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise GitLabError(f"GitLab {exc.response.status_code}: {detail}") from exc
        except httpx.HTTPError as exc:
            raise GitLabError(f"GitLab 连接失败: {exc}") from exc
        if response.status_code == 204:
            return None
        return response.json()

    def current_user(self) -> dict[str, Any]:
        return self._request("GET", "/user")

    def list_projects(self, search: str = "") -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "membership": True,
            "simple": True,
            "per_page": 100,
            "order_by": "last_activity_at",
        }
        if search:
            params["search"] = search
        return self._request("GET", "/projects", params=params)

    def get_project(self, project_id: int | str) -> dict[str, Any]:
        return self._request("GET", f"/projects/{quote(str(project_id), safe='')}")

    def get_branch_sha(self, project_id: int, branch: str) -> str:
        data = self._request(
            "GET", f"/projects/{project_id}/repository/branches/{quote(branch, safe='')}"
        )
        return str(data["commit"]["id"])

    def create_branch(self, project_id: int, branch: str, ref: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/projects/{project_id}/repository/branches",
            data={"branch": branch, "ref": ref},
        )

    def create_commit(
        self,
        project_id: int,
        *,
        branch: str,
        message: str,
        actions: list[dict[str, Any]],
        start_sha: str | None = None,
        start_branch: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "branch": branch,
            "commit_message": message,
            "actions": actions,
        }
        if start_sha:
            payload["start_sha"] = start_sha
        if start_branch:
            payload["start_branch"] = start_branch
        return self._request(
            "POST",
            f"/projects/{project_id}/repository/commits",
            json=payload,
        )

    def create_commit_comment(self, project_id: int, sha: str, note: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/projects/{project_id}/repository/commits/{quote(sha, safe='')}/comments",
            json={"note": note},
        )


def project_path_from_url(value: str, gitlab_base_url: str) -> str | None:
    candidate = urlparse(value.strip())
    expected = urlparse(gitlab_base_url.strip())
    if candidate.scheme not in {"http", "https"} or not candidate.hostname:
        return None
    if candidate.hostname.lower() != (expected.hostname or "").lower():
        return None
    path = candidate.path.strip("/").removesuffix(".git")
    return path or None
