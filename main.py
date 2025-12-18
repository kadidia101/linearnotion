import os
import requests
from datetime import datetime, timezone

# ======================
# CONFIG
# ======================

LINEAR_API_KEY = os.environ.get("LINEAR_API_KEY")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DATABASE_ID = os.environ.get("NOTION_DB_LINEAR")
SYNC_STATUS_PAGE_ID = os.environ.get("SYNC_STATUS_PAGE_ID")

LINEAR_URL = "https://api.linear.app/graphql"
NOTION_BASE_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# ======================
# LINEAR HELPERS
# ======================

def linear_query(query, variables=None):
    headers = {
        "Authorization": f"Bearer {LINEAR_API_KEY}",
        "Content-Type": "application/json",
    }
    r = requests.post(LINEAR_URL, headers=headers, json={"query": query, "variables": variables or {}})
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise Exception(f"Linear API returned errors: {data['errors']}")
    return data["data"]

def get_teams():
    query = """
    query Teams {
      teams {
        nodes {
          id
          name
        }
      }
    }
    """
    data = linear_query(query)
    return data["teams"]["nodes"]

def get_issues_for_team(team_id):
    query = f"""
    query Team {{
      team(id: {team_id}) {{
        id
        name
        issues {{
          nodes {{
            id
            identifier
            title
            description
            url
            state {{
              name
            }}
            priority
            createdAt
            updatedAt
            assignee {{
              email
              name
            }}
          }}
        }}
      }}
    }}
    """
    data = linear_query(query)
    return data["team"]["issues"]["nodes"]

    

def get_all_linear_issues():
    """Fetch all issues from all teams."""
    issues = []
    teams = get_teams()
    for team in teams:
        team_issues = get_issues_for_team(team["id"])
        issues.extend(team_issues)
    return issues

# ======================
# NOTION HELPERS
# ======================

def notion_headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

def get_notion_page(page_id):
    r = requests.get(f"{NOTION_BASE_URL}/pages/{page_id}", headers=notion_headers())
    r.raise_for_status()
    return r.json()

def update_notion_page(page_id, properties):
    r = requests.patch(f"{NOTION_BASE_URL}/pages/{page_id}", headers=notion_headers(), json={"properties": properties})
    r.raise_for_status()

def query_notion_database(database_id, payload):
    r = requests.post(f"{NOTION_BASE_URL}/databases/{database_id}/query", headers=notion_headers(), json=payload)
    r.raise_for_status()
    return r.json()["results"]

def create_notion_item(database_id, properties):
    r = requests.post(f"{NOTION_BASE_URL}/pages", headers=notion_headers(), json={"parent": {"database_id": database_id}, "properties": properties})
    r.raise_for_status()

def get_notion_user_id_by_email(email):
    r = requests.get(f"{NOTION_BASE_URL}/users", headers=notion_headers())
    r.raise_for_status()
    for user in r.json()["results"]:
        if user.get("person", {}).get("email") == email:
            return user["id"]
    return None

# ======================
# SYNC STATE
# ======================

def get_last_linear_sync():
    page = get_notion_page(SYNC_STATUS_PAGE_ID)
    last_sync_text = page.get("properties", {}).get("Last Linear Sync", {}).get("rich_text", [])
    if not last_sync_text:
        # First run — return a very old date
        return datetime(2000, 1, 1, tzinfo=timezone.utc)
    last_sync = datetime.fromisoformat(last_sync_text[0]["text"]["content"])
    return last_sync

def update_last_linear_sync(ts: str):
    update_notion_page(
        SYNC_STATUS_PAGE_ID,
        {
            "Last Linear Sync": {
                "rich_text": [{"text": {"content": ts}}]
            }
        }
    )
    print(f"[DEBUG] Updated last Linear sync → {ts}")

# ======================
# NOTION MAPPING
# ======================

def build_notion_properties(issue):
    props = {
        "Title": {"title": [{"text": {"content": issue["title"]}}]},
        "Linear ID": {"rich_text": [{"text": {"content": issue["id"]}}]},
        "Linear URL": {"url": issue.get("url")},
        "Priority": {"number": issue.get("priority")},
        "Created At": {"date": {"start": issue.get("createdAt")}},
        "Updated At": {"date": {"start": issue.get("updatedAt")}},
    }
    if issue.get("state"):
        props["Status"] = {"select": {"name": issue["state"]["name"]}}
    if issue.get("assignee") and issue["assignee"].get("email"):
        user_id = get_notion_user_id_by_email(issue["assignee"]["email"])
        if user_id:
            props["Assignee"] = {"people": [{"id": user_id}]}
    return props

def find_existing_page(linear_id):
    results = query_notion_database(
        NOTION_DATABASE_ID,
        {"filter": {"property": "Linear ID", "rich_text": {"equals": linear_id}}},
    )
    return results[0] if results else None

# ======================
# MAIN SYNC
# ======================

def sync_linear():
    print("=== Linear → Notion Sync ===")
    issues = get_all_linear_issues()
    print(f"[DEBUG] Retrieved {len(issues)} issues from Linear")

    for issue in issues:
        props = build_notion_properties(issue)
        existing = find_existing_page(issue["id"])
        if existing:
            update_notion_page(existing["id"], props)
            print(f"[UPDATED] {issue['title']}")
        else:
            create_notion_item(NOTION_DATABASE_ID, props)
            print(f"[CREATED] {issue['title']}")

    now_iso = datetime.now(timezone.utc).isoformat()
    update_last_linear_sync(now_iso)
    print(f"[DEBUG] Updated last sync timestamp: {now_iso}")

# ======================
# ENTRYPOINT
# ======================

if __name__ == "__main__":
    sync_linear()
