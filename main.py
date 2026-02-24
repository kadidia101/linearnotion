import os
import requests
from datetime import datetime, timezone
from notion_client import Client
import time
import random

# ======================
# CONFIG
# ======================

LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DATABASE_ID = os.environ.get("NOTION_DB_LINEAR")

notion = Client(auth=NOTION_TOKEN)

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

LINEAR_PRIORITY_MAP = {
    0: "No priority",
    1: "Low",
    2: "Medium",
    3: "High",
    4: "Urgent"
}

# ----------------------
# STEP 1: Define GraphQL query to get issues for a team
# ----------------------
def get_linear_issues(team_id, team_name):
    url = "https://api.linear.app/graphql"
    headers = {"Authorization": LINEAR_API_KEY, "Content-Type": "application/json"}

    query = """
    query TeamIssues($teamId: String!, $after: String) {
      team(id: $teamId) {
        issues(first: 50, after: $after) {
          pageInfo {
            hasNextPage
            endCursor
          }
          nodes {
            id
            url
            title
            description
            assignee { id name }
            state { name }
            team { id name }
            cycle { number }
            labels { edges { node { id name } } }
            updatedAt
            project { id name }
            createdAt
            archivedAt
            priority
          }
        }
      }
    }
    """

    all_issues = []
    seen_ids = set()      
    after = None

    while True:
        variables = {"teamId": team_id, "after": after}
        response = requests.post(url, json={"query": query, "variables": variables}, headers=headers)
        data = response.json()

        if "errors" in data:
            print("[ERROR] Linear API returned errors:", data["errors"])
            break

        nodes = data.get("data", {}).get("team", {}).get("issues", {}).get("nodes", [])

        for node in nodes:
            issue_id = (node.get("id") or "").strip().lower()  # ✅ normalize
            if not issue_id:
                continue

            if issue_id in seen_ids:
                print(f"[DEBUG] Duplicate issue returned by Linear pagination, skipping: {issue_id}")
                continue
            seen_ids.add(issue_id)

            # Ensure each issue has the team info
            node["team"] = {"id": team_id, "name": team_name or "Unknown"}

            all_issues.append(node)

        page_info = data.get("data", {}).get("team", {}).get("issues", {}).get("pageInfo", {})
        if page_info.get("hasNextPage"):
            after = page_info.get("endCursor")
        else:
            break

    print(f"[INFO] Fetched {len(all_issues)} total issues from Linear for team {team_name} ({team_id})")
    return all_issues


def get_linear_teams():
    """
    Returns a dict: {team_name: team_id} for all teams in the workspace.
    """
    url = "https://api.linear.app/graphql"
    headers = {"Authorization": LINEAR_API_KEY, "Content-Type": "application/json"}
    
    query = """
    query {
      teams {
        nodes {
          id
          name
        }
      }
    }
    """
    
    response = requests.post(url, json={"query": query}, headers=headers)
    data = response.json()
    
    if "errors" in data:
        print("[ERROR] Linear API returned errors when fetching teams:", data["errors"])
        return {}
    
    teams = {}
    for node in data.get("data", {}).get("teams", {}).get("nodes", []):
        teams[node["name"]] = node["id"]
    
    print(f"[INFO] Fetched {len(teams)} teams from Linear")
    return teams


def get_notion_user_id_by_email(email):
    if not email:
        return None

    url = "https://api.notion.com/v1/users"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()

    for user in response.json().get("results", []):
        person = user.get("person")
        if person and person.get("email") == email:
            return user["id"]

    return None
    
def resolve_linear_assignee_to_notion(issue):
    """
    Convert Linear assignees to Notion people property.
    Returns a list of dicts like [{"id": "NOTION_USER_ID"}].
    """
    assignees = issue.get("assignees") or [issue.get("assignee")]  # Support multiple or single assignee
    people = []

    for assignee in assignees:
        if assignee:
            # DEBUG: show what we are passing to get_notion_user_id_by_email
            print(f"[DEBUG] Assignee raw data: {assignee}")
            email = assignee.get("email") or assignee.get("name")  # fallback if email missing
            print(f"[DEBUG] Using '{email}' to resolve Notion user ID")
            
            notion_user_id = get_notion_user_id_by_email(email)
            if notion_user_id:
                people.append({"id": notion_user_id})
            else:
                print(f"[WARNING] Could not find Notion user for '{email}'")

    return people

