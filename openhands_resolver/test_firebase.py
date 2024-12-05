# flake8: noqa: E501

import asyncio
import dataclasses
import shutil
from typing import Any, Awaitable, TextIO
import argparse
import multiprocessing as mp
import os
import pathlib
import subprocess
import json
import random

from termcolor import colored
from tqdm import tqdm


from openhands_resolver.github_issue import GithubIssue
from openhands_resolver.issue_definitions import ( 
    IssueHandler, 
    IssueHandlerInterface, 
    PRHandler
)
from openhands_resolver.resolver_output import ResolverOutput
import openhands
from openhands.core.main import create_runtime, run_controller
from openhands.controller.state.state import State
from openhands.core.logger import openhands_logger as logger
from openhands.events.action import CmdRunAction, MessageAction
from openhands.events.observation import (
    CmdOutputObservation,
    ErrorObservation,
    Observation,
)
from openhands.core.config import (
    AppConfig,
    SandboxConfig,
)
from openhands.core.config import LLMConfig
from openhands.runtime.runtime import Runtime
from openhands_resolver.utils import (
    codeact_user_response,
    reset_logger_for_multiprocessing,
)

import firebase_admin
from firebase_admin import credentials, firestore

def issue_handler_factory(issue_type: str, owner: str, repo: str, token: str) -> IssueHandlerInterface:
    if issue_type == "issue":
        return IssueHandler(owner, repo, token)
    elif issue_type == "pr":
        return PRHandler(owner, repo, token)
    else:
        raise ValueError(f"Invalid issue type: {issue_type}")

def build_resolver_output (
    owner: str,
    repo: str,
    token: str,
    username: str,
    issue_type: str,
    issue_number: int,
    model: str,
) -> ResolverOutput:
    """_summary_

    Args:
        owner (str): _description_
        repo (str): _description_
        token (str): _description_
        username (str): _description_
        output_dir (str): _description_
        issue_type (str): _description_
        repo_instruction (str | None): _description_
        issue_number (int): _description_

    Returns:
        ResolverOutput: _description_
    """
    
    logger.info(f"1. Start building resolver output for {owner}/{repo}.")
    
    issue_handler = issue_handler_factory(issue_type, owner, repo, token)
    issues: list[GithubIssue] = issue_handler.get_converted_issues()
    issue = None
    for issue in issues:
        if issue.number == issue_number:
            break

    if issue is None:
        ValueError(f"Issue does not match. Issue Number: {issue_number}.")
    
    logger.info(f"Limiting resolving to issues {issue_number}.")
        
    issue = issues[0]
    
    output = ResolverOutput(
        issue=issue,
        issue_type=issue_handler.issue_type,
        instruction="instruction",
        base_commit="base_commit",
        git_patch="git_patch",
        history=[],
        metrics=None,
        success=1,
        comment_success=None,
        success_explanation="success_explanation",
        error=None,
        model=model,
    )
    return output


def send_to_firebase (
    resolved_output: ResolverOutput,
    output_dir: str,
    username: str,
    repo: str,
    issue_number: int,
    firebase_config: dict,
) -> None:
    """
    Send the resolver output to Firebase Firestore.

    Args:
        resolved_output (ResolverOutput): The resolved output to be sent.
        username (str): GitHub username.
        repo (str): GitHub repository name.
        issue_number (int): Issue number.
        firebase_config (dict): Firebase configuration.
    """
    logger.info(f"2. Write down the resolver to {output_dir}/... .")
    
    pathlib.Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    file_name = f"{username}-{repo}-{issue_number}.jsonl"
    output_file = pathlib.Path(output_dir) / file_name
    # output_file = os.path.join(output_dir, file_name)
    
    output_data = json.loads(resolved_output.model_dump_json())
    output_data.update({
        "username": username,
        "repo": repo,
        "issue_number": issue_number
    })
    
    with open(output_file, "a") as output_fp:
        output_fp.write(json.dumps(output_data) + "\n")
    
    logger.info("3. Sending jsonl file to firebase.")
    
    cred = credentials.Certificate(firebase_config)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    
    # Initialize Firestore client
    db = firestore.client()
    
    collection_name = f"{username}-{repo}-{issue_number}"
    collection_ref = db.collection(collection_name)
    collection_ref.add(output_data)

    logger.info(f"Data successfully uploaded to Firestore collection: {collection_name}")
    

def write_url_on_comment ():
    pass

def load_firebase_config(config_json: str) -> dict:
    """Load Firebase configuration from JSON string."""
    try:
        return json.loads(config_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid Firebase configuration JSON: {e}")

def main():

    parser = argparse.ArgumentParser(description="Resolve issues from Github.")
    parser.add_argument(
        "--repo",
        type=str,
        required=True,
        help="Github repository to resolve issues in form of `owner/repo`.",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Github token to access the repository.",
    )
    parser.add_argument(
        "--username",
        type=str,
        default=None,
        help="Github username to access the repository.",
    )
    parser.add_argument(
        "--agent-class",
        type=str,
        default="CodeActAgent",
        help="The agent class to use.",
    )
    parser.add_argument(
        "--issue-number",
        type=str,
        default=None,
        help="issue number to resolve.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Output directory to write the results.",
    )
    parser.add_argument(
        "--llm-models",
        type=str,
        default=None,
        help="LLM models to use.",
    )
    parser.add_argument(
        "--issue-type",
        type=str,
        default="issue",
        choices=["issue", "pr"],
        help="Type of issue to resolve, either open issue or pr comments.",
    )
    parser.add_argument(
    "--firebase-config",
    type=str,
    required=True,
    help="Firebase configuration in JSON format."
    )

    my_args = parser.parse_args()
    
    owner, repo = my_args.repo.split("/")
    token = (
        my_args.token if my_args.token else os.getenv("GITHUB_TOKEN")
    )
    username = (
        my_args.username
        if my_args.username
        else os.getenv("GITHUB_USERNAME")
    )
    
    if not token:
        raise ValueError("Github token is required.")
    
    models = my_args.llm_models or os.environ["LLM_MODELS"]
    if models:
        model_names = [model.strip() for model in models.split(",")]
    else:
        raise ValueError("No LLM models provided in either the arguments or environment variables.")
    
    selected_llms = random.sample(model_names, 2)
    
    issue_type = my_args.issue_type
    
    resolver_output1 = build_resolver_output (
        owner=owner,
        repo=repo,
        token=token,
        username=username,
        issue_type=issue_type,
        issue_number=int(my_args.issue_number),
        model=selected_llms[0]
    )
    
    raw_config = my_args.firebase_config if my_args.firebase_config else os.getenv("FIREBASE_CONFIG")
    firebase_config = load_firebase_config(raw_config)
    logger.info(f"Firebase Config Loaded... {firebase_config}")
    
    send_to_firebase (
        resolved_output=resolver_output1,
        output_dir=my_args.output_dir,
        username=username,
        repo=repo,
        issue_number=int(my_args.issue_number),
        firebase_config=firebase_config
    )
    
if __name__ == "__main__":
    main()