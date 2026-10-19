"""Public GitHub access separated from model inference. No repository code execution."""

import base64
import hashlib
import json
import re
import secrets
import time
from pathlib import PurePosixPath
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select

from .db import ApiKey, Connection, Job, OAuthState
from .errors import ServiceError
from .extractors import chunks, extract_blocks, metadata
from .service import identifier


class GitHubClient:
    def __init__(self, token, client=None):
        self.client = client or httpx.Client(
            base_url="https://api.github.com", timeout=25, follow_redirects=False, trust_env=False
        )
        self.headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get(self, path, params=None):
        if (
            not path.startswith("/")
            or path.startswith("//")
            or ":" in path
            or ".." in path.split("/")
            or "?" in path
            or "#" in path
        ):
            raise ServiceError("invalid_github_path")
        try:
            with self.client.stream("GET", path, params=params, headers=self.headers) as r:
                if r.status_code == 401:
                    raise ServiceError("github_revoked", 403)
                if r.status_code == 429 or (
                    r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0"
                ):
                    try:
                        delay = int(r.headers.get("retry-after", "0")) or max(
                            1, int(r.headers.get("x-ratelimit-reset", "0")) - int(time.time())
                        )
                    except ValueError:
                        delay = 60
                    raise ServiceError("github_rate_limited", 429, True, min(delay, 86400))
                if r.status_code == 403:
                    raise ServiceError("github_forbidden", 403)
                if r.status_code in {404, 409}:
                    raise ServiceError("github_resource_unavailable", 404)
                if r.status_code != 200:
                    raise ServiceError("github_unavailable", 503, True)
                raw, size = [], 0
                for piece in r.iter_bytes():
                    size += len(piece)
                    if size > 8 * 1024 * 1024:
                        raise ServiceError("github_response_limit")
                    raw.append(piece)
                return json.loads(b"".join(raw))
        except ServiceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceError("github_unavailable", 503, True) from exc

    def inventory(self, page=1):
        if not 1 <= page <= 100:
            raise ServiceError("invalid_inventory_page")
        repos = self.get("/user/repos", {"per_page": 100, "page": page, "visibility": "public"})
        if not isinstance(repos, list):
            raise ServiceError("invalid_github_response")
        return repos


class GitHubConnections:
    def __init__(self, service):
        self.service = service
        self.settings = service.settings
        self.cipher = Fernet(self.settings.token_encryption_key.encode())

    def authorize(self, principal, subject_ref=None):
        s = self.settings
        if (
            not s.github_client_id
            or not s.github_client_secret
            or not s.github_redirect_uri.startswith("https://")
        ):
            raise ServiceError("github_oauth_not_configured", 503)
        state = secrets.token_urlsafe(32)
        with self.service.db.transaction() as db:
            db.add(
                OAuthState(
                    digest=hashlib.sha256(state.encode()).hexdigest(),
                    tenant=principal.tenant,
                    key_id=principal.key_id,
                    subject_ref=subject_ref,
                    expires=time.time() + 600,
                )
            )
        return {
            "authorization_url": "https://github.com/login/oauth/authorize?"
            + urlencode(
                {
                    "client_id": s.github_client_id,
                    "redirect_uri": s.github_redirect_uri,
                    "state": state,
                    "scope": "",
                }
            ),
            "expires_in": 600,
        }

    def callback(self, code, state):
        if not code or len(code) > 512 or not state or len(state) > 256:
            raise ServiceError("invalid_oauth_state", 400)
        with self.service.db.transaction() as db:
            row = db.get(OAuthState, hashlib.sha256(state.encode()).hexdigest())
            if not row or row.expires <= time.time():
                raise ServiceError("invalid_oauth_state", 400)
            tenant, key_id, subject = row.tenant, row.key_id, row.subject_ref
            db.delete(row)  # Commit consumption before any network exchange.
        try:
            with httpx.Client(timeout=25, follow_redirects=False, trust_env=False) as client:
                response = client.post(
                    "https://github.com/login/oauth/access_token",
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": self.settings.github_client_id,
                        "client_secret": self.settings.github_client_secret,
                        "code": code,
                        "redirect_uri": self.settings.github_redirect_uri,
                    },
                )
                if response.status_code != 200:
                    raise ServiceError("github_oauth_exchange_failed", 400)
                token_data = response.json()
                token = token_data.get("access_token")
                if not isinstance(token, str) or not token:
                    raise ServiceError("github_oauth_exchange_failed", 400)
            gh = GitHubClient(token)
            try:
                identity = gh.get("/user")
            finally:
                gh.client.close()
        except ServiceError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceError("github_oauth_exchange_failed", 400) from exc
        with self.service.db.transaction() as db:
            key = db.get(ApiKey, key_id)
            if not key or key.revoked or key.tenant != tenant:
                raise ServiceError("invalid_credentials", 401)
            row = Connection(
                id=identifier("conn"),
                tenant=tenant,
                key_id=key_id,
                github_id=int(identity["id"]),
                token_ciphertext=self.cipher.encrypt(token.encode()).decode(),
                scopes=str(token_data.get("scope", ""))[:1000],
                subject_ref=subject,
            )
            db.add(row)
        return {"connection_id": row.id, "public_repositories_only": True}

    def client(self, tenant, connection_id):
        with self.service.db.sessions() as db:
            row = db.get(Connection, connection_id)
            if not row or row.tenant != tenant:
                raise ServiceError("resource_not_found", 404)
            key = db.get(ApiKey, row.key_id)
            if not key or key.revoked:
                raise ServiceError("github_revoked", 403)
            try:
                token = self.cipher.decrypt(row.token_ciphertext.encode()).decode()
            except Exception as exc:
                raise ServiceError("github_reconnect_required", 403) from exc
            return GitHubClient(token), row.github_id

    def disconnect(self, principal, connection_id):
        with self.service.db.transaction() as db:
            row = db.get(Connection, connection_id)
            if not row or row.tenant != principal.tenant:
                raise ServiceError("resource_not_found", 404)
            db.delete(row)
            for job in db.scalars(select(Job).where(Job.tenant == principal.tenant)):
                if any(
                    source.get("connection_id") == connection_id for source in job.payload.get("sources", [])
                ):
                    self.service.mark_deleting(job)
        return {
            "state": "disconnected",
            "remote_grant_revoked": False,
            "message": "Local token erased. Revoke the OAuth app in GitHub settings to revoke its grant.",
        }


