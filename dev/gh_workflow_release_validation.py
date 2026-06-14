# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "requests",
# ]
# ///

"""
GitHub Workflow for Validating Release
--------------------------------------
This script resolves and validates release tags and run IDs for CI/CD pipelines.
It supports both automated 'workflow_run' triggers and manual 'workflow_dispatch' overrides.

LOCAL TESTING INSTRUCTIONS:
1. Ensure 'uv' is installed.
2. To test Manual Trigger:
   EVENT="workflow_dispatch" REPO="owner/repo" TAG="v1.0.0" MANUAL_ID="12345" uv run dev/gh_workflow_release_validation.py

3. To test Automated Trigger (requires GitHub PAT for API calls):
   GH_TOKEN="your_pat" EVENT="workflow_run" REPO="owner/repo" CONCLUSION="success" \
   SHA="$(git rev-parse HEAD)" AUTO_ID="67890" uv run dev/gh_workflow_release_validation.py
"""

import os
import re
import sys
import requests

def append_github_output(key: str, value: str):
    """Writes to GITHUB_OUTPUT in CI, or prints to stdout locally."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    else:
        print(f"DEBUG [Local]: GITHUB_OUTPUT not set. Result -> {key}={value}")

def fail_gate(message: str):
    """Logs an error and exits the workflow job cleanly but marked as invalid."""
    print(f"❌ ERROR: {message}")
    append_github_output("is_valid", "false")
    sys.exit(0)

def main():
    event_name = os.environ.get("EVENT")
    repo = os.environ.get("REPO")
    token = os.environ.get("GH_TOKEN")
    manual_tag = os.environ.get("TAG", "").strip()
    manual_run_id = os.environ.get("MANUAL_ID", "").strip()
    upstream_conclusion = os.environ.get("CONCLUSION")
    commit_sha = os.environ.get("SHA")
    automated_run_id = os.environ.get("AUTO_ID")

    if not event_name or not repo:
        fail_gate(f"Missing required environment variables. EVENT: '{event_name}', REPO: '{repo}'.")

    if not token:
        print("⚠️  Warning: GH_TOKEN is not set. API calls will likely fail.")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    # LOGIC PATH A: Manual Trigger
    if event_name == "workflow_dispatch":
        print("🔮 Triggered Manually via Workflow Dispatch.")
        if not manual_tag or not manual_run_id or not re.match(r"^[0-9]+$", manual_run_id):
            fail_gate("Manual validation failed. Ensure tag is present and run ID is numeric.")
        
        print(f"✅ Validation passed. Target Tag: {manual_tag} | Run ID: {manual_run_id}")
        append_github_output("is_valid", "true")
        append_github_output("version", manual_tag)
        append_github_output("run_id", manual_run_id)
        return

    # LOGIC PATH B: Automated Trigger
    if event_name != "workflow_run":
        fail_gate(f"Unsupported EVENT type: '{event_name}'.")

    print("🤖 Triggered Automatically by Main Workflow.")
    if upstream_conclusion != "success":
        fail_gate(f"Trigger skipped. Upstream conclusion was '{upstream_conclusion}'.")

    print(f"Querying GitHub API for repository tags matching commit {commit_sha}...")
    try:
        base_url = f"https://api.github.com/repos/{repo}"
        
        # Attempt to find Lightweight Tags
        refs_url = f"{base_url}/git/matching-refs/tags/v"
        response = requests.get(refs_url, headers=headers, timeout=15)
        response.raise_for_status()
        tag_name = next((ref.get("ref", "").replace("refs/tags/", "") 
                        for ref in response.json() 
                        if ref.get("object", {}).get("sha") == commit_sha), "")

        # Fallback to Annotated Tags if necessary
        if not tag_name:
            tags_url = f"{base_url}/tags"
            response = requests.get(tags_url, headers=headers, timeout=15)
            response.raise_for_status()
            tag_name = next((tag.get("name") for tag in response.json() if tag.get("commit", {}).get("sha") == commit_sha), "")

        if not tag_name:
            fail_gate(f"No SemVer tag pointed to commit {commit_sha}.")

        print(f"✅ SUCCESS: Valid SemVer tag located: {tag_name}")
        append_github_output("is_valid", "true")
        append_github_output("version", tag_name)
        append_github_output("run_id", automated_run_id)

    except Exception as e:
        fail_gate(f"API interaction error: {e}")

if __name__ == "__main__":
    main()