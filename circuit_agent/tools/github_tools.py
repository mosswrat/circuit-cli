"""
GitHub API Tools for Circuit Agent.

Provides direct integration with GitHub's REST API for repository,
issue, PR, and other GitHub operations.
"""

from typing import Any, Dict, Optional

import httpx

from ..config import load_github_pat, ssl_config


class GitHubTools:
    """GitHub API tools using REST API directly."""

    API_BASE = "https://api.github.com"

    def __init__(self):
        self._pat: Optional[str] = None

    def _get_pat(self) -> Optional[str]:
        """Get GitHub PAT, loading if needed."""
        if not self._pat:
            self._pat = load_github_pat()
        return self._pat

    def _get_headers(self) -> Dict[str, str]:
        """Get API request headers."""
        pat = self._get_pat()
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if pat:
            headers["Authorization"] = f"Bearer {pat}"
        return headers

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Make an API request to GitHub."""
        pat = self._get_pat()
        if not pat:
            return {"error": "GitHub PAT not configured. Set it in Settings → MCP Servers."}

        url = f"{self.API_BASE}{endpoint}"

        try:
            with httpx.Client(verify=ssl_config.get_verify_param(), timeout=30.0) as client:
                response = client.request(
                    method=method,
                    url=url,
                    headers=self._get_headers(),
                    params=params,
                    json=json_data,
                )

                if response.status_code == 401:
                    return {"error": "GitHub authentication failed. Check your PAT."}
                elif response.status_code == 403:
                    return {"error": "GitHub access forbidden. Check PAT permissions."}
                elif response.status_code == 404:
                    return {"error": "Resource not found on GitHub."}
                elif response.status_code >= 400:
                    return {
                        "error": f"GitHub API error: {response.status_code} - {response.text[:200]}"
                    }

                if response.status_code == 204:
                    return {"success": True}

                return response.json()

        except httpx.TimeoutException:
            return {"error": "GitHub API request timed out."}
        except Exception as e:
            return {"error": f"GitHub API error: {str(e)[:100]}"}

    # =========================================================================
    # User / Auth
    # =========================================================================

    def get_authenticated_user(self, arguments: dict, confirmed: bool) -> str:
        """Get the authenticated user's info."""
        result = self._request("GET", "/user")
        if "error" in result:
            return result["error"]
        return f"Authenticated as @{result.get('login')} ({result.get('name', 'No name')})"

    # =========================================================================
    # Repositories
    # =========================================================================

    def list_repos(self, arguments: dict, confirmed: bool) -> str:
        """List repositories for the authenticated user or a specified user/org."""
        owner = arguments.get("owner")
        repo_type = arguments.get("type", "all")  # all, owner, public, private, member
        sort = arguments.get("sort", "updated")  # created, updated, pushed, full_name
        per_page = min(arguments.get("per_page", 10), 30)

        if owner:
            # List repos for specific user/org
            endpoint = f"/users/{owner}/repos"
        else:
            # List repos for authenticated user
            endpoint = "/user/repos"

        result = self._request(
            "GET",
            endpoint,
            params={
                "type": repo_type,
                "sort": sort,
                "per_page": per_page,
            },
        )

        # Handle error response (dict with "error" key)
        if isinstance(result, dict) and "error" in result:
            return result["error"]

        # Handle empty or non-list response
        if not result or not isinstance(result, list):
            return "No repositories found."

        repos = []
        for repo in result[:per_page]:
            visibility = "🔒" if repo.get("private") else "🌐"
            stars = repo.get("stargazers_count", 0)
            desc = repo.get("description") or "No description"
            repos.append(f"{visibility} {repo['full_name']} ⭐{stars} - {desc[:60]}")

        return f"Found {len(repos)} repositories:\n" + "\n".join(repos)

    def get_repo(self, arguments: dict, confirmed: bool) -> str:
        """Get details about a specific repository."""
        owner = arguments.get("owner")
        repo = arguments.get("repo")

        if not owner or not repo:
            return "Error: owner and repo are required"

        result = self._request("GET", f"/repos/{owner}/{repo}")

        if "error" in result:
            return result["error"]

        info = [
            f"**{result['full_name']}**",
            f"Description: {result.get('description', 'None')}",
            f"Stars: {result.get('stargazers_count', 0)} | Forks: {result.get('forks_count', 0)}",
            f"Language: {result.get('language', 'Unknown')}",
            f"Default branch: {result.get('default_branch', 'main')}",
            f"Visibility: {'Private' if result.get('private') else 'Public'}",
            f"URL: {result.get('html_url')}",
        ]
        return "\n".join(info)

    def create_repo(self, arguments: dict, confirmed: bool) -> str:
        """Create a new repository."""
        if not confirmed:
            return "NEEDS_CONFIRMATION:github_create_repo"

        name = arguments.get("name")
        if not name:
            return "Error: name is required"

        data = {
            "name": name,
            "description": arguments.get("description", ""),
            "private": arguments.get("private", False),
            "auto_init": arguments.get("auto_init", True),
        }

        result = self._request("POST", "/user/repos", json_data=data)

        if "error" in result:
            return result["error"]

        return f"Repository created: {result.get('html_url')}"

    # =========================================================================
    # Issues
    # =========================================================================

    def list_issues(self, arguments: dict, confirmed: bool) -> str:
        """List issues for a repository."""
        owner = arguments.get("owner")
        repo = arguments.get("repo")
        state = arguments.get("state", "open")  # open, closed, all
        per_page = min(arguments.get("per_page", 10), 30)

        if not owner or not repo:
            return "Error: owner and repo are required"

        result = self._request(
            "GET",
            f"/repos/{owner}/{repo}/issues",
            params={
                "state": state,
                "per_page": per_page,
            },
        )

        # Handle error response
        if isinstance(result, dict) and "error" in result:
            return result["error"]

        # Handle empty or non-list response
        if not result or not isinstance(result, list):
            return f"No {state} issues found."

        issues = []
        for issue in result[:per_page]:
            # Skip pull requests (they show up in issues API)
            if "pull_request" in issue:
                continue
            labels = ", ".join([lbl["name"] for lbl in issue.get("labels", [])])
            labels_str = f" [{labels}]" if labels else ""
            issues.append(f"#{issue['number']} {issue['title']}{labels_str}")

        if not issues:
            return f"No {state} issues found (only PRs)."

        return f"Found {len(issues)} {state} issues:\n" + "\n".join(issues)

    def get_issue(self, arguments: dict, confirmed: bool) -> str:
        """Get details about a specific issue."""
        owner = arguments.get("owner")
        repo = arguments.get("repo")
        issue_number = arguments.get("issue_number")

        if not all([owner, repo, issue_number]):
            return "Error: owner, repo, and issue_number are required"

        result = self._request("GET", f"/repos/{owner}/{repo}/issues/{issue_number}")

        if "error" in result:
            return result["error"]

        labels = ", ".join([lbl["name"] for lbl in result.get("labels", [])])
        info = [
            f"**#{result['number']}: {result['title']}**",
            f"State: {result['state']}",
            f"Author: @{result['user']['login']}",
            f"Labels: {labels or 'None'}",
            f"Created: {result['created_at']}",
            "",
            result.get("body", "No description")[:500],
        ]
        return "\n".join(info)

    def create_issue(self, arguments: dict, confirmed: bool) -> str:
        """Create a new issue."""
        if not confirmed:
            return "NEEDS_CONFIRMATION:github_create_issue"

        owner = arguments.get("owner")
        repo = arguments.get("repo")
        title = arguments.get("title")

        if not all([owner, repo, title]):
            return "Error: owner, repo, and title are required"

        data = {
            "title": title,
            "body": arguments.get("body", ""),
        }
        if arguments.get("labels"):
            data["labels"] = arguments["labels"]
        if arguments.get("assignees"):
            data["assignees"] = arguments["assignees"]

        result = self._request("POST", f"/repos/{owner}/{repo}/issues", json_data=data)

        if "error" in result:
            return result["error"]

        return f"Issue created: {result.get('html_url')}"

    def close_issue(self, arguments: dict, confirmed: bool) -> str:
        """Close an issue."""
        if not confirmed:
            return "NEEDS_CONFIRMATION:github_close_issue"

        owner = arguments.get("owner")
        repo = arguments.get("repo")
        issue_number = arguments.get("issue_number")

        if not all([owner, repo, issue_number]):
            return "Error: owner, repo, and issue_number are required"

        result = self._request(
            "PATCH", f"/repos/{owner}/{repo}/issues/{issue_number}", json_data={"state": "closed"}
        )

        if "error" in result:
            return result["error"]

        return f"Issue #{issue_number} closed."

    # =========================================================================
    # Pull Requests
    # =========================================================================

    def list_pull_requests(self, arguments: dict, confirmed: bool) -> str:
        """List pull requests for a repository."""
        owner = arguments.get("owner")
        repo = arguments.get("repo")
        state = arguments.get("state", "open")  # open, closed, all
        per_page = min(arguments.get("per_page", 10), 30)

        if not owner or not repo:
            return "Error: owner and repo are required"

        result = self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            params={
                "state": state,
                "per_page": per_page,
            },
        )

        # Handle error response
        if isinstance(result, dict) and "error" in result:
            return result["error"]

        # Handle empty or non-list response
        if not result or not isinstance(result, list):
            return f"No {state} pull requests found."

        prs = []
        for pr in result[:per_page]:
            status = "✅" if pr.get("merged") else ("🔴" if pr["state"] == "closed" else "🟢")
            prs.append(f"{status} #{pr['number']} {pr['title']} (@{pr['user']['login']})")

        return f"Found {len(prs)} {state} pull requests:\n" + "\n".join(prs)

    def get_pull_request(self, arguments: dict, confirmed: bool) -> str:
        """Get details about a specific pull request."""
        owner = arguments.get("owner")
        repo = arguments.get("repo")
        pr_number = arguments.get("pr_number")

        if not all([owner, repo, pr_number]):
            return "Error: owner, repo, and pr_number are required"

        result = self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}")

        if "error" in result:
            return result["error"]

        info = [
            f"**#{result['number']}: {result['title']}**",
            f"State: {result['state']} {'(merged)' if result.get('merged') else ''}",
            f"Author: @{result['user']['login']}",
            f"Branch: {result['head']['ref']} → {result['base']['ref']}",
            f"Commits: {result.get('commits', 0)} | Changed files: {result.get('changed_files', 0)}",
            f"+{result.get('additions', 0)} -{result.get('deletions', 0)}",
            "",
            result.get("body", "No description")[:500],
        ]
        return "\n".join(info)

    # =========================================================================
    # Actions / Workflows
    # =========================================================================

    def list_workflow_runs(self, arguments: dict, confirmed: bool) -> str:
        """List recent workflow runs for a repository."""
        owner = arguments.get("owner")
        repo = arguments.get("repo")
        status = arguments.get("status")  # queued, in_progress, completed
        per_page = min(arguments.get("per_page", 10), 30)

        if not owner or not repo:
            return "Error: owner and repo are required"

        params = {"per_page": per_page}
        if status:
            params["status"] = status

        result = self._request("GET", f"/repos/{owner}/{repo}/actions/runs", params=params)

        # Handle error or unexpected response
        if not isinstance(result, dict):
            return "Failed to fetch workflow runs."
        if "error" in result:
            return result["error"]

        runs = result.get("workflow_runs", [])
        if not runs:
            return "No workflow runs found."

        output = []
        for run in runs[:per_page]:
            status_icon = {
                "completed": "✅" if run.get("conclusion") == "success" else "❌",
                "in_progress": "🔄",
                "queued": "⏳",
            }.get(run["status"], "❓")
            output.append(
                f"{status_icon} {run['name']} #{run['run_number']} - {run['status']} "
                f"({run.get('conclusion', 'running')}) on {run['head_branch']}"
            )

        return f"Found {len(output)} workflow runs:\n" + "\n".join(output)

    # =========================================================================
    # Search
    # =========================================================================

    def search_repos(self, arguments: dict, confirmed: bool) -> str:
        """Search for repositories."""
        query = arguments.get("query")
        per_page = min(arguments.get("per_page", 10), 30)

        if not query:
            return "Error: query is required"

        result = self._request(
            "GET",
            "/search/repositories",
            params={
                "q": query,
                "per_page": per_page,
            },
        )

        # Handle error or unexpected response
        if not isinstance(result, dict):
            return "Failed to search repositories."
        if "error" in result:
            return result["error"]

        items = result.get("items", [])
        if not items:
            return "No repositories found matching your search."

        repos = []
        for repo in items[:per_page]:
            stars = repo.get("stargazers_count", 0)
            repos.append(
                f"⭐{stars} {repo['full_name']} - {repo.get('description', 'No description')[:50]}"
            )

        return f"Found {result.get('total_count', 0)} repositories:\n" + "\n".join(repos)

    def search_issues(self, arguments: dict, confirmed: bool) -> str:
        """Search for issues and pull requests."""
        query = arguments.get("query")
        per_page = min(arguments.get("per_page", 10), 30)

        if not query:
            return "Error: query is required"

        result = self._request(
            "GET",
            "/search/issues",
            params={
                "q": query,
                "per_page": per_page,
            },
        )

        # Handle error or unexpected response
        if not isinstance(result, dict):
            return "Failed to search issues."
        if "error" in result:
            return result["error"]

        items = result.get("items", [])
        if not items:
            return "No issues found matching your search."

        issues = []
        for issue in items[:per_page]:
            type_icon = "🔀" if "pull_request" in issue else "🐛"
            issues.append(
                f"{type_icon} {issue['repository_url'].split('/')[-2]}/{issue['repository_url'].split('/')[-1]}#{issue['number']} {issue['title'][:50]}"
            )

        return f"Found {result.get('total_count', 0)} results:\n" + "\n".join(issues)