def eligible(path):
    p = PurePosixPath(path)
    if (
        p.is_absolute()
        or ".." in p.parts
        or any(
            part.lower() in {".git", ".venv", "node_modules", "vendor", "dist", "build", "coverage"}
            for part in p.parts
        )
        or p.name.lower().startswith(".env")
        or p.suffix.lower() in {".pem", ".key", ".lock"}
        or p.name.lower() in {"package-lock.json", "yarn.lock", "poetry.lock", "uv.lock"}
        or any(word in p.name.lower() for word in (".min.", ".generated.", "credentials", "secrets"))
    ):
        return False
    return p.suffix.lower() in {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".md",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".sql",
        ".php",
        ".rb",
        ".cs",
        ".kt",
        ".swift",
    } or p.name in {"Dockerfile", "requirements.txt", "go.mod"}


def redact(text):
    text = re.sub(
        r"(?i)(?:gh[pousr]_[A-Za-z0-9_]{10,}|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{15,})",
        "[REDACTED]",
        text,
    )
    return re.sub(r"(?im)((?:password|api[_-]?key|secret|token)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", text)


def analyze_repositories(gh, expected_id, source, model, check=lambda: None):
    check()
    identity = gh.get("/user")
    if identity.get("id") != expected_id:
        raise ServiceError("github_identity_changed", 403)
    wanted = set(source["repository_ids"])
    found = {}
    for page in range(1, 101):
        check()
        batch = gh.inventory(page)
        found.update({r["id"]: r for r in batch if r.get("id") in wanted and not r.get("private", True)})
        if wanted <= found.keys() or len(batch) < 100:
            break
    if wanted - found.keys():
        raise ServiceError("invalid_repository_selection", 422)
    profile_keys = {
        "id",
        "login",
        "name",
        "bio",
        "html_url",
        "avatar_url",
        "public_repos",
        "public_gists",
        "followers",
        "following",
        "created_at",
        "updated_at",
        "company",
        "blog",
        "location",
        "hireable",
        "twitter_username",
    }
    profile = {k: identity.get(k) for k in sorted(profile_keys)}
    repositories, errors, evidence, fields = [], [], {}, {}
    for rid in source["repository_ids"]:
        check()
        item = found[rid]
        keys = {
            "id",
            "name",
            "full_name",
            "description",
            "html_url",
            "private",
            "fork",
            "language",
            "topics",
            "default_branch",
            "created_at",
            "updated_at",
            "pushed_at",
            "stargazers_count",
            "forks_count",
            "open_issues_count",
            "size",
            "archived",
            "disabled",
            "homepage",
            "license",
            "owner",
        }
        row = {k: item.get(k) for k in sorted(keys)}
        # Owner metadata is deliberately projected to avoid copying arbitrary API fields.
        row["owner"] = {k: (item.get("owner") or {}).get(k) for k in ("id", "login", "html_url")}
        row["coverage"] = {"metadata_observed_at": time.time(), "sampled_paths": [], "tree_truncated": False}
        row["warnings"], row["errors"] = [], []
        idx = len(repositories)
        try:
            name = item["full_name"]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name):
                raise ServiceError("invalid_github_response")
            prefix = "/repos/" + name
            check()
            row["languages"] = gh.get(prefix + "/languages")
            commits = gh.get(prefix + "/commits", {"per_page": 10})
            sha = commits[0]["sha"]
            if not re.fullmatch(r"[a-f0-9]{40,64}", sha):
                raise ServiceError("invalid_github_response")
            row["sampled_contributions"] = [
                {"sha": c.get("sha"), "author_login": (c.get("author") or {}).get("login")}
                for c in commits[:10]
            ]
            row["coverage"]["commit_sha"] = sha
            tree = gh.get(prefix + "/git/trees/" + sha, {"recursive": "1"})
            paths = [
                r
                for r in tree.get("tree", [])
                if r.get("type") == "blob" and r.get("size", 100001) <= 100000 and eligible(r.get("path", ""))
            ]
            paths.sort(
                key=lambda r: (
                    0
                    if PurePosixPath(r["path"]).name.lower().startswith("readme")
                    else 1
                    if PurePosixPath(r["path"]).name
                    in {"requirements.txt", "package.json", "pyproject.toml", "go.mod"}
                    else 2,
                    r["path"],
                )
            )
            row["coverage"].update(
                eligible_paths_count=len(paths),
                tree_truncated=bool(tree.get("truncated")) or len(paths) > 5000,
            )
            blocks, total = [], 0
            for entry in paths[:5000][:10]:
                check()
                blob_sha = entry.get("sha", "")
                if not re.fullmatch(r"[a-f0-9]{40,64}", blob_sha):
                    raise ServiceError("invalid_github_response")
                blob = gh.get(prefix + "/git/blobs/" + blob_sha)
                if blob.get("encoding") != "base64" or blob.get("size", 100001) > 100000:
                    continue
                try:
                    raw = base64.b64decode(blob["content"], validate=False)
                    if len(raw) > 100000:
                        continue
                    text = redact(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError, KeyError):
                    row["warnings"].append({"code": "unreadable_source_file", "path": entry["path"]})
                    continue
                clip = text[: min(3500, 18000 - total)]
                if not clip:
                    break
                total += len(clip)
                row["coverage"]["sampled_paths"].append(entry["path"])
                if len(clip) < len(text):
                    row["warnings"].append({"code": "source_sample_truncated", "path": entry["path"]})
                for offset, part in chunks(clip):
                    blocks.append(
                        {
                            "id": f"repo_{rid}_{len(blocks)}",
                            "input_id": f"repo_{rid}",
                            "text": part,
                            "origin": "github_source",
                            "locator": {
                                "repository_id": rid,
                                "commit_sha": sha,
                                "path": entry["path"],
                                "char_start": offset,
                                "line_start": clip[:offset].count("\n") + 1,
                            },
                        }
                    )
            row["coverage"]["source_characters"] = total
            if blocks:
                analysis = extract_blocks("github", blocks, model, check)
                row["analysis"] = analysis["data"]
                row["errors"].extend(analysis["errors"])
                row["warnings"].extend(analysis["warnings"])
                evidence.update(analysis["evidence"])
                fields.update({f"/repositories/{idx}/analysis" + k: v for k, v in analysis["fields"].items()})
            else:
                row["analysis"] = None
                row["warnings"].append({"code": "no_eligible_source_samples"})
        except ServiceError as exc:
            if exc.code in {"job_cancelled", "job_deadline_exceeded"}:
                raise
            row["errors"].append(exc.payload())
        row["warnings"].append({"code": "sampled_source_not_authorship_or_proficiency_proof"})
        errors.extend(row["errors"])
        repositories.append(row)
    data = {"profile": profile, "repositories": repositories}

    # API facts have JSON-pointer evidence, not a fabricated quotation.
    def api_fields(value, pointer):
        if pointer in fields or "/analysis/" in pointer:
            return
        eid = "api_" + hashlib.sha256((source["id"] + pointer).encode()).hexdigest()[:24]
        evidence[eid] = {
            "id": eid,
            "input_id": source["connection_id"],
            "source_kind": "github_api",
            "locator": {"json_pointer": pointer},
            "observed_at": time.time(),
            "content_sha256": hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest(),
        }
        fields[pointer] = metadata("extracted" if value is not None else "not_found", [eid], "github_api")
        if isinstance(value, dict):
            for k, v in value.items():
                if k not in {"analysis", "warnings", "errors", "coverage"}:
                    api_fields(v, pointer + "/" + k)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                api_fields(v, pointer + "/" + str(i))

    for key, value in data.items():
        api_fields(value, "/" + key)
    return {
        "type": "github",
        "state": "partial" if errors else "succeeded",
        "data": data,
        "fields": fields,
        "evidence": evidence,
        "inputs": [f"repo_{i}" for i in source["repository_ids"]],
        "coverage": {"repositories_selected": len(wanted), "repositories_examined": len(repositories)},
        "warnings": [{"code": "public_repositories_only"}],
        "errors": errors,
    }
