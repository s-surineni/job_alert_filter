#!/usr/bin/env python3
"""
Gmail Message Downloader for Job Alert Filter
--------------------------------------------
Downloads emails from Gmail using the official Gmail API (OAuth 2.0)
and saves them as standard .eml files to be ingested by job_alert_filter.py.

Usage:
    .venv/bin/python download_emails.py [--days 1] [--query 'newer_than:1d'] [--output emails]
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import List, Optional

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    print(
        "[Error] Google API client libraries not found.\n"
        "Please run:\n"
        "  .venv/bin/pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib\n"
        "or run this script using .venv/bin/python download_emails.py",
        file=sys.stderr
    )
    sys.exit(1)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_gmail_service(credentials_path: str = "credentials.json", token_path: str = "token.json"):
    """Authenticates the user and returns the Gmail API service instance."""
    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception as e:
            print(f"[Warning] Failed to load existing {token_path}: {e}", file=sys.stderr)
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("🔄 Refreshing expired credentials...")
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(
                    f"Credentials file '{credentials_path}' not found! "
                    "Please place your OAuth 2.0 client secret file as 'credentials.json'."
                )
            print("🔑 Initiating OAuth 2.0 authorization...", flush=True)
            print("A browser window should open automatically. If not, open the URL displayed below:", flush=True)
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)

        # Save credentials for future runs
        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())
        print(f"✓ Saved credentials to {token_path}")

    return build("gmail", "v1", credentials=creds)


def download_emails(
    service,
    query: str = "newer_than:1d",
    output_dir: str = "emails",
    max_results: int = 50
) -> List[Path]:
    """Downloads messages matching query and writes them as .eml files."""
    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)

    print(f"🔍 Searching Gmail for: '{query}' (up to {max_results} messages)...")
    res = service.users().messages().list(userId="me", q=query, maxResults=min(max_results, 100)).execute()
    messages = res.get("messages", [])

    if not messages:
        print("ℹ️ No emails found matching the query.")
        return []

    print(f"📥 Found {len(messages)} email(s). Downloading...")
    downloaded_files: List[Path] = []

    for idx, msg_meta in enumerate(messages, start=1):
        msg_id = msg_meta["id"]
        # Fetch raw RFC 2822 message bytes
        msg_data = service.users().messages().get(userId="me", id=msg_id, format="raw").execute()
        raw_bytes = base64.urlsafe_b64decode(msg_data["raw"].encode("ASCII"))

        # Parse basic metadata for logging
        parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)
        subject = parsed.get("Subject", "(No Subject)")
        sender = parsed.get("From", "(Unknown Sender)")
        date = parsed.get("Date", "")

        filename = f"{msg_id}.eml"
        filepath = dest / filename
        with open(filepath, "wb") as f:
            f.write(raw_bytes)

        downloaded_files.append(filepath)
        print(f"  [{idx}/{len(messages)}] Saved: {filepath.name}")
        print(f"      Subject: {subject[:70]}")
        print(f"      From:    {sender[:70]}")
        if date:
            print(f"      Date:    {date}")

    print(f"\n✅ Successfully downloaded {len(downloaded_files)} email(s) to '{output_dir}/'")
    return downloaded_files


def main():
    parser = argparse.ArgumentParser(description="Download emails from Gmail as .eml files.")
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="Number of past days to download emails for (default: 1)"
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Custom Gmail search query (overrides --days). Default: 'newer_than:<days>d'"
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default="emails",
        help="Directory to save .eml files (default: emails)"
    )
    parser.add_argument(
        "--credentials",
        type=str,
        default="credentials.json",
        help="Path to client secret credentials.json (default: credentials.json)"
    )
    parser.add_argument(
        "--token",
        type=str,
        default="token.json",
        help="Path to saved token.json (default: token.json)"
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=50,
        help="Maximum number of emails to fetch (default: 50)"
    )
    args = parser.parse_args()

    query = args.query if args.query else f"newer_than:{args.days}d"

    try:
        service = get_gmail_service(credentials_path=args.credentials, token_path=args.token)
        download_emails(
            service=service,
            query=query,
            output_dir=args.output,
            max_results=args.max_results
        )
    except Exception as e:
        print(f"\n[Error] {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
