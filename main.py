import os
import requests
from datetime import datetime, timezone
from notion_client import Client

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
def get_linear_issues(team_id):
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
            title
            description
            assignee {
              id
              name
            }
            state {
              name
            }
            cycle { number } 
            labels {
                edges {
                    node {
                        id
                        name
                    }
                }
            }
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
    after = None

    while True:
        variables = {"teamId": team_id, "after": after}
        response = requests.post(url, json={"query": query, "variables": variables}, headers=headers)
        data = response.json()

        if "errors" in data:
            print("Linear API returned errors:", data["errors"])
            break

        nodes = data["data"]["team"]["issues"]["nodes"]
        all_issues.extend(nodes)

        page_info = data["data"]["team"]["issues"]["pageInfo"]
        if page_info["hasNextPage"]:
            after = page_info["endCursor"]
        else:
            break

    # Filter only "In Progress" or "In Testing"
    issues = [i for i in all_issues if i["state"]["name"] in ["In Progress", "In Testing"]]
    print(f"[INFO] Fetched {len(issues)} issues in progress/in testing from Linear")
    return issues

    
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
    assignees = issue.get("assignees") or [issue.get("assignee")]  # If Linear supports multiple assignees
    people = []
    for assignee in assignees:
        if assignee:
            notion_user_id = get_notion_user_id_by_email(assignee["name"])
            if notion_user_id:
                people.append({"id": notion_user_id})
    return people
# ----------------------
# STEP 2: Sync issues to Notion
# ----------------------
def split_rich_text(text, chunk_size=2000):
    """
    Splits a long string into a list of rich_text blocks for Notion.
    """
    return [{"text": {"content": text[i:i+chunk_size]}} for i in range(0, len(text), chunk_size)]

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


def get_last_sync_time(file_path="last_sync.txt"):
    try:
        with open(file_path, "r") as f:
            ts = f.read().strip()
            # Add UTC timezone
            return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except Exception:
        return datetime(2000, 1, 1, tzinfo=timezone.utc)


def save_last_sync_time(timestamp, file_path="last_sync.txt"):
    with open(file_path, "w") as f:
        f.write(timestamp.isoformat())

def sync_to_notion(issues):
    last_sync = get_last_sync_time()
    # Determine issues to sync with debugging
    issues_to_sync = []
    for issue in issues:
        issue_time_str = issue.get("updatedAt") or issue.get("createdAt")
        issue_time = datetime.fromisoformat(issue_time_str.replace("Z", "+00:00"))
    
        # DEBUG: print issue info and timestamp comparison
        print(f"[DEBUG] Issue '{issue['title']}' (ID: {issue['id']}) updated at {issue_time} | last_sync={last_sync}")
    
        # Include all issues if first run, otherwise only if updated since last sync
        if last_sync.year <= 2000 or issue_time > last_sync:
            issues_to_sync.append(issue)
            print(f"[DEBUG] -> Will be synced")
        else:
            print(f"[DEBUG] -> Skipped (not updated since last sync)")
    

    print(f"[INFO] {len(issues)} issues fetched from Linear")
    print(f"[INFO] {len(issues_to_sync)} issues to process")

    for issue in issues_to_sync:
        page = find_notion_page_by_linear_id(issue["id"])

        # Build Notion properties
        properties = {
            "Title": {"title": [{"text": {"content": issue["title"]}}]},
            "Description": {"rich_text": split_rich_text(issue.get("description") or "")},
            "Linear ID": {"rich_text": [{"text": {"content": issue["id"]}}]},
            "Created At": {"date": {"start": issue["createdAt"]}},
            "Status": {"select": {"name": issue["state"]["name"]}},
            "Linear URL": {"url": f"https://linear.app/issue/{issue['id']}"}
        }

        # Priority (string)
        raw_priority = issue.get("priority", "medium")
        if isinstance(raw_priority, int):
            priority_name = LINEAR_PRIORITY_MAP.get(raw_priority, "Medium")
        else:
            priority_name = str(raw_priority).capitalize()
        properties["Priority"] = {"select": {"name": priority_name}}

        # Project (select)
        project = issue.get("project")
        if project:
            properties["Project"] = {"select": {"name": project["name"]}}
        else:
            properties["Project"] = None

        # Cycle (single select, convert number to string)
        cycle = issue.get("cycle")
        if cycle:
            properties["Cycle"] = {"select": {"name": str(cycle["number"])}}
        else:
            properties["Cycle"] = None

        # Assignee (people)
        people = resolve_linear_assignee_to_notion(issue)
        if people:
            properties["Assignee"] = {"people": people}

        # Labels (multi-select)
        labels_edges = issue.get("labels", {}).get("edges", [])
        properties["Labels"] = {
            "multi_select": [{"name": e["node"]["name"]} for e in labels_edges]
        } if labels_edges else {"multi_select": []}

        # Create or update page
        try:
            if page:
                notion.pages.update(page_id=page["id"], properties=properties)
                print(f"[INFO] Updated issue '{issue['title']}' in Notion")
            else:
                notion.pages.create(
                    parent={"database_id": NOTION_DATABASE_ID},
                    properties=properties
                )
                print(f"[INFO] Created issue '{issue['title']}' in Notion")
        except Exception as e:
            print(f"[ERROR] Failed to sync issue '{issue['title']}': {e}")

    # Save last sync time
    save_last_sync_time(datetime.utcnow())
    print(f"[INFO] Sync complete. {len(issues_to_sync)} issues processed.")
    
# ----------------------
# MAIN
# ----------------------
if __name__ == "__main__":
    TEAM_ID = "b9b18fd9-3e94-4e0c-b2b2-a7a1eb2560d6"  # <-- PLACE YOUR TEAM ID HERE
    issues = get_linear_issues(TEAM_ID)
    print(f"Found {len(issues)} issues in progress/in testing.")
    sync_to_notion(issues)
    print("Sync complete.")
