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
            createdAt
            archivedAt
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

def find_notion_page_by_linear_id(database_id, linear_id):
    """Check if a Notion page with this Linear ID already exists using REST API."""
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
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


def sync_to_notion(issues):
        properties = {
            "Title": {"title": [{"text": {"content": issue["title"]}}]},
            "Description": {"rich_text": split_rich_text(issue.get("description") or "")},
            "Linear ID": {"rich_text": [{"text": {"content": issue["id"]}}]},
            "Created At": {"date": {"start": issue["createdAt"]}},
            "Status": {"select": {"name": issue["state"]["name"]}},
            "Priority": {"select": {"name": issue.get("priority", "Medium")}},
            "Linear URL": {"url": f"https://linear.app/issue/{issue['id']}"}
        }
        
        # Project
        project = issue.get("project")
        if project:
            properties["Project"] = {"select": {"name": project["name"]}}
        else:
            properties["Project"] = None
        
        # Cycle
        cycle = issue.get("cycle")
        properties["Cycle"] = {"select": {"name": str(cycle["number"])}} if cycle else None
        
        # Assignee
        people = resolve_linear_assignee_to_notion(issue)
        if people:
            properties["Assignee"] = {"people": people}
        
        # Labels (multi-select)
        labels = issue.get("labels", [])
        properties["Labels"] = {"multi_select": [{"name": l["name"]} for l in labels]} if labels else {"multi_select": []}


        try:
            # Check for existing page
            page = find_notion_page_by_linear_id(NOTION_DATABASE_ID, issue["id"])
            if page:
                # Update existing page
                notion.pages.update(page_id=page["id"], properties=properties)
                print(f"[INFO] Updated issue '{issue['title']}' in Notion")
            else:
                # Create new page
                notion.pages.create(parent={"database_id": NOTION_DATABASE_ID}, properties=properties)
                print(f"[INFO] Created issue '{issue['title']}' in Notion")
        except Exception as e:
            print(f"[ERROR] Failed to sync issue '{issue['title']}': {e}")

# ----------------------
# MAIN
# ----------------------
if __name__ == "__main__":
    TEAM_ID = "b9b18fd9-3e94-4e0c-b2b2-a7a1eb2560d6"  # <-- PLACE YOUR TEAM ID HERE
    issues = get_linear_issues(TEAM_ID)
    print(f"Found {len(issues)} issues in progress/in testing.")
    sync_to_notion(issues)
    print("Sync complete.")

