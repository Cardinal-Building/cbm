#!/usr/bin/env python3
"""Create standing Gmail filters and backfill them onto existing inbox mail.

One-time setup (Google Cloud project, OAuth client, credentials.json) is
documented in README.md. This script does not run itself automatically --
review it, then run:

    python setup_filters.py --dry-run     # preview only, no account changes
    python setup_filters.py               # create filters + backfill (asks first)
    python setup_filters.py --yes         # same, no confirmation prompt

Other flags: --skip-backfill, --skip-filters, --credentials, --token
"""

import argparse
import os
import sys
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    # Create/list Gmail filters (users.settings.filters.*)
    "https://www.googleapis.com/auth/gmail.settings.basic",
    # List/read messages and modify their labels, including removing INBOX
    # (archiving) and adding STARRED
    "https://www.googleapis.com/auth/gmail.modify",
    # Create/list custom labels
    "https://www.googleapis.com/auth/gmail.labels",
]

DEFAULT_CREDENTIALS_FILE = "credentials.json"
DEFAULT_TOKEN_FILE = "token.json"

# Each entry describes one standing filter AND the backfill that mirrors it.
# `query` uses normal Gmail search syntax. `archive` == "skip inbox" in the
# Gmail UI (removes the INBOX label). `star` adds the STARRED label.
FILTERS = [
    {
        "name": "Promotions",
        "query": (
            "category:promotions OR from:(mcmap.chase.com) OR "
            "from:(mailreply.sleepnumber.com) OR from:(email.microsoft.com) OR "
            "from:(email.homes.com) OR from:(content.breakthrought1d.org)"
        ),
        "label": "Promotions",
        "star": False,
        "archive": True,
    },
    {
        "name": "Priority",
        "query": (
            "-from:(noreply OR no-reply OR donotreply OR do-not-reply OR "
            "notifications OR notification OR alerts OR digest OR newsletter OR "
            "mailer OR news) -category:(promotions OR social OR forums) -list:*"
        ),
        "label": "Priority",
        "star": True,
        "archive": False,
    },
    {
        "name": "Faxes",
        "query": 'subject:"NEW FAX"',
        "label": "Faxes",
        "star": False,
        "archive": True,
    },
    {
        "name": "AP Records",
        "query": (
            'from:(accountspayable@cardinal-building.com) '
            'subject:(E-Receipt OR "Exception Report")'
        ),
        "label": "AP Records",
        "star": False,
        "archive": True,
    },
    {
        "name": "System Logs",
        "query": (
            'subject:("Promise Date Change" OR "force ship") OR '
            "from:(automation@cardinal-building.com)"
        ),
        "label": "System Logs",
        "star": False,
        "archive": True,
    },
    {
        "name": "Prospect Forwards",
        "query": (
            'from:(cardinal-building.com) (subject:"FW:" OR subject:"Fwd:") '
            '-subject:("Promise Date Change" OR "force ship")'
        ),
        "label": "Prospect Forwards",
        "star": True,
        "archive": False,
    },
]

BATCH_MODIFY_LIMIT = 1000  # Gmail API max ids per batchModify call
LIST_PAGE_SIZE = 500  # Gmail API max maxResults per messages.list call


def get_credentials(credentials_file, token_file):
    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_file):
                sys.exit(
                    f"Missing {credentials_file}. Follow README.md to download "
                    "your OAuth client secret from Google Cloud Console first."
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_file, "w") as f:
            f.write(creds.to_json())
    return creds


