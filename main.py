import os
import requests
from datetime import datetime, timezone

# ======================
# CONFIG
# ======================

LINEAR_API_KEY = os.getenv("LINEAR_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DB_LINEAR")
SYNC_STATUS_PAGE_ID = os.getenv("SYNC_STATUS_PAGE_ID")

LINEAR_ENDPOINT = "https://api.linear.app/graphql"
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
    r = requests.post(
        LINEAR_ENDPOINT,
        headers=headers,
        json={"query": query, "variables": variables or {}},
    )
    r.raise_for_status()
    return r.json()["data"]

def get_linear_issues_updated_since(iso_ts):
    query = """
    query ($updatedAfter: DateTime!) {
      issues(
        first: 100
        filter: { updatedAt: { gte: $updatedAfter } }
      ) {
        nodes {
          id
          title
          description
          url
          priority
          createdAt
          updatedAt
          state { name }
          assignee { name email }
          team { name }
          project { name }
        }
      }
    }
    """
    data = linear_query(query, {"updatedAfter": iso_ts})
    return data["issues"]["nodes"]

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
    r = requests.get(
        f"{NOTION_BASE_URL}/pages/{page_id}",
        headers=notion_headers(),
    )
    r.raise_for_status()
    return r.json()

def update_notion_page(page_id, properties):
    r = requests.patch(
        f"{NOTION_BASE_URL}/pages/{page_id}",
        headers=notion_headers(),
        json={"properties": properties},
    )
    r.raise_for_status()

def query_notion_database(database_id, payload):
    r = requests.post(
        f"{NOTION_BASE_URL}/databases/{database_id}/query",
        headers=notion_headers(),
        json=payload,
    )
    r.raise_for_status()
    return r.json()["results"]

def create_notion_item(database_id, properties):
    r = requests.post(
        f"{NOTION_BASE_URL}/pages",
        headers=notion_headers(),
        json={
            "parent": {"database_id": database_id},
            "properties": properties,
        },
    )
    r.raise_for_status()

def get_notion_user_id_by_email(email):
    r = requests.get(
        f"{NOTION_BASE_URL}/users",
        headers=notion_headers(),
    )
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
    prop = page["properties"].get("Last Linear Sync", {})
    date = prop.get("date")
    if date and date.get("start"):
        return date["start"]
    return "1970-01-01T00:00:00Z"

def update_last_linear_sync(ts):
    update_notion_page(
        SYNC_STATUS_PAGE_ID,
        {
            "Last Linear Sync": {
                "date": {"start": ts}
            }
        }
    )
    print(f"[DEBUG] Updated last Linear sync → {ts}")

# ======================
# NOTION MAPPING
# ======================

def build_notion_properties(issue):
    props = {
        "Title": {
            "title": [{"text": {"content": issue["title"]}}]
        },
        "Linear ID": {
            "rich_text": [{"text": {"content": issue["id"]}}]
        },
        "Linear URL": {"url": issue["url"]},
        "Priority": {"number": issue["priority"]},
        "Created At": {"date": {"start": issue["createdAt"]}},
        "Updated At": {"date": {"start": issue["updatedAt"]}},
    }

    if issue.get("state"):
        props["Status"] = {
            "select": {"name": issue["state"]["name"]}
        }

    if issue.get("project"):
        props["Project"] = {
            "select": {"name": issue["project"]["name"]}
        }

    if issue.get("team"):
        props["Team"] = {
            "select": {"name": issue["team"]["name"]}
        }

    if issue.get("assignee"):
        user_id = get_notion_user_id_by_email(issue["assignee"]["email"])
        if user_id:
            props["Assignee"] = {"people": [{"id": user_id}]}

    return props

def find_existing_page(linear_id):
    results = query_notion_database(
        NOTION_DATABASE_ID,
        {
            "filter": {
                "property": "Linear ID",
                "rich_text": {"equals": linear_id}
            }
        },
    )
    return results[0] if results else None

# ======================
# MAIN SYNC
# ======================

def sync_linear():
    print("\n=== Linear → Notion Sync ===")

    last_sync = get_last_linear_sync()
    print(f"[DEBUG] Last sync: {last_sync}")

    issues = get_linear_issues_updated_since(last_sync)
    print(f"[DEBUG] Found {len(issues)} updated issues")

    for issue in issues:
        props = build_notion_properties(issue)
        existing = find_existing_page(issue["id"])

        if existing:
            update_notion_page(existing["id"], props)
            print(f"[UPDATED] {issue['title']}")
        else:
            create_notion_item(NOTION_DATABASE_ID, props)
            print(f"[CREATED] {issue['title']}")

    now = datetime.now(timezone.utc).isoformat()
    update_last_linear_sync(now)

# ======================
# ENTRYPOINT
# ======================

if __name__ == "__main__":
    sync_linear()