# ----------------------
# STEP 2: Sync issues to Notion
# ----------------------

def find_notion_page_by_linear_id(linear_id):
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "Linear ID",
            "rich_text": {"equals": linear_id}
        }
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    response.raise_for_status()
    results = response.json().get("results", [])
    return results[0] if results else None

    
def split_rich_text(text, chunk_size=2000):
    """
    Splits a long string into a list of rich_text blocks for Notion.
    """
    return [{"text": {"content": text[i:i+chunk_size]}} for i in range(0, len(text), chunk_size)]

def get_record_last_sync(page):
    prop = page["properties"].get("last_sync")
    if prop and prop.get("date") and prop["date"].get("start"):
        return datetime.fromisoformat(prop["date"]["start"]).replace(tzinfo=timezone.utc)
    return datetime(2000, 1, 1, tzinfo=timezone.utc)

def save_record_last_sync(page_id, timestamp):
    notion.pages.update(
        page_id=page_id,
        properties={
            "last_sync": {"date": {"start": timestamp.isoformat()}}
        }
    )

def get_all_notion_pages():
    """
    Fetch all pages from the Notion database and return a dict mapping Linear ID -> page object
    """
    pages = {}
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    payload = {}
    has_more = True
    start_cursor = None

    while has_more:
        if start_cursor:
            payload["start_cursor"] = start_cursor

        response = requests.post(url, headers=HEADERS, json=payload)
        response.raise_for_status()
        data = response.json()

        for page in data.get("results", []):
            rt = page["properties"].get("Linear ID", {}).get("rich_text", [])
            linear_id = "".join(x.get("plain_text", "") for x in rt).strip().lower()
            if linear_id:
                pages[linear_id] = page
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    print(f"[INFO] Retrieved {len(pages)} pages from Notion")
    return pages

def safe_create_or_get_existing(linear_id, properties, retries=5):
    """
    Creates a Notion page. If the request errors/times out, query by Linear ID
    to see if it was actually created, and return that instead (prevents dupes).
    """
    for attempt in range(retries):
        try:
            resp = requests.post(
                "https://api.notion.com/v1/pages",
                headers=HEADERS,
                json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties},
                timeout=60,
            )

            if resp.status_code == 429 or resp.status_code >= 500:
                wait = (2 ** attempt) + random.random()
                print(f"[WARN] Notion create {resp.status_code} — retrying in {wait:.2f}s")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.RequestException as e:
            # Reconcile: page may have been created even though request failed
            existing = find_notion_page_by_linear_id(linear_id)
            if existing:
                print(f"[WARN] Create errored but page exists; using existing page for {linear_id}")
                return existing

            wait = (2 ** attempt) + random.random()
            print(f"[WARN] Notion create failed ({e}) — retrying in {wait:.2f}s")
            time.sleep(wait)

    # Final reconciliation
    existing = find_notion_page_by_linear_id(linear_id)
    if existing:
        print(f"[WARN] Create failed after retries but page exists; using existing page for {linear_id}")
        return existing

    raise Exception(f"Notion create failed after retries for Linear ID {linear_id}")

# ----------------------
# MAIN
# ----------------------

