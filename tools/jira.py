"""Jira ticket and attachment tools.

Supports two modes:
- LOCAL: reads from tickets/<TICKET_ID>/ticket.txt and attachment files on disk
- REMOTE: hits the Jira REST API (requires JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN env vars)

Local mode is used automatically when Jira env vars are not set.
"""

from fnmatch import fnmatch
from pathlib import Path

import requests

from mcp_instance import mcp
from config import (
    JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN,
    WORK_DIR, TICKETS_DIR,
)


def _use_remote() -> bool:
    """Return True if Jira API credentials are configured."""
    return bool(JIRA_BASE_URL and JIRA_EMAIL and JIRA_API_TOKEN)


def _jira_get(endpoint: str) -> requests.Response:
    """Make an authenticated GET request to the Jira REST API."""
    url = f"{JIRA_BASE_URL}/rest/api/2/{endpoint}"
    return requests.get(url, auth=(JIRA_EMAIL, JIRA_API_TOKEN), timeout=30)


# ---------------------------------------------------------------------------
# Local mode helpers
# ---------------------------------------------------------------------------

def _local_ticket_dir(ticket_id: str) -> Path | None:
    """Return the local ticket directory, or None if it doesn't exist."""
    ticket_dir = TICKETS_DIR / ticket_id
    if ticket_dir.is_dir():
        return ticket_dir
    return None


def _local_get_ticket(ticket_id: str) -> str:
    """Read a ticket from the local tickets directory."""
    ticket_dir = _local_ticket_dir(ticket_id)
    if not ticket_dir:
        return f"Error: ticket not found at {TICKETS_DIR / ticket_id}"

    ticket_file = ticket_dir / "ticket.txt"
    if not ticket_file.exists():
        return f"Error: no ticket.txt in {ticket_dir}"

    content = ticket_file.read_text()

    # List attachment files (everything except ticket.txt)
    attachments = [f for f in ticket_dir.iterdir() if f.name != "ticket.txt"]
    if attachments:
        content += f"\nAttachments: {', '.join(f.name for f in attachments)}\n"

    return content


def _local_get_attachments(ticket_id: str, filename_filter: str | None) -> str:
    """List or return paths to attachment files in the local ticket directory."""
    ticket_dir = _local_ticket_dir(ticket_id)
    if not ticket_dir:
        return f"Error: ticket not found at {TICKETS_DIR / ticket_id}"

    attachments = [f for f in ticket_dir.iterdir() if f.name != "ticket.txt"]
    if not attachments:
        return f"No attachments on {ticket_id}"

    if not filename_filter:
        lines = [f"  {f.name} ({f.stat().st_size} bytes)" for f in attachments]
        return f"Attachments on {ticket_id}:\n" + "\n".join(lines)

    matched = [f for f in attachments if fnmatch(f.name, filename_filter)]
    if not matched:
        return f"No attachments matching {filename_filter!r} on {ticket_id}"

    lines = [f"  {f.name} → {f}" for f in matched]
    return f"Found {len(matched)} file(s):\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Remote mode helpers
# ---------------------------------------------------------------------------

def _remote_get_ticket(ticket_id: str) -> str:
    """Fetch a ticket from the Jira REST API."""
    resp = _jira_get(f"issue/{ticket_id}")
    if resp.status_code != 200:
        return f"Error: Jira API returned {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    fields = data["fields"]

    summary = fields.get("summary", "N/A")
    status = fields.get("status", {}).get("name", "N/A")
    reporter = fields.get("reporter", {}).get("displayName", "N/A")
    created = fields.get("created", "N/A")
    description = fields.get("description") or "No description"

    if len(description) > 2000:
        description = description[:2000] + "... (truncated)"

    comments = fields.get("comment", {}).get("comments", [])
    comment_lines: list[str] = []
    for c in comments[-10:]:
        author = c.get("author", {}).get("displayName", "Unknown")
        body = c.get("body", "")
        if len(body) > 500:
            body = body[:500] + "..."
        comment_lines.append(f"  [{author}]: {body}")

    attachments = fields.get("attachment", [])
    attachment_names = [a["filename"] for a in attachments]

    result = (
        f"Ticket: {ticket_id}\n"
        f"Summary: {summary}\n"
        f"Status: {status}\n"
        f"Reporter: {reporter}\n"
        f"Created: {created}\n"
        f"Description:\n  {description}\n"
    )

    if comment_lines:
        result += f"Comments ({len(comments)} total, showing last {len(comment_lines)}):\n"
        result += "\n".join(comment_lines) + "\n"

    if attachment_names:
        result += f"Attachments: {', '.join(attachment_names)}\n"

    return result


def _remote_get_attachments(ticket_id: str, filename_filter: str | None) -> str:
    """List or download attachments from the Jira REST API."""
    resp = _jira_get(f"issue/{ticket_id}?fields=attachment")
    if resp.status_code != 200:
        return f"Error: Jira API returned {resp.status_code}: {resp.text[:200]}"

    attachments = resp.json()["fields"].get("attachment", [])
    if not attachments:
        return f"No attachments on {ticket_id}"

    if not filename_filter:
        lines = [f"  {a['filename']} ({a.get('size', '?')} bytes)" for a in attachments]
        return f"Attachments on {ticket_id}:\n" + "\n".join(lines)

    matched = [a for a in attachments if fnmatch(a["filename"], filename_filter)]
    if not matched:
        return f"No attachments matching {filename_filter!r} on {ticket_id}"

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []
    for attachment in matched:
        url = attachment["content"]
        dest = WORK_DIR / attachment["filename"]
        dl = requests.get(url, auth=(JIRA_EMAIL, JIRA_API_TOKEN), timeout=120, stream=True)
        if dl.status_code != 200:
            downloaded.append(f"  {attachment['filename']}: download failed ({dl.status_code})")
            continue
        with open(dest, "wb") as f:
            for chunk in dl.iter_content(chunk_size=8192):
                f.write(chunk)
        downloaded.append(f"  {attachment['filename']} → {dest}")

    return f"Downloaded {len(downloaded)} file(s):\n" + "\n".join(downloaded)


# ---------------------------------------------------------------------------
# MCP tools — dispatch to local or remote
# ---------------------------------------------------------------------------

@mcp.tool()
def get_jira_ticket(ticket_id: str) -> str:
    """Fetch a Jira ticket's summary, description, status, comments, and timestamps.

    Use this as the starting point of an investigation — it gives you the
    reported issue, who reported it, when it happened, and any discussion.
    Works with local ticket files or the Jira API.
    """
    if _use_remote():
        return _remote_get_ticket(ticket_id)
    return _local_get_ticket(ticket_id)


@mcp.tool()
def get_jira_attachments(ticket_id: str, filename_filter: str | None = None) -> str:
    """List or retrieve attachments from a Jira ticket.

    Without a filename_filter, lists all attachments. With a filter (e.g.
    "*.log"), returns paths to matching files (local mode) or downloads
    them (remote mode). Works with local ticket files or the Jira API.
    """
    if _use_remote():
        return _remote_get_attachments(ticket_id, filename_filter)
    return _local_get_attachments(ticket_id, filename_filter)