# Tool definitions for OpenAI function calling format
GITHUB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "github_whoami",
            "description": "Get information about the authenticated GitHub user",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_repos",
            "description": "List repositories for the authenticated user or a specified owner",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {
                        "type": "string",
                        "description": "Username or organization (optional, defaults to authenticated user)",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["all", "owner", "public", "private", "member"],
                        "description": "Type of repos to list",
                    },
                    "sort": {
                        "type": "string",
                        "enum": ["created", "updated", "pushed", "full_name"],
                        "description": "Sort order",
                    },
                    "per_page": {"type": "integer", "description": "Number of results (max 30)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_get_repo",
            "description": "Get details about a specific repository",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                },
                "required": ["owner", "repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_create_repo",
            "description": "Create a new repository",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Repository name"},
                    "description": {"type": "string", "description": "Repository description"},
                    "private": {"type": "boolean", "description": "Whether the repo is private"},
                    "auto_init": {"type": "boolean", "description": "Initialize with README"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_issues",
            "description": "List issues for a repository",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "state": {
                        "type": "string",
                        "enum": ["open", "closed", "all"],
                        "description": "Issue state filter",
                    },
                    "per_page": {"type": "integer", "description": "Number of results (max 30)"},
                },
                "required": ["owner", "repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_get_issue",
            "description": "Get details about a specific issue",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "issue_number": {"type": "integer", "description": "Issue number"},
                },
                "required": ["owner", "repo", "issue_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_create_issue",
            "description": "Create a new issue in a repository",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "title": {"type": "string", "description": "Issue title"},
                    "body": {"type": "string", "description": "Issue body/description"},
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Labels to add",
                    },
                    "assignees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Users to assign",
                    },
                },
                "required": ["owner", "repo", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_close_issue",
            "description": "Close an issue",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "issue_number": {"type": "integer", "description": "Issue number"},
                },
                "required": ["owner", "repo", "issue_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_prs",
            "description": "List pull requests for a repository",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "state": {
                        "type": "string",
                        "enum": ["open", "closed", "all"],
                        "description": "PR state filter",
                    },
                    "per_page": {"type": "integer", "description": "Number of results (max 30)"},
                },
                "required": ["owner", "repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_get_pr",
            "description": "Get details about a specific pull request",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "pr_number": {"type": "integer", "description": "Pull request number"},
                },
                "required": ["owner", "repo", "pr_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_list_workflows",
            "description": "List recent GitHub Actions workflow runs",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "description": "Repository owner"},
                    "repo": {"type": "string", "description": "Repository name"},
                    "status": {
                        "type": "string",
                        "enum": ["queued", "in_progress", "completed"],
                        "description": "Filter by status",
                    },
                    "per_page": {"type": "integer", "description": "Number of results (max 30)"},
                },
                "required": ["owner", "repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_search_repos",
            "description": "Search for repositories on GitHub",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "per_page": {"type": "integer", "description": "Number of results (max 30)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_search_issues",
            "description": "Search for issues and pull requests on GitHub",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (can include qualifiers like repo:owner/name, is:issue, is:pr)",
                    },
                    "per_page": {"type": "integer", "description": "Number of results (max 30)"},
                },
                "required": ["query"],
            },
        },
    },
]