def sync_to_notion(issues, notion_pages):
    """
    Sync a list of issues to Notion. Issues are guaranteed to have 'team' key.

    Requirements/assumptions:
    - notion_pages is a dict keyed by normalized Linear UUID (lowercased, stripped) -> Notion page object
      (built by get_all_notion_pages using the same normalization).
    - find_notion_page_by_linear_id(linear_id) returns a Notion page object or None.
    - safe_create_or_get_existing(linear_id, properties) exists (prevents dupes on create timeouts/5xx).
    """

    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    # ✅ Prevent duplicate creates within the same Lambda invocation
    seen_linear_ids = set()

    for issue in issues:
        linear_id = (issue.get("id") or "").strip().lower()
        if not linear_id:
            print(f"[WARNING] Issue missing 'id': {issue}")
            continue

        # ✅ If the same issue appears twice in the same run, don't process it twice
        if linear_id in seen_linear_ids:
            print(f"[DEBUG] Duplicate issue in input list (same run), skipping: {linear_id}")
            continue
        seen_linear_ids.add(linear_id)

        # Try preload map first
        page = notion_pages.get(linear_id)

        # ✅ Fallback: if preload missed it, query Notion directly (idempotency)
        if not page:
            page = find_notion_page_by_linear_id(linear_id)
            if page:
                notion_pages[linear_id] = page

        # DEBUG
        print(f"[DEBUG] Processing issue: {issue.get('title')} (Linear ID: {linear_id})")

        # Safely get each field
        title = issue.get("title", "No title")
        description = issue.get("description") or ""
        created_at = issue.get("createdAt", None)

        updated_at_str = issue.get("updatedAt")
        updated_at = (
            datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
            if updated_at_str
            else None
        )

        state_name = (issue.get("state") or {}).get("name", "Unknown")
        project_name = (issue.get("project") or {}).get("name", "None")

        cycle = issue.get("cycle") or {}
        cycle_number = str(cycle.get("number", "None"))

        team_name = (issue.get("team") or {}).get("name", "Unknown")
        priority = LINEAR_PRIORITY_MAP.get(issue.get("priority", 2), "Medium")

        # Determine last_sync from the page
        last_sync = None
        if page:
            prop = page["properties"].get("last_sync")
            if prop and prop.get("date") and prop["date"].get("start"):
                last_sync = datetime.fromisoformat(prop["date"]["start"]).replace(tzinfo=timezone.utc)

        # Skip if the issue hasn’t been updated since last sync
        if last_sync and updated_at and updated_at <= last_sync:
            print(f"[DEBUG] Skipping '{title}' (updated_at {updated_at}, last_sync {last_sync})")
            continue

        # Build Notion properties
        properties = {
            "Title": {"title": [{"text": {"content": title}}]},
            "Description": {"rich_text": split_rich_text(description)},
            "Linear ID": {"rich_text": [{"text": {"content": linear_id}}]},
            "Created At": {"date": {"start": created_at}},
            "Status": {"select": {"name": state_name}},
            "Project": {"select": {"name": project_name}},
            "Cycle": {"select": {"name": cycle_number}},
            "Team": {"select": {"name": team_name}},
            "Linear URL": {"url": issue.get("url")},
            "Priority": {"select": {"name": priority}},
        }

        # Assignee
        people = resolve_linear_assignee_to_notion(issue)
        if people:
            properties["Assignee"] = {"people": people}

        # Labels
        labels_edges = (issue.get("labels") or {}).get("edges", [])
        properties["Labels"] = (
            {"multi_select": [{"name": e["node"]["name"]} for e in labels_edges if e.get("node")]}
            if labels_edges
            else {"multi_select": []}
        )

        try:
            if page:
                # Update existing page
                notion.pages.update(page_id=page["id"], properties=properties)
                notion.pages.update(
                    page_id=page["id"],
                    properties={"last_sync": {"date": {"start": now.isoformat()}}},
                )
                print(f"[INFO] Updated issue '{title}' in Notion")
            else:
                # Create safely; if create “fails” but actually succeeded, we re-find it
                new_page = safe_create_or_get_existing(
                    linear_id,
                    {**properties, "last_sync": {"date": {"start": now.isoformat()}}},
                )
                # Cache immediately to prevent later creates in same run
                notion_pages[linear_id] = new_page
                print(f"[INFO] Created issue '{title}' in Notion (or found existing)")
        except Exception as e:
            print(f"[ERROR] Failed to sync issue '{title}': {e}")
            print(f"[DEBUG] Full issue object causing error: {issue}")
