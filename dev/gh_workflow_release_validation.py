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
    """
    Communicates data back to the GitHub Actions runner.
    
    In a CI environment, it appends to the file defined by $GITHUB_OUTPUT.
    In local testing, it prints to stdout to simulate the behavior without 
    requiring the environment variable.
    """
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    else:
        print(f"DEBUG [Local]: GITHUB_OUTPUT not set. Result -> {key}={value}")

def conclude_validation(is_valid: bool, message: str, severity: str = "notice"):
    """
    Finalizes the validation process and exits the script.
    
    - severity="error": Marks the build as failed (exit 1).
    - severity="notice" or "warning": Allows the build to remain green (exit 0).
    - writes to $GITHUB_STEP_SUMMARY to provide a visible report in the UI.
    - sets the 'is_valid' output variable for downstream job conditions.
    """
    icon = "✅" if is_valid else ("❌" if severity == "error" else "⏭️")
    print(f"{icon} {severity.upper()}: {message}")

    # GitHub Workflow Command to highlight in the UI
    print(f"::{severity}::{message}")

    # Write to Job Summary (Visible on the run overview page)
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            status = "VALID" if is_valid else "SKIPPED"
            f.write(f"### Release Validation: {status}\n{message}\n")

    append_github_output("is_valid", "true" if is_valid else "false")
    sys.exit(1 if severity == "error" else 0)

def _get_run_info(run_id: str, repo: str, headers: dict, base_url: str) -> dict:
    """
    Fetches and validates GitHub Action run details from the API.
    
    Ensures the run ID exists, belongs to the 'Main Workflow', 
    and completed with a successful conclusion.
    """
    run_url = f"{base_url}/actions/runs/{run_id}"
    response = requests.get(run_url, headers=headers, timeout=15)
    if response.status_code == 404:
        raise ValueError(
            f"Run ID '{run_id}' not found in '{repo}'. "
            f"Please verify the ID is correct and corresponds to a successful 'Main Workflow' run. "
            f"History: https://github.com/{repo}/actions"
        )
    response.raise_for_status()
    
    run_data = response.json()
    if run_data.get("conclusion") != "success":
        raise ValueError(f"Run ID '{run_id}' has a conclusion of '{run_data.get('conclusion')}'. Only successful workflow runs can be used for a release.")
    if run_data.get("name") != "Main Workflow":
        raise ValueError(f"Run ID '{run_id}' belongs to workflow '{run_data.get('name')}', but artifacts must originate from the 'Main Workflow'.")
    
    return run_data

def _get_tag_sha(tag_name: str, repo: str, headers: dict, base_url: str) -> str:
    """
    Resolves a tag name or reference to its underlying commit SHA.
    
    This is used to verify the relationship between a version tag and a specific build.
    """
    # Using the commits endpoint to resolve the tag ref to a specific commit SHA
    tag_res = requests.get(f"{base_url}/commits/{tag_name}", headers=headers, timeout=15)
    if tag_res.status_code == 404:
        raise ValueError(f"The tag '{tag_name}' was not found in the repository '{repo}'. Please ensure the tag has been pushed.")
    tag_res.raise_for_status()
    
    return tag_res.json().get("sha")

def _find_tag_for_sha(commit_sha: str, repo: str, headers: dict, base_url: str) -> str:
    """
    Performs a reverse-lookup to find a tag associated with a specific SHA.
    
    Priority is given to lightweight tags starting with 'v', falling back to annotated tags.
    """
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
    
    return tag_name

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
        conclude_validation(False, f"Missing required environment variables. EVENT: '{event_name}', REPO: '{repo}'.", severity="error")

    if not token:
        print("⚠️  Warning: GH_TOKEN is not set. API calls will likely fail.")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    base_url = f"https://api.github.com/repos/{repo}"

    # PATH A: Manual Trigger (Untrusted Input)
    # We verify that the user-provided Tag and Run ID actually exist and match each other.
    if event_name == "workflow_dispatch":
        print("🔮 Triggered Manually via Workflow Dispatch.")
        if not manual_tag or not manual_run_id or not re.match(r"^[0-9]+$", manual_run_id):
            conclude_validation(False, "Invalid input: A version tag and a numeric Run ID are required for manual releases.", severity="error")
        
        # Explicitly verify the provided run and tag metadata via the API
        try:
            print(f"Validating Run ID '{manual_run_id}'...")
            run_data = _get_run_info(manual_run_id, repo, headers, base_url)
            run_sha = run_data.get("head_sha")

            print(f"Verifying if tag '{manual_tag}' aligns with commit {run_sha[:8]}...")
            tag_sha = _get_tag_sha(manual_tag, repo, headers, base_url)

            if tag_sha != run_sha:
                conclude_validation(False, f"Mismatch! Tag '{manual_tag}' points to {tag_sha[:8]}, but Run ID {manual_run_id} was built from {run_sha[:8]}.", severity="error")

        except ValueError as e:
            conclude_validation(False, str(e), severity="error")
        except Exception as e:
            conclude_validation(False, f"Unexpected system error during manual verification: {e}", severity="error")

        append_github_output("version", manual_tag)
        append_github_output("run_id", manual_run_id)
        conclude_validation(True, f"Manual validation passed. Target Tag: {manual_tag} | Run ID: {manual_run_id}")
        return

    # PATH B: Automated Trigger (Trusted Input)
    # GitHub provides the Run ID and SHA. We only need to find the corresponding 
    # Version Tag to justify a release.
    if event_name != "workflow_run":
        conclude_validation(False, f"Unsupported EVENT type: '{event_name}'.", severity="error")

    print("🤖 Triggered Automatically by Main Workflow.")
    if upstream_conclusion != "success":
        conclude_validation(False, f"Trigger skipped. Upstream conclusion was '{upstream_conclusion}'.", severity="notice")

    print(f"Querying GitHub API for repository tags matching commit {commit_sha}...")
    try:
        tag_name = _find_tag_for_sha(commit_sha, repo, headers, base_url)

        if not tag_name:
            conclude_validation(False, f"No SemVer tag pointed to commit {commit_sha}. Skipping release.", severity="notice")

        append_github_output("version", tag_name)
        append_github_output("run_id", automated_run_id)
        conclude_validation(True, f"Valid SemVer tag located: {tag_name}")

    except ValueError as e:
        conclude_validation(False, str(e), severity="error")
    except Exception as e:
        conclude_validation(False, f"Unexpected system error during automated resolution: {e}", severity="error")

if __name__ == "__main__":
    main()
