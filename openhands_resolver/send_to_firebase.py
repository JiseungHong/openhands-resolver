import argparse
import os
import shutil
from openhands_resolver.github_issue import GithubIssue
from openhands_resolver.io_utils import (
    load_all_resolver_outputs,
    load_single_resolver_output,
)
from openhands_resolver.patching import parse_patch, apply_diff
import requests
import subprocess
import shlex
import json

from openhands_resolver.resolver_output import ResolverOutput

def main():
    parser = argparse.ArgumentParser(description="Send a pull request to Github.")
    parser.add_argument(
        "--github-token",
        type=str,
        default=None,
        help="Github token to access the repository.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Output directory to write the results.",
    )
    parser.add_argument(
        "--issue-number",
        type=str,
        required=True,
        help="Issue number to send the pull request for, or 'all_successful' to process all successful issues.",
    )
    my_args = parser.parse_args()
    
    github_token = (
        my_args.github_token if my_args.github_token else os.getenv("GITHUB_TOKEN")
    )
    if not github_token:
        raise ValueError(
            "Github token is not set, set via --github-token or GITHUB_TOKEN environment variable."
        )
    
    if not os.path.exists(my_args.output_dir):
        raise ValueError(f"Output directory {my_args.output_dir} does not exist.")
    
    issue_number = int(my_args.issue_number)
    
    output_path1 = os.path.join(my_args.output_dir, "output1.jsonl")
    output_path2 = os.path.join(my_args.output_dir, "output2.jsonl")
    resolver_output1 = load_single_resolver_output(output_path1, issue_number)
    resolver_output2 = load_single_resolver_output(output_path2, issue_number)
    
    data = {'issue_number': issue_number, \
        'model_name_1': resolver_output1.model, 'git_patch_1': resolver_output1.git_patch, \
        'model_name_2': resolver_output2.model, 'git_patch_2': resolver_output2.git_patch}
    
    firebase_database_url = "{firebase_database_url}"
    
    firebase_url = f"{firebase_database_url}/issues/{issue_number}.json"
    response = requests.put(firebase_url, json=data)
    
    if response.status_code == 200:
        print(f"Data successfully sent to Firebase for issue {issue_number}.")
    else:
        print(f"Failed to send data to Firebase: {response.text}")
        response.raise_for_status()
    
if __name__ == "__main__":
    main()
