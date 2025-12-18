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

    # Filter only "In Progress" or "In Training"
    issues = [i for i in all_issues if i["state"]["name"] in ["In Progress", "In Training"]]
    print(f"[INFO] Fetched {len(issues)} issues in progress/in training from Linear")
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
    Convert Linear assignee to Notion people property.
    Returns a list of dicts like [{"id": "NOTION_USER_ID"}].
    """
    assignee = issue.get("assignee")
    if not assignee:
        return []  # Unassigned

    # Map Linear assignee to Notion user ID
    notion_user_id = get_notion_user_id_by_email(assignee["name"])  # or implement name → user ID mapping

    if notion_user_id:
        return [{"id": notion_user_id}]
    return []

# ----------------------
# STEP 2: Sync issues to Notion
# ----------------------
def sync_to_notion(issues):
    for issue in issues:
        # Base properties
        properties = {
            "Title": {"title": [{"text": {"content": issue["title"]}}]},
            "Description": {"rich_text": [{"text": {"content": issue.get("description") or ""}}]},
            "Linear ID": {"rich_text": [{"text": {"content": issue["id"]}}]},
            "Created At": {"date": {"start": issue["createdAt"]}},
            "Status": {"select": {"name": issue["state"]["name"]}},  # In Progress / In Training
            "Priority": {"select": {"name": issue.get("priority", "Medium")}},  # Adjust default as needed
            "Project": {"select": {"name": issue.get("project_name", "None")}},  # Replace with actual field
            "Team": {"select": {"name": issue.get("team_name", "None")}},       # Replace with actual field
            "Linear URL": {"url": f"https://linear.app/issue/{issue['id']}"}
        }

        # Resolve Linear assignee to Notion people
        people = resolve_linear_assignee_to_notion(issue)
        if people:
            properties["Assignee"] = {"people": people}

        # Create the page in Notion
        try:
            notion.pages.create(
                parent={"database_id": NOTION_DATABASE_ID},
                properties=properties
            )
            print(f"[INFO] Synced issue '{issue['title']}' to Notion")
        except Exception as e:
            print(f"[ERROR] Failed to sync issue '{issue['title']}': {e}")


# ----------------------
# MAIN
# ----------------------
if __name__ == "__main__":
    TEAM_ID = "b9b18fd9-3e94-4e0c-b2b2-a7a1eb2560d6"  # <-- PLACE YOUR TEAM ID HERE
    issues = get_linear_issues(TEAM_ID)
    print(f"Found {len(issues)} issues in progress/in training.")
    sync_to_notion(issues)
    print("Sync complete.")

