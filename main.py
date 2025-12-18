import os
import requests
from datetime import datetime, timezone, timedelta

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
    r = requests.post(
        LINEAR_ENDPOINT,
        headers=headers,
        json={"query": query, "variables": variables or {}},
    )
    r.raise_for_status()
    return r.json()["data"]

def to_linear_iso(dt: datetime) -> str:
    if isinstance(dt, str):
        return dt.replace("+00:00", "Z")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
def get_linear_issues_updated_since(last_sync):
    headers = {
        "Authorization": f"Bearer {LINEAR_API_KEY}",
        "Content-Type": "application/json"
    }

    query = """
    query($updatedAfter: DateTime) {
      issues(first: 100, updatedAfter: $updatedAfter) {
        nodes {
          id
          identifier
          title
          description
          createdAt
          updatedAt
        }
      }
    }
    """

    variables = {}
    # Only send updatedAfter if last_sync is not the dummy 2000-01-01
    if last_sync and last_sync.year > 2000:
        iso_ts = last_sync.replace(microsecond=0).isoformat()
        if iso_ts.endswith("+00:00"):
            iso_ts = iso_ts.replace("+00:00", "Z")
        variables["updatedAfter"] = iso_ts

    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    response = requests.post("https://api.linear.app/graphql", json=payload, headers=headers)
    response.raise_for_status()
    data = response.json()
    if "errors" in data:
        raise Exception(f"Linear API returned errors: {data['errors']}")
    return data["data"]["issues"]["nodes"]

def get_all_linear_issues():
    headers = {
        "Authorization": f"Bearer {LINEAR_API_KEY}",
        "Content-Type": "application/json"
    }

    # GraphQL query: get first 100 issues with selected states
    query = """
    query {
      issues(first: 100, filter: { state: { name_in: ["In Progress", "In Testing"] } }) {
        nodes {
          id
          identifier
          title
          description
          state {
            name
          }
          createdAt
          updatedAt
          assignee {
            id
            name
            email
          }
          project {
            id
            name
          }
        }
      }
    }
    """

    payload = {"query": query}
    response = requests.post("https://api.linear.app/graphql", json=payload, headers=headers)
    
    # Will raise HTTPError if 400 or 401, etc.
    response.raise_for_status()
    data = response.json()
    
    # Extract issue nodes
    issues = data.get("data", {}).get("issues", {}).get("nodes", [])
    print(f"[DEBUG] Retrieved {len(issues)} issues from Linear")
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
    last_sync_str = page.get("properties", {}).get("Last Sync", {}).get("rich_text", [])
    
    if not last_sync_str:
        # First run — return a very old date to fetch everything
        return datetime(2000, 1, 1, tzinfo=timezone.utc)
    
    last_sync = datetime.fromisoformat(last_sync_str[0]["text"]["content"])
    return last_sync


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

    now = datetime.now(timezone.utc).isoformat()
    update_last_linear_sync(now)
    
    new_sync_time = datetime.now(timezone.utc)
    save_last_sync(new_sync_time)
    
    print(f"[DEBUG] Updated last sync timestamp: {new_sync_time.isoformat()}")


# ======================
# ENTRYPOINT
# ======================

if __name__ == "__main__":
    sync_linear()
