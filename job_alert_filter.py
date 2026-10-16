#!/usr/bin/env python3
"""
Job Alert Filter & Salary Ranker CLI
------------------------------------
A modular, extensible tool for parsing email job alerts, filtering for frontend
developer positions, and ranking opportunities by compensation.

Features:
- Extensible Email Ingestion Layer (Local files, directories, .eml, or Gmail API stub)
- Regex-based Extraction of Job Title, Company, URLs, and Compensation
- Smart Salary Normalization (Annual ranges, shorthand $150k, hourly $75/hr to annual)
- Frontend Role Matching with customizable include/exclude keyword filters
- Multiple Output Formats: Terminal Table, Markdown, JSON, and CSV

Author: Google Antigravity Agent
License: MIT
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from email import policy
from email.parser import BytesParser, Parser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ==============================================================================
# 1. Data Models
# ==============================================================================

@dataclass
class JobAlert:
    """Represents an extracted and normalized job opportunity."""
    title: str
    company: str
    raw_salary: Optional[str]
    min_annual_salary: Optional[float]
    max_annual_salary: Optional[float]
    sort_key_salary: float
    url_or_contact: Optional[str]
    source_email_subject: str
    currency: str = "$"

    @property
    def display_salary(self) -> str:
        """User-friendly representation of salary."""
        if self.raw_salary and self.raw_salary.strip():
            return self.raw_salary.strip()
        return "Unlisted / Negotiable"

    @property
    def normalized_annual_summary(self) -> str:
        """Formatted normalized annual salary estimate."""
        if self.sort_key_salary <= 0:
            return "Unlisted"
        if self.min_annual_salary and self.max_annual_salary and self.min_annual_salary != self.max_annual_salary:
            return f"{self.currency}{self.min_annual_salary:,.0f} - {self.currency}{self.max_annual_salary:,.0f}/yr"
        elif self.max_annual_salary:
            return f"{self.currency}{self.max_annual_salary:,.0f}/yr"
        elif self.min_annual_salary:
            return f"{self.currency}{self.min_annual_salary:,.0f}/yr"
        return "Unlisted"


@dataclass
class RawEmail:
    """Represents raw email content ingested from any source."""
    subject: str
    body: str
    sender: Optional[str] = None
    date: Optional[str] = None
    source_identifier: str = ""


# ==============================================================================
# 2. Ingestion Layer (EmailSource Interface & Adapters)
# ==============================================================================

class EmailSource(ABC):
    """Abstract Base Class for all email ingestion sources."""

    @abstractmethod
    def fetch_emails(self) -> List[RawEmail]:
        """Fetch and return raw emails from the configured source."""
        pass


class LocalFileEmailSource(EmailSource):
    """
    Ingests emails from local sources:
    - A plain text file containing one or more emails separated by delimiters (---, ===)
    - A directory containing .txt, .eml, or .json files
    - A single .eml file
    - A single .json dump file
    - Stdin (if path is '-')
    """

    def __init__(self, target_path: str):
        self.target_path = target_path

    def fetch_emails(self) -> List[RawEmail]:
        if self.target_path == "-":
            content = sys.stdin.read()
            return self._parse_text_content(content, source="<stdin>")

        path = Path(self.target_path)
        if not path.exists():
            raise FileNotFoundError(f"Input path does not exist: {self.target_path}")

        if path.is_dir():
            return self._read_directory(path)
        elif path.suffix.lower() == ".eml":
            return [self._read_eml_file(path)]
        elif path.suffix.lower() == ".json":
            return self._read_json_file(path)
        else:
            # Default to text file parser
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return self._parse_text_content(content, source=path.name)

    def _read_directory(self, dir_path: Path) -> List[RawEmail]:
        emails: List[RawEmail] = []
        for file in sorted(dir_path.iterdir()):
            if file.is_file():
                try:
                    if file.suffix.lower() == ".eml":
                        emails.append(self._read_eml_file(file))
                    elif file.suffix.lower() == ".json":
                        emails.extend(self._read_json_file(file))
                    elif file.suffix.lower() in [".txt", ".log"]:
                        with open(file, "r", encoding="utf-8", errors="replace") as f:
                            emails.extend(self._parse_text_content(f.read(), source=file.name))
                except Exception as e:
                    print(f"[Warning] Failed to read {file.name}: {e}", file=sys.stderr)
        return emails

    def _read_eml_file(self, file_path: Path) -> RawEmail:
        with open(file_path, "rb") as f:
            msg = BytesParser(policy=policy.default).parse(f)
        
        subject = msg.get("Subject", "(No Subject)")
        sender = msg.get("From", "")
        date = msg.get("Date", "")
        
        body_part = msg.get_body(preferencelist=("plain", "html"))
        body = body_part.get_content() if body_part else ""
        
        # If html only, strip tags for clean text parsing
        if body_part and body_part.get_content_subtype() == "html":
            body = re.sub(r"<[^>]+>", " ", body)

        return RawEmail(
            subject=subject,
            body=body,
            sender=sender,
            date=date,
            source_identifier=file_path.name
        )

    def _read_json_file(self, file_path: Path) -> List[RawEmail]:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        emails = []
        items = data if isinstance(data, list) else [data]
        for idx, item in enumerate(items):
            emails.append(RawEmail(
                subject=item.get("subject", f"Alert #{idx+1}"),
                body=item.get("body", "") or item.get("content", ""),
                sender=item.get("from") or item.get("sender"),
                date=item.get("date"),
                source_identifier=f"{file_path.name}#{idx+1}"
            ))
        return emails

    def _parse_text_content(self, text: str, source: str) -> List[RawEmail]:
        """
        Splits multi-email text documents into individual RawEmail items.
        Handles both explicit divider lines (===, ---) and standard header boundaries.
        """
        delimiter_pattern = re.compile(r"^(?:={4,}|-{4,}|_{4,})\s*$", re.MULTILINE)
        raw_parts = [p.strip() for p in delimiter_pattern.split(text) if p.strip()]

        if len(raw_parts) <= 1:
            # Fallback: try splitting on From: or Subject: at start of line if multiple emails are stacked
            header_split = re.split(r"\n+(?=(?:From|Subject)\s*:[^\n]+)", text, flags=re.IGNORECASE)
            raw_parts = [p.strip() for p in header_split if p.strip()]

        # Coalesce header-only chunks with their following body chunk
        coalesced_chunks: List[str] = []
        i = 0
        while i < len(raw_parts):
            part = raw_parts[i]
            lines = [l.strip() for l in part.splitlines() if l.strip()]
            is_header_only = bool(lines) and all(
                re.match(r"^(?:From|Subject|Date|To|Cc|Reply-To|Message-ID|MIME-Version|Content-Type)\s*:", l, re.IGNORECASE)
                for l in lines
            )
            if is_header_only and (i + 1) < len(raw_parts):
                combined = f"{part}\n\n{raw_parts[i+1]}"
                coalesced_chunks.append(combined)
                i += 2
            else:
                coalesced_chunks.append(part)
                i += 1

        emails: List[RawEmail] = []
        for idx, chunk in enumerate(coalesced_chunks):
            subject_match = re.search(r"^Subject:\s*(.+)$", chunk, re.IGNORECASE | re.MULTILINE)
            from_match = re.search(r"^From:\s*(.+)$", chunk, re.IGNORECASE | re.MULTILINE)
            date_match = re.search(r"^Date:\s*(.+)$", chunk, re.IGNORECASE | re.MULTILINE)

            subject = subject_match.group(1).strip() if subject_match else f"Alert from {source} [{idx+1}]"
            sender = from_match.group(1).strip() if from_match else None
            date = date_match.group(1).strip() if date_match else None

            # Remove top header lines from the body text
            clean_body = re.sub(
                r"^(?:From|Subject|Date|To|Cc|Reply-To)\s*:[^\n\r]*[\r\n]*",
                "",
                chunk,
                flags=re.IGNORECASE | re.MULTILINE
            ).strip()

            emails.append(RawEmail(
                subject=subject,
                body=clean_body or chunk,
                sender=sender,
                date=date,
                source_identifier=f"{source}#{idx+1}"
            ))

        return emails


class GmailApiEmailSource(EmailSource):
    """
    Phase 2 Integration Stub:
    Connects to the official Google Gmail REST API via OAuth 2.0 to fetch
    unread job alert emails.
    """

    def __init__(self, credentials_path: str = "credentials.json", query: str = 'label:INBOX "job alert"'):
        self.credentials_path = credentials_path
        self.query = query

    def fetch_emails(self) -> List[RawEmail]:
        """
        To activate Gmail API:
        1. Enable Gmail API in Google Cloud Console.
        2. Download OAuth 2.0 Client Secret as credentials.json.
        3. Install dependencies: `pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib`
        """
        raise NotImplementedError(
            "Gmail API Source is ready for Phase 2.\n"
            "To use it:\n"
            "  1. Run: pip install google-api-python-client google-auth-oauthlib\n"
            "  2. Place credentials.json in the project directory\n"
            "  3. Instantiate GmailApiEmailSource(credentials_path='credentials.json')\n"
            "For Phase 1 MVP, use LocalFileEmailSource (alerts.txt)."
        )


# ==============================================================================
# 3. Extraction & Normalization Layer (JobParser)
# ==============================================================================

class JobParser:
    """Parses raw email text to extract structured JobAlert models."""

    # Regex patterns for field extraction
    TITLE_PATTERNS = [
        re.compile(r"(?:Job Title|Position|Role|Title)\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE),
        re.compile(r"^(?:Senior|Staff|Lead|Principal|Junior)?\s*(?:Frontend|Front-end|Front End|React|Vue|Angular|UI|Web)\s+(?:Engineer|Developer|Architect)[^\n\r]*$", re.IGNORECASE | re.MULTILINE),
    ]

    COMPANY_PATTERNS = [
        re.compile(r"(?:Company|Employer|Organization|Hiring Company)\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE),
        re.compile(r"(?:at|@)\s+([A-Z][A-Za-z0-9\s&,\.\-]{1,30}?)(?:\s+(?:is hiring|in\b|Location|\-|\())", re.MULTILINE),
    ]

    URL_PATTERNS = [
        re.compile(r"(?:Apply(?:\s*URL)?|Link|URL|Application|Contact)\s*[:\-]\s*(https?://[^\s<>\"']+)", re.IGNORECASE),
        re.compile(r"(https?://[^\s<>\"']+)", re.IGNORECASE),
    ]

    # Salary patterns
    # 1. Shorthand range: $130k - $160k, $130K - $160K, 130k-160k USD
    P_SHORTHAND_RANGE = re.compile(
        r"([$€£₹]|USD\s*)?\s*(\d{2,3})\s*[kK]\s*(?:-|to|–|—)\s*([$€£₹]|USD\s*)?\s*(\d{2,3})\s*[kK]",
        re.IGNORECASE
    )
    # 2. Full annual range: $120,000 - $160,000 / yr, $120000 - $160000
    P_FULL_ANNUAL_RANGE = re.compile(
        r"([$€£₹]|USD\s*)?\s*(\d{1,3}(?:,\d{3})+|\d{5,6})\s*(?:-|to|–|—)\s*([$€£₹]|USD\s*)?\s*(\d{1,3}(?:,\d{3})+|\d{5,6})\s*(?:(?:per|/|a)\s*(?:year|yr|annum)|annual|annually)?",
        re.IGNORECASE
    )
    # 3. Hourly range: $60 - $80/hr, $60-$80 / hour
    P_HOURLY_RANGE = re.compile(
        r"([$€£₹]|USD\s*)?\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:-|to|–|—)\s*([$€£₹]|USD\s*)?\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:/|\s*(?:per|/)\s*)(?:hour|hr)",
        re.IGNORECASE
    )
    # 4. Hourly single: $75/hr, $75 per hour
    P_HOURLY_SINGLE = re.compile(
        r"([$€£₹]|USD\s*)?\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:/|\s*(?:per|/)\s*)(?:hour|hr)",
        re.IGNORECASE
    )
    # 5. Full annual single: $150,000 / year or $150k / year
    P_ANNUAL_SINGLE = re.compile(
        r"([$€£₹]|USD\s*)?\s*(\d{1,3}(?:,\d{3})+|\d{2,3}\s*[kK]|\d{5,6})\s*(?:(?:per|/|a)\s*(?:year|yr|annum)|annual|annually)",
        re.IGNORECASE
    )
    # 6. Indian LPA: 25 - 35 LPA, 30 LPA
    P_LPA_RANGE = re.compile(
        r"(\d{1,2}(?:\.\d{1,2})?)\s*(?:-|to|–|—)\s*(\d{1,2}(?:\.\d{1,2})?)\s*(?:LPA|Lakhs?(?:\s*per\s*annum)?)",
        re.IGNORECASE
    )
    P_LPA_SINGLE = re.compile(
        r"(\d{1,2}(?:\.\d{1,2})?)\s*(?:LPA|Lakhs?(?:\s*per\s*annum)?)",
        re.IGNORECASE
    )

    def parse_email(self, email: RawEmail) -> List[JobAlert]:
        """
        Parses a RawEmail into one or more JobAlert objects.
        Handles both single-job emails and multi-job digests.
        """
        # Split body into job blocks if it's a digest (e.g. repeated "Job Title:" or bullet markers)
        blocks = self._split_into_job_blocks(email.body)
        
        results: List[JobAlert] = []
        for block in blocks:
            alert = self._parse_single_block(block, email)
            if alert:
                results.append(alert)

        return results

    def _split_into_job_blocks(self, body: str) -> List[str]:
        """Detects if body contains multiple job listings or a single listing."""
        # Check for repeated "Job Title:" or "Role:" or "Position:"
        marker = re.findall(r"^(?:Job Title|Role|Position)\s*:", body, re.IGNORECASE | re.MULTILINE)
        if len(marker) > 1:
            # Multi-job digest: split on the marker
            parts = re.split(r"(?=^(?:Job Title|Role|Position)\s*:)", body, flags=re.IGNORECASE | re.MULTILINE)
            return [p.strip() for p in parts if p.strip()]
        return [body]

    def _parse_single_block(self, block: str, email: RawEmail) -> Optional[JobAlert]:
        # 1. Title Extraction
        title = None
        for pattern in self.TITLE_PATTERNS:
            match = pattern.search(block)
            if match:
                title = match.group(1).strip()
                break

        if not title:
            # Fallback: inspect subject line
            subj_clean = re.sub(r"^(?:Fwd|Re|LinkedIn Job Alert|Indeed Job Alert|Alert)\s*:\s*", "", email.subject, flags=re.IGNORECASE)
            title_candidate = re.search(r"((?:Senior|Staff|Lead|Principal|Junior)?\s*(?:Frontend|Front-end|Front End|React|Vue|Angular|UI|Web)\s+(?:Engineer|Developer|Architect)[^,\-\(]*)", subj_clean, re.IGNORECASE)
            if title_candidate:
                title = title_candidate.group(1).strip()
            else:
                title = subj_clean.strip() or "Untitled Job Opening"

        # 2. Company Extraction
        company = None
        for pattern in self.COMPANY_PATTERNS:
            match = pattern.search(block)
            if match:
                company = match.group(1).strip()
                break

        if not company:
            # Try to find company from subject like "at Stripe" or "@ Vercel"
            comp_match = re.search(r"(?:at|@)\s+([A-Za-z0-9\s&]{2,25})", email.subject, re.IGNORECASE)
            company = comp_match.group(1).strip() if comp_match else "Company Unknown"

        # 3. URL / Application link
        url_match = None
        for pattern in self.URL_PATTERNS:
            m = pattern.search(block)
            if m:
                url_match = m.group(1).strip()
                break

        # 4. Salary Extraction & Normalization
        raw_salary, min_sal, max_sal, sort_key, currency = self._extract_salary(block)

        return JobAlert(
            title=title,
            company=company,
            raw_salary=raw_salary,
            min_annual_salary=min_sal,
            max_annual_salary=max_sal,
            sort_key_salary=sort_key,
            url_or_contact=url_match,
            source_email_subject=email.subject,
            currency=currency
        )

    def _extract_salary(self, text: str) -> Tuple[Optional[str], Optional[float], Optional[float], float, str]:
        """
        Extracts raw salary text and converts it into normalized annual min, max,
        and sort_key.
        Annual hours baseline for hourly rates: 2,000 hrs (40 hrs/wk * 50 wks).
        """
        # Look for explicit Salary / Compensation line first
        explicit_line = re.search(r"(?:Salary|Compensation|Pay|Rate)\s*[:\-]\s*([^\n\r]+)", text, re.IGNORECASE)
        candidate_text = explicit_line.group(1) if explicit_line else text

        # Check currency symbol
        curr = "$"
        if "€" in candidate_text:
            curr = "€"
        elif "£" in candidate_text:
            curr = "£"
        elif "₹" in candidate_text or "LPA" in candidate_text.upper():
            curr = "₹"

        # Check 1: Shorthand Range: $130k - $160k
        m = self.P_SHORTHAND_RANGE.search(candidate_text)
        if m:
            min_val = float(m.group(2)) * 1000.0
            max_val = float(m.group(4)) * 1000.0
            return (m.group(0).strip(), min_val, max_val, max_val, curr)

        # Check 2: Full Annual Range: $120,000 - $160,000
        m = self.P_FULL_ANNUAL_RANGE.search(candidate_text)
        if m:
            min_str = m.group(2).replace(",", "")
            max_str = m.group(4).replace(",", "")
            min_val = float(min_str)
            max_val = float(max_str)
            return (m.group(0).strip(), min_val, max_val, max_val, curr)

        # Check 3: Hourly Range: $60 - $80/hr
        m = self.P_HOURLY_RANGE.search(candidate_text)
        if m:
            min_val = float(m.group(2)) * 2000.0
            max_val = float(m.group(4)) * 2000.0
            return (f"{m.group(0).strip()} (~{curr}{min_val:,.0f}-{curr}{max_val:,.0f}/yr)", min_val, max_val, max_val, curr)

        # Check 4: Hourly Single: $75/hr
        m = self.P_HOURLY_SINGLE.search(candidate_text)
        if m:
            val = float(m.group(2)) * 2000.0
            return (f"{m.group(0).strip()} (~{curr}{val:,.0f}/yr)", val, val, val, curr)

        # Check 5: Annual Single: $150,000 / year or $150k
        m = self.P_ANNUAL_SINGLE.search(candidate_text)
        if m:
            num_str = m.group(2).replace(",", "").lower()
            if "k" in num_str:
                val = float(num_str.replace("k", "").strip()) * 1000.0
            else:
                val = float(num_str)
            return (m.group(0).strip(), val, val, val, curr)

        # Check 6: Indian LPA range (e.g. 25 - 35 LPA)
        m = self.P_LPA_RANGE.search(candidate_text)
        if m:
            min_val = float(m.group(1)) * 100000.0
            max_val = float(m.group(2)) * 100000.0
            return (m.group(0).strip(), min_val, max_val, max_val, "₹")

        # Check 7: Indian LPA single
        m = self.P_LPA_SINGLE.search(candidate_text)
        if m:
            val = float(m.group(1)) * 100000.0
            return (m.group(0).strip(), val, val, val, "₹")

        # Check for unlisted text in explicit line (e.g. "Competitive", "Based on experience")
        if explicit_line:
            raw = explicit_line.group(1).strip()
            return (raw, None, None, 0.0, curr)

        return (None, None, None, 0.0, curr)


# ==============================================================================
# 4. Filter Layer (JobFilter)
# ==============================================================================

class JobFilter:
    """Filters JobAlert opportunities based on role requirements and keywords."""

    DEFAULT_INCLUDE_KEYWORDS = [
        "frontend", "front-end", "front end",
        "react", "reactjs", "react.js",
        "vue", "vuejs", "vue.js",
        "angular", "svelte", "next.js", "nextjs", "nuxt",
        "ui engineer", "ui developer", "ui/ux engineer", "web developer",
        "javascript developer", "typescript developer", "client-side"
    ]

    DEFAULT_EXCLUDE_KEYWORDS = [
        "backend", "back-end", "back end",
        "devops", "sre", "data engineer", "data scientist",
        "machine learning", "ml engineer", "embedded", "firmware",
        "database administrator", "dba", "salesforce developer"
    ]

    def __init__(
        self,
        include_keywords: Optional[List[str]] = None,
        exclude_keywords: Optional[List[str]] = None,
        min_salary_threshold: Optional[float] = None
    ):
        self.include_keywords = [k.lower() for k in (include_keywords or self.DEFAULT_INCLUDE_KEYWORDS)]
        self.exclude_keywords = [k.lower() for k in (exclude_keywords or self.DEFAULT_EXCLUDE_KEYWORDS)]
        self.min_salary_threshold = min_salary_threshold

    def is_match(self, job: JobAlert) -> Tuple[bool, str]:
        """
        Determines whether a job alert matches frontend criteria.
        Returns: (is_matched: bool, reason: str)
        """
        search_corpus = f"{job.title} {job.source_email_subject}".lower()

        # 1. Salary threshold filter
        if self.min_salary_threshold is not None:
            if job.sort_key_salary > 0 and job.sort_key_salary < self.min_salary_threshold:
                return False, f"Salary below threshold (${self.min_salary_threshold:,.0f})"

        # 2. Check for positive frontend indicators
        matched_include = any(k in search_corpus for k in self.include_keywords)
        if not matched_include:
            return False, "Does not contain frontend target keywords"

        # 3. Check for exclude keywords (e.g. pure backend or devops)
        for excl in self.exclude_keywords:
            if excl in search_corpus:
                # If both include & exclude exist, only exclude if title clearly emphasizes non-frontend
                # e.g., "Senior Backend Engineer (some React)" -> Exclude
                # e.g., "Fullstack Engineer (React/Node)" -> Allow if frontend-prominent
                if excl in job.title.lower() and not any(k in job.title.lower() for k in ["frontend", "front-end", "ui"]):
                    return False, f"Contains excluded keyword: '{excl}'"

        return True, "Matched frontend criteria"


# ==============================================================================
# 5. Output Presenter Layer (JobReporter)
# ==============================================================================

class JobReporter:
    """Formats and prints the sorted list of JobAlert opportunities."""

    @staticmethod
    def rank_and_sort(jobs: List[JobAlert]) -> List[JobAlert]:
        """Sorts jobs by highest compensation first; unlisted at the end."""
        return sorted(jobs, key=lambda j: j.sort_key_salary, reverse=True)

    @classmethod
    def render(cls, jobs: List[JobAlert], output_format: str = "table") -> str:
        sorted_jobs = cls.rank_and_sort(jobs)

        if output_format == "json":
            return json.dumps([asdict(j) for j in sorted_jobs], indent=2)

        elif output_format == "markdown":
            return cls._render_markdown(sorted_jobs)

        elif output_format == "csv":
            return cls._render_csv(sorted_jobs)

        else:
            return cls._render_terminal_table(sorted_jobs)

    @staticmethod
    def _render_terminal_table(jobs: List[JobAlert]) -> str:
        if not jobs:
            return "No matching jobs found."

        # Compute column widths
        col_rank = 4
        col_title = max(18, min(36, max(len(j.title) for j in jobs)))
        col_company = max(12, min(24, max(len(j.company) for j in jobs)))
        col_salary = max(16, min(30, max(len(j.normalized_annual_summary) for j in jobs)))
        col_link = 30

        lines: List[str] = []
        border_top = f"┌{'─' * (col_rank + 2)}┬{'─' * (col_title + 2)}┬{'─' * (col_company + 2)}┬{'─' * (col_salary + 2)}┬{'─' * (col_link + 2)}┐"
        border_mid = f"├{'─' * (col_rank + 2)}┼{'─' * (col_title + 2)}┼{'─' * (col_company + 2)}┼{'─' * (col_salary + 2)}┼{'─' * (col_link + 2)}┤"
        border_bot = f"└{'─' * (col_rank + 2)}┴{'─' * (col_title + 2)}┴{'─' * (col_company + 2)}┴{'─' * (col_salary + 2)}┴{'─' * (col_link + 2)}┘"

        lines.append(border_top)
        header = (
            f"│ {'#':<{col_rank}} "
            f"│ {'Job Title':<{col_title}} "
            f"│ {'Company':<{col_company}} "
            f"│ {'Est. Annual CTC':<{col_salary}} "
            f"│ {'Application URL':<{col_link}} │"
        )
        lines.append(header)
        lines.append(border_mid)

        for idx, job in enumerate(jobs, 1):
            title = (job.title[:col_title - 3] + "...") if len(job.title) > col_title else job.title
            company = (job.company[:col_company - 3] + "...") if len(job.company) > col_company else job.company
            sal = job.normalized_annual_summary
            sal_disp = (sal[:col_salary - 3] + "...") if len(sal) > col_salary else sal
            raw_url = job.url_or_contact or "N/A"
            url = (raw_url[:col_link - 3] + "...") if len(raw_url) > col_link else raw_url

            row = (
                f"│ {idx:<{col_rank}} "
                f"│ {title:<{col_title}} "
                f"│ {company:<{col_company}} "
                f"│ {sal_disp:<{col_salary}} "
                f"│ {url:<{col_link}} │"
            )
            lines.append(row)

        lines.append(border_bot)
        return "\n".join(lines)

    @staticmethod
    def _render_markdown(jobs: List[JobAlert]) -> str:
        lines = [
            "| Rank | Job Title | Company | Estimated Annual Salary | Raw Salary | Link |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |"
        ]
        for idx, j in enumerate(jobs, 1):
            url_str = f"[Apply Here]({j.url_or_contact})" if j.url_or_contact else "N/A"
            lines.append(
                f"| {idx} | **{j.title}** | {j.company} | `{j.normalized_annual_summary}` | {j.display_salary} | {url_str} |"
            )
        return "\n".join(lines)

    @staticmethod
    def _render_csv(jobs: List[JobAlert]) -> str:
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["rank", "title", "company", "normalized_annual_salary", "min_annual", "max_annual", "sort_key", "raw_salary", "url", "subject"])
        for idx, j in enumerate(jobs, 1):
            writer.writerow([
                idx, j.title, j.company, j.normalized_annual_summary,
                j.min_annual_salary, j.max_annual_salary, j.sort_key_salary,
                j.raw_salary, j.url_or_contact, j.source_email_subject
            ])
        return output.getvalue()


# ==============================================================================
# 6. Orchestration & CLI
# ==============================================================================

def run_pipeline(
    input_source: EmailSource,
    job_filter: JobFilter,
    output_format: str = "table",
    show_all: bool = False
) -> int:
    """Executes ingestion -> parsing -> filtering -> sorting -> reporting."""
    print("📥 Ingesting emails...", file=sys.stderr)
    emails = input_source.fetch_emails()
    print(f"   ✓ Ingested {len(emails)} raw email(s)", file=sys.stderr)

    parser = JobParser()
    all_jobs: List[JobAlert] = []
    for email in emails:
        alerts = parser.parse_email(email)
        all_jobs.extend(alerts)

    print(f"   ✓ Extracted {len(all_jobs)} job opening(s)", file=sys.stderr)

    if show_all:
        matched_jobs = all_jobs
        print("   ℹ Filter bypassed (--all flag active)", file=sys.stderr)
    else:
        matched_jobs = []
        discarded_count = 0
        for job in all_jobs:
            is_match, reason = job_filter.is_match(job)
            if is_match:
                matched_jobs.append(job)
            else:
                discarded_count += 1
        print(f"   ✓ Filtered to {len(matched_jobs)} frontend opening(s) ({discarded_count} discarded)", file=sys.stderr)

    # Render results
    output = JobReporter.render(matched_jobs, output_format=output_format)
    print("\n" + output)

    # Summary
    if output_format == "table" and matched_jobs:
        sorted_jobs = JobReporter.rank_and_sort(matched_jobs)
        top_sal = sorted_jobs[0].normalized_annual_summary if sorted_jobs[0].sort_key_salary > 0 else "N/A"
        print(f"\n📊 Summary: {len(matched_jobs)} frontend positions found. Highest: {top_sal} ({sorted_jobs[0].company})", file=sys.stderr)

    return 0


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Filter and rank email job alerts for frontend developer positions by salary.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run against sample alerts.txt (Phase 1 MVP):
  python3 job_alert_filter.py -i alerts.txt

  # Output as Markdown table:
  python3 job_alert_filter.py -i alerts.txt --format markdown

  # Export rankings to CSV:
  python3 job_alert_filter.py -i alerts.txt --format csv > ranked_jobs.csv

  # Filter only jobs paying at least $160,000 / year:
  python3 job_alert_filter.py -i alerts.txt --min-salary 160000

  # Read a folder of .eml files:
  python3 job_alert_filter.py -i /path/to/eml_folder/
        """
    )
    parser.add_argument(
        "-i", "--input",
        default="alerts.txt",
        help="Path to alerts file (.txt, .eml, .json), directory of emails, or '-' for stdin (default: alerts.txt)"
    )
    parser.add_argument(
        "-f", "--format",
        choices=["table", "markdown", "json", "csv"],
        default="table",
        help="Output presentation format (default: table)"
    )
    parser.add_argument(
        "--min-salary",
        type=float,
        default=None,
        help="Minimum normalized annual salary threshold to include (e.g. 150000)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show all parsed jobs without frontend filtering (useful for inspecting raw alert streams)"
    )
    parser.add_argument(
        "--include",
        nargs="+",
        help="Additional custom keywords to match for frontend roles"
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        help="Custom keywords to exclude"
    )
    return parser


def main() -> int:
    parser = build_cli()
    args = parser.parse_args()

    try:
        source = LocalFileEmailSource(args.input)
    except Exception as e:
        print(f"[Error] Failed to initialize source '{args.input}': {e}", file=sys.stderr)
        return 1

    custom_include = None
    if args.include:
        custom_include = JobFilter.DEFAULT_INCLUDE_KEYWORDS + args.include

    custom_exclude = None
    if args.exclude:
        custom_exclude = JobFilter.DEFAULT_EXCLUDE_KEYWORDS + args.exclude

    job_filter = JobFilter(
        include_keywords=custom_include,
        exclude_keywords=custom_exclude,
        min_salary_threshold=args.min_salary
    )

    return run_pipeline(
        input_source=source,
        job_filter=job_filter,
        output_format=args.format,
        show_all=args.all
    )


if __name__ == "__main__":
    sys.exit(main())
