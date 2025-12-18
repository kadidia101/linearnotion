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

# ----------------------
# STEP 1: Define GraphQL query to get issues for a team
# ----------------------
def get_linear_issues(team_id):
    url = "https://api.linear.app/graphql"
    headers = {
        "Authorization": f"Bearer {LINEAR_API_KEY}",
        "Content-Type": "application/json"
    }
    
    query = """
    query Team($teamId: String!) {
      team(id: $teamId) {
        id
        name
        issues(filter: {state: {name: {in: ["In Progress", "In Training"]}}}) {
          nodes {
            id
            title
            description
            assignee {
              id
              name
            }
            createdAt
            archivedAt
          }
        }
      }
    }
    """
    
    variables = {"teamId": "b9b18fd9-3e94-4e0c-b2b2-a7a1eb2560d6"} 
    
    response = requests.post(url, json={"query": query, "variables": variables}, headers=headers)
    response.raise_for_status()
    data = response.json()
    
    issues = data["data"]["team"]["issues"]["nodes"]
    return issues

# ----------------------
# STEP 2: Sync issues to Notion
# ----------------------
def sync_to_notion(issues):
    for issue in issues:
        # Check if issue already exists in Notion
        # (Optional: implement deduplication here if you want)
        
        notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties={
                "Title": {"title": [{"text": {"content": issue["title"]}}]},
                "Description": {"rich_text": [{"text": {"content": issue.get("description") or ""}}]},
                "Assignee": {"rich_text": [{"text": {"content": issue["assignee"]["name"] if issue["assignee"] else "Unassigned"}}]},
                "Linear ID": {"rich_text": [{"text": {"content": issue["id"]}}]},
                "Created At": {"date": {"start": issue["createdAt"]}}
            }
        )

# ----------------------
# MAIN
# ----------------------
if __name__ == "__main__":
    TEAM_ID = "your_team_id_here"  # <-- PLACE YOUR TEAM ID HERE
    issues = get_linear_issues(TEAM_ID)
    print(f"Found {len(issues)} issues in progress/in training.")
    sync_to_notion(issues)
    print("Sync complete.")