def with_backoff(request_fn, max_retries=5):
    """Call request_fn() (a zero-arg callable that performs one API call),
    retrying on transient/rate-limit errors with exponential backoff."""
    delay = 1
    for attempt in range(max_retries):
        try:
            return request_fn()
        except HttpError as e:
            if e.resp.status in (403, 429, 500, 503) and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def get_or_create_label(service, name, dry_run):
    resp = with_backoff(lambda: service.users().labels().list(userId="me").execute())
    for label in resp.get("labels", []):
        if label["name"] == name:
            return label["id"], False
    if dry_run:
        return None, True
    created = with_backoff(
        lambda: service.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    return created["id"], True


def existing_filter_queries(service):
    resp = with_backoff(
        lambda: service.users().settings().filters().list(userId="me").execute()
    )
    return {f["criteria"].get("query") for f in resp.get("filter", []) if "criteria" in f}


def build_action(label_id, star, archive):
    add_labels = []
    if label_id:
        add_labels.append(label_id)
    if star:
        add_labels.append("STARRED")
    action = {}
    if add_labels:
        action["addLabelIds"] = add_labels
    if archive:
        action["removeLabelIds"] = ["INBOX"]
    return action


def create_filter(service, filter_def, label_id, already_present):
    if already_present:
        print(f"  [skip] filter already exists for query: {filter_def['query']!r}")
        return
    body = {
        "criteria": {"query": filter_def["query"]},
        "action": build_action(label_id, filter_def["star"], filter_def["archive"]),
    }
    with_backoff(
        lambda: service.users().settings().filters().create(userId="me", body=body).execute()
    )
    print(f"  [created] filter -> label '{filter_def['label']}'")


def list_matching_message_ids(service, query):
    ids = []
    page_token = None
    while True:
        resp = with_backoff(
            lambda: service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=LIST_PAGE_SIZE,
                pageToken=page_token,
                fields="messages/id,nextPageToken",
            )
            .execute()
        )
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids


def backfill_filter(service, filter_def, label_id, dry_run):
    # Scope the backfill to mail currently sitting in the inbox, per the
    # "retroactively clean up existing inbox mail" requirement.
    query = f"in:inbox {filter_def['query']}"
    ids = list_matching_message_ids(service, query)
    print(f"  {len(ids)} matching message(s) currently in inbox")
    if dry_run or not ids:
        return
    action = build_action(label_id, filter_def["star"], filter_def["archive"])
    for i, chunk in enumerate(chunked(ids, BATCH_MODIFY_LIMIT)):
        with_backoff(
            lambda chunk=chunk: service.users()
            .messages()
            .batchModify(userId="me", body={"ids": chunk, **action})
            .execute()
        )
        print(f"  applied to {len(chunk)} message(s) (batch {i + 1})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="preview only, make no changes")
    parser.add_argument("--yes", "-y", action="store_true", help="skip confirmation prompt")
    parser.add_argument("--skip-backfill", action="store_true", help="only create standing filters")
    parser.add_argument("--skip-filters", action="store_true", help="only backfill, skip filter creation")
    parser.add_argument("--credentials", default=DEFAULT_CREDENTIALS_FILE)
    parser.add_argument("--token", default=DEFAULT_TOKEN_FILE)
    args = parser.parse_args()

    creds = get_credentials(args.credentials, args.token)
    service = build("gmail", "v1", credentials=creds)

    existing_queries = existing_filter_queries(service) if not args.skip_filters else set()

    print("Plan:")
    for f in FILTERS:
        exists = f["query"] in existing_queries
        print(f"- {f['name']}: filter {'already exists' if exists else 'will be created'}")
    print()

    if not args.dry_run and not args.yes:
        answer = input("Proceed with creating filters and backfilling matching inbox mail? [y/N] ")
        if answer.strip().lower() != "y":
            print("Aborted, no changes made.")
            return

    for f in FILTERS:
        print(f"\n== {f['name']} ==")
        label_id, label_created = get_or_create_label(service, f["label"], args.dry_run)
        if label_created:
            verb = "would create" if args.dry_run else "created"
            print(f"  label '{f['label']}' {verb}")

        if not args.skip_filters:
            already_present = f["query"] in existing_queries
            if args.dry_run:
                print(
                    f"  would {'skip (exists)' if already_present else 'create'} filter "
                    f"for query: {f['query']!r}"
                )
            else:
                create_filter(service, f, label_id, already_present)

        if not args.skip_backfill:
            backfill_filter(service, f, label_id, args.dry_run)

    print("\nDone." if not args.dry_run else "\nDry run complete, no changes made.")


if __name__ == "__main__":
    main()
