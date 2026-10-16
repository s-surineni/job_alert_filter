# Job Alert Filter & Salary Ranker 📬💼

A lightweight, extensible command-line tool built in Python (standard library only) that parses job alert emails, filters for frontend developer opportunities, normalizes salaries, and displays them sorted by compensation (highest first).

---

## 🏗 Architecture & Phased Strategy

The tool follows a decoupled, progressive delivery model:

```
[ Ingestion Layer ] ──▶ [ Extraction / Parsing ] ──▶ [ Filter Layer ] ──▶ [ Reporter / Sorter ]
- LocalFileEmailSource     - Title & Company regex       - Frontend Matcher     - Salary Ranker
  (alerts.txt / .eml)      - Salary Normalizer           - Exclude Filter       - Terminal Table
- GmailApiEmailSource        ($150k, $75/hr -> /yr)      - Min-Salary Gate      - Markdown / JSON
  (Phase 2)
```

- **Phase 1 (MVP — Active):** Ingest raw emails from text files (`alerts.txt`), raw `.eml` files, or JSON dumps. Zero external dependencies or OAuth setup required.
- **Phase 2 (Automated — Ready):** Plug in the `GmailApiEmailSource` adapter to fetch unread alert emails automatically via Google's Gmail API.

---

## 🚀 Quick Start (Phase 1)

### 1. Requirements
- Python 3.10+ (Uses standard library only: `re`, `email`, `dataclasses`, `argparse`, `typing`).

### 2. Run with Sample Data
```bash
python3 job_alert_filter.py -i alerts.txt
```

### 3. Output Formats
```bash
# Formatted Terminal Table (Default)
python3 job_alert_filter.py -i alerts.txt

# Markdown Table (Great for pasting into notes or GitHub)
python3 job_alert_filter.py -i alerts.txt -f markdown

# Export directly to CSV
python3 job_alert_filter.py -i alerts.txt -f csv > ranked_jobs.csv

# JSON Output (For downstream automation)
python3 job_alert_filter.py -i alerts.txt -f json
```

### 4. Advanced Filtering Flags
```bash
# Filter only jobs offering at least $175,000 / year:
python3 job_alert_filter.py -i alerts.txt --min-salary 175000

# Include additional custom frontend keywords:
python3 job_alert_filter.py -i alerts.txt --include svelte webgl

# Bypass frontend filter to inspect all parsed jobs:
python3 job_alert_filter.py -i alerts.txt --all
```

---

## 📝 Input Options

`LocalFileEmailSource` accepts any of the following:

1. **Plain Text File (`alerts.txt`)**: Paste one or more emails separated by divider lines (`---` or `===`) or standard headers (`From:`, `Subject:`).
2. **Directory of `.eml` / `.txt` files**:
   ```bash
   python3 job_alert_filter.py -i /path/to/downloaded_emails/
   ```
3. **Piped Input (`stdin`)**:
   ```bash
   cat alerts.txt | python3 job_alert_filter.py -i -
   ```

---

## 🔌 Phase 2: Gmail API Integration Guide

When you're ready to automate email retrieval without copy-pasting, follow these steps to connect `GmailApiEmailSource`:

### Step 1: Install Google API Client
```bash
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

### Step 2: Enable Gmail API in Google Cloud Console
1. Create a project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the **Gmail API**.
3. Create an **OAuth 2.0 Client ID** (Desktop application) and download the credentials file as `credentials.json`.

### Step 3: Implement `fetch_emails()` in `GmailApiEmailSource`
The class `GmailApiEmailSource` in `job_alert_filter.py` already inherits from `EmailSource`. Implement its `fetch_emails` method as follows:

```python
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

class GmailApiEmailSource(EmailSource):
    def __init__(self, credentials_path: str = "credentials.json", query: str = 'label:INBOX "job alert"'):
        self.credentials_path = credentials_path
        self.query = query

    def fetch_emails(self) -> List[RawEmail]:
        creds = None
        if os.path.exists("token.json"):
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            with open("token.json", "w") as token:
                token.write(creds.to_json())

        service = build("gmail", "v1", credentials=creds)
        response = service.users().messages().list(userId="me", q=self.query, maxResults=20).execute()
        messages = response.get("messages", [])

        raw_emails = []
        for msg_meta in messages:
            msg = service.users().messages().get(userId="me", id=msg_meta["id"], format="full").execute()
            headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
            subject = headers.get("subject", "No Subject")
            sender = headers.get("from", "")
            date = headers.get("date", "")

            # Extract body
            body = ""
            payload = msg.get("payload", {})
            parts = payload.get("parts", [payload])
            for part in parts:
                if part.get("mimeType") in ["text/plain", "text/html"]:
                    data = part.get("body", {}).get("data", "")
                    if data:
                        body += base64.urlsafe_b64decode(data.encode("UTF-8")).decode("UTF-8", errors="ignore")
                        break

            raw_emails.append(RawEmail(
                subject=subject,
                body=body,
                sender=sender,
                date=date,
                source_identifier=f"gmail:{msg_meta['id']}"
            ))

        return raw_emails
```

Because `JobParser`, `JobFilter`, and `JobReporter` depend only on `EmailSource` and `RawEmail`, no downstream code needs to change!

---

## 🧪 Testing

Run the included unit test suite:
```bash
python3 -m unittest test_job_alert_filter.py
```
