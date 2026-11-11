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
import html
import json
import os
import re
import sys
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
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
    company_rank: int = 99
    position_rank: int = 99
    matched_position: Optional[str] = None
    target_position: Optional[str] = None
    target_salary: Optional[str] = None
    rating: Optional[float] = None
    number_of_ratings: Optional[int] = None
    company_alumni: Optional[int] = None

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

    @property
    def display_company_rank(self) -> str:
        if self.company_rank < 99:
            return f"Tier {self.company_rank}"
        return "-"

    @property
    def display_position_rank(self) -> str:
        if self.position_rank < 99:
            return f"#{self.position_rank}"
        return "-"

    @property
    def display_target_position(self) -> str:
        if self.target_position and self.target_position.strip():
            return self.target_position.strip()
        return "-"

    @property
    def display_rating(self) -> str:
        """User-friendly display for rating and review count."""
        if self.rating is not None:
            if self.number_of_ratings is not None and self.number_of_ratings > 0:
                if self.number_of_ratings >= 1000:
                    cnt_str = f"{self.number_of_ratings / 1000:.1f}k"
                else:
                    cnt_str = str(self.number_of_ratings)
                return f"★ {self.rating:.1f} ({cnt_str})"
            return f"★ {self.rating:.1f}"
        return "-"

    @property
    def display_company_alumni(self) -> str:
        """User-friendly display for number of company alumni."""
        if self.company_alumni is not None and self.company_alumni > 0:
            if self.company_alumni == 1:
                return "1 alum"
            return f"{self.company_alumni:,} alumni"
        return "-"


@dataclass
class CompanyPreference:
    name: str = ""
    position: Optional[str] = None
    rank: int = 99
    target_salary: Optional[str] = None
    rating: Optional[float] = None
    number_of_ratings: Optional[int] = None
    company_alumni: Optional[int] = None


@dataclass
class PositionPreference:
    rank: int = 99
    target_salary: Optional[str] = None


@dataclass
class RankingConfig:
    """Stores company and position rankings loaded from CSV."""
    companies: Dict[str, CompanyPreference] = field(default_factory=dict)
    preferences: List[CompanyPreference] = field(default_factory=list)
    positions: Dict[str, PositionPreference] = field(default_factory=dict)
    default_company_rank: int = 99
    default_position_rank: int = 99

    @classmethod
    def load_from_file(cls, filepath: str | Path) -> "RankingConfig":
        path = Path(filepath)
        if not path.is_file():
            raise FileNotFoundError(f"Rankings file not found: {filepath}")
        return cls._load_from_csv(path)

    @classmethod
    def _load_from_csv(cls, path: Path) -> "RankingConfig":
        companies: Dict[str, CompanyPreference] = {}
        preferences: List[CompanyPreference] = []

        with open(path, "r", encoding="utf-8-sig") as f:
            lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

        if not lines:
            return cls(companies={}, preferences=[])

        reader = csv.reader(lines)
        header = [h.strip().lower() for h in next(reader, [])]

        comp_idx: Optional[int] = None
        for idx, h in enumerate(header):
            if h == "company":
                comp_idx = idx
                break

        if comp_idx is None:
            raise ValueError(f"Rankings CSV '{path}' must contain a 'company' column.")

        def find_col(aliases: List[str]) -> Optional[int]:
            for alias in aliases:
                for idx, h in enumerate(header):
                    if alias in h and idx != comp_idx:
                        return idx
            return None

        pos_idx = find_col(["position", "role", "title"])
        rank_idx = find_col(["rank", "tier"])
        sal_idx = find_col(["salary", "comp", "pay"])
        rating_idx = find_col(["rating", "stars", "score"])
        num_ratings_idx = find_col(["number_of_ratings", "num_ratings", "reviews", "ratings", "count"])
        alumni_idx = find_col(["company_alumni", "alumni", "num_alumni"])

        for row in reader:
            if len(row) <= comp_idx:
                continue
            company = row[comp_idx].strip()
            if not company:
                continue

            pos = (
                row[pos_idx].strip()
                if pos_idx is not None and len(row) > pos_idx and row[pos_idx].strip()
                else None
            )

            rank = 99
            if rank_idx is not None and len(row) > rank_idx and row[rank_idx].strip():
                try:
                    rank = int(row[rank_idx].strip())
                except ValueError:
                    rank = 99

            sal = (
                row[sal_idx].strip()
                if sal_idx is not None and len(row) > sal_idx and row[sal_idx].strip()
                else None
            )

            rating = None
            if rating_idx is not None and len(row) > rating_idx and row[rating_idx].strip():
                try:
                    rating = float(row[rating_idx].strip())
                except ValueError:
                    rating = None

            num_rat = None
            if num_ratings_idx is not None and len(row) > num_ratings_idx and row[num_ratings_idx].strip():
                try:
                    clean_num = re.sub(r"[^\d]", "", row[num_ratings_idx].strip())
                    if clean_num:
                        num_rat = int(clean_num)
                except ValueError:
                    num_rat = None

            num_alumni = None
            if alumni_idx is not None and len(row) > alumni_idx and row[alumni_idx].strip():
                try:
                    clean_alumni = re.sub(r"[^\d]", "", row[alumni_idx].strip())
                    if clean_alumni:
                        num_alumni = int(clean_alumni)
                except ValueError:
                    num_alumni = None

            pref = CompanyPreference(
                name=company,
                position=pos,
                rank=rank,
                target_salary=sal,
                rating=rating,
                number_of_ratings=num_rat,
                company_alumni=num_alumni
            )
            preferences.append(pref)
            if company not in companies:
                companies[company] = pref

        return cls(companies=companies, preferences=preferences)

    def apply_to_job(self, job: JobAlert) -> None:
        """Matches company against rankings and updates job ranks and target position in-place."""
        comp_str = job.company.lower().strip()
        comp_words = set(re.findall(r"\b\w+\b", comp_str))
        matched_pref: Optional[CompanyPreference] = None

        items = self.preferences if self.preferences else [
            CompanyPreference(
                name=k, position=v.position, rank=v.rank, target_salary=v.target_salary,
                rating=v.rating, number_of_ratings=v.number_of_ratings, company_alumni=v.company_alumni
            )
            for k, v in self.companies.items()
        ]

        # First matching company from CSV
        for pref in items:
            comp_name = pref.name or ""
            c_key = comp_name.lower().strip()
            c_words = set(re.findall(r"\b\w+\b", c_key))
            matched = False

            if c_key == comp_str:
                matched = True
            elif len(c_key) > 3 and (c_key in comp_str or comp_str in c_key):
                matched = True
            elif c_words and c_words.issubset(comp_words):
                matched = True
            elif comp_words and comp_words.issubset(c_words):
                matched = True

            if matched:
                matched_pref = pref
                break

        if matched_pref:
            job.company_rank = matched_pref.rank
            job.rating = matched_pref.rating
            job.number_of_ratings = matched_pref.number_of_ratings
            job.target_position = matched_pref.position
            job.matched_position = matched_pref.position

            if matched_pref.company_alumni is not None and job.company_alumni is None:
                job.company_alumni = matched_pref.company_alumni

            # Target salary fallback:
            # If position is specified, apply if alert title matches; if no position specified, apply to company.
            if matched_pref.target_salary:
                if not matched_pref.position:
                    job.target_salary = matched_pref.target_salary
                else:
                    title_str = job.title.lower().strip()
                    pos_key = matched_pref.position.lower().strip()
                    pos_words = [w for w in re.findall(r"\b\w+\b", pos_key) if len(w) > 2]
                    if pos_key in title_str or (pos_words and all(w in title_str for w in pos_words)):
                        job.target_salary = matched_pref.target_salary
        else:
            job.company_rank = self.default_company_rank

        job.position_rank = self.default_position_rank


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
        
        # If html only, extract hyperlinks and strip tags for clean text parsing
        if body_part and body_part.get_content_subtype() == "html":
            # Preserve target URLs from anchor tags: <a href="url">text</a> -> text (url)
            body = re.sub(
                r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                r'\2 (\1)',
                body,
                flags=re.IGNORECASE | re.DOTALL
            )
            body = re.sub(r"<[^>]+>", " ", body)

        return RawEmail(
            subject=subject,
            body=body,
            sender=sender,
            date=date,
            source_identifier=file_path.name
        )

    def _read_json_file(self, file_path: Path) -> List[RawEmail]:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []

        emails = []
        items = data if isinstance(data, list) else [data]
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            body = item.get("body") or item.get("content") or ""
            has_subject = bool(item.get("subject"))
            has_sender = bool(item.get("from") or item.get("sender"))
            # Require at least an email body or both subject and sender to qualify as an email
            if not body and not (has_subject and has_sender):
                continue
            emails.append(RawEmail(
                subject=item.get("subject", f"Alert #{idx+1}"),
                body=body,
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
        re.compile(r"^(?:Job Title|Position|Role|Title)\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^((?:Senior|Staff|Lead|Principal|Junior)?\s*(?:Software\s+)?(?:Frontend|Front-end|Front End|React|Vue|Angular|UI|Web)\s+(?:Engineer|Developer|Architect|Specialist)[^\n\r]*)$", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^((?:Senior|Staff|Lead|Principal|Junior)?\s*(?:Software\s+Engineer|Developer|Architect)\s*[-–—:]\s*(?:Frontend|Front-end|Front End|React|Vue|Angular|UI|Web)[^\n\r]*)$", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^((?:Senior|Staff|Lead|Principal|Junior)?\s*(?:Member of Technical Staff|MTS)\s*[-–—:]\s*[^\n\r]+)$", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^((?:Senior|Staff|Lead|Principal|Junior)?\s*(?:UI|Frontend|Front-end|React|Vue|Angular)[_\-](?:Engineer|Developer|Architect)[^\n\r]*)$", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^((?:React|Vue|Angular|Frontend|Front-End|UI)\s+Developer[^\n\r]*)$", re.IGNORECASE | re.MULTILINE),
    ]

    COMPANY_PATTERNS = [
        re.compile(r"^\s*(?:Company|Employer|Organization|Hiring Company)\s*[:\-]\s*([^\n\r]+)", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*Posted by\s+([^\n\r.]+)", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*(?:Hiring for|Client of)\s+([^\n\r.]+)", re.IGNORECASE | re.MULTILINE),
        re.compile(r"(?:at|@)\s+([A-Z][A-Za-z0-9\s&,\.\-]{1,30}?)(?:\s+(?:is hiring|in\b|Location|\-|\())", re.MULTILINE),
    ]

    URL_PATTERNS = [
        re.compile(r"(?:View\s*job|Apply(?:\s*URL)?|Link|URL|Application|Contact)\s*[:\-]\s*(https?://[^\s<>\"']+)", re.IGNORECASE),
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
    # 6. Indian LPA: 25 - 35 LPA, 80L - 90L/year, 30 LPA
    P_LPA_RANGE = re.compile(
        r"(?:[$€£₹]|USD\s*)?\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:[lL](?:akhs?|PA)?|LPA)\s*(?:-|to|–|—)\s*(?:[$€£₹]|USD\s*)?\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:[lL](?:akhs?|PA)?|LPA)(?:\s*(?:per|/|a)\s*(?:year|yr|annum))?",
        re.IGNORECASE
    )
    P_LPA_SINGLE = re.compile(
        r"(?:[$€£₹]|USD\s*)?\s*(\d{1,3}(?:\.\d{1,2})?)\s*(?:[lL]akhs?|LPA|[lL]\s*(?:per|/|a)\s*(?:year|yr|annum))",
        re.IGNORECASE
    )

    # 7. Company alumni: "4 company alumni", "1 company alum", "162 company alumni", "Company alumni: 12"
    P_COMPANY_ALUMNI = re.compile(
        r"(?:(\d[\d,]*)\s+company\s+alum(?:n[iusa]*|s)?\b|\bcompany\s+alum(?:n[iusa]*|s)?\s*[:\-]?\s*(\d[\d,]*)\b)",
        re.IGNORECASE
    )

    def parse_email(self, email: RawEmail) -> List[JobAlert]:
        """
        Parses a RawEmail into one or more JobAlert objects.
        Handles both single-job emails and multi-job digests.
        """
        blocks = self._split_into_job_blocks(email.body)
        
        results: List[JobAlert] = []
        for block in blocks:
            alert = self._parse_single_block(block, email)
            if alert:
                results.append(alert)

        return results

    def _split_into_job_blocks(self, body: str) -> List[str]:
        """
        Detects if body contains multiple job listings or a single listing.
        Handles labeled markers, divider lines (LinkedIn/digest style), and multiple job links.
        """
        normalized_body = body.replace("\xa0", " ").replace("\u200b", "").replace("\r\n", "\n")

        # 1. Explicit labeled markers: repeated "Job Title:" or "Role:" or "Position:"
        labeled_markers = re.findall(r"^(?:Job Title|Role|Position)\s*:", normalized_body, re.IGNORECASE | re.MULTILINE)
        if len(labeled_markers) > 1:
            parts = re.split(r"(?=^(?:Job Title|Role|Position)\s*:)", normalized_body, flags=re.IGNORECASE | re.MULTILINE)
            return [p.strip() for p in parts if p.strip()]

        # 2. Divider-separated job cards (e.g. LinkedIn digests separated by dashed lines)
        divider_pattern = re.compile(r"\n[ \t]*[-=_]{5,}[ \t]*\n")
        if divider_pattern.search(normalized_body):
            raw_chunks = [c.strip() for c in divider_pattern.split(normalized_body) if c.strip()]
            valid_blocks: List[str] = []
            for chunk in raw_chunks:
                clean_chunk = self._clean_digest_header(chunk)
                if self._is_valid_job_block(clean_chunk):
                    valid_blocks.append(clean_chunk)
            if valid_blocks:
                return valid_blocks

        # 3. Repeated "View job:" URLs (LinkedIn digest fallback without dividers)
        view_job_matches = list(re.finditer(r"View job:\s*https?://\S+", normalized_body, re.IGNORECASE))
        if len(view_job_matches) > 1:
            blocks: List[str] = []
            prev_idx = 0
            for m in view_job_matches:
                end_idx = m.end()
                chunk = normalized_body[prev_idx:end_idx].strip()
                clean_chunk = self._clean_digest_header(chunk)
                if self._is_valid_job_block(clean_chunk):
                    blocks.append(clean_chunk)
                prev_idx = end_idx
            if blocks:
                return blocks

        return [normalized_body]

    @staticmethod
    def _clean_digest_header(chunk: str) -> str:
        """Strips leading digest headers and greetings from the top of a card."""
        lines = chunk.splitlines()
        start_idx = 0
        for i, line in enumerate(lines):
            line_str = line.strip()
            if not line_str:
                continue
            if re.match(
                r"^(?:Your job alert for|Manage your job alerts|Here are (?:new )?jobs?|New jobs? matching|Jobs you may be|Jobs in|Daily job alert|Weekly job alert|Hi\s+[A-Za-z]+,)",
                line_str,
                re.IGNORECASE
            ):
                start_idx = i + 1
            else:
                break
        return "\n".join(lines[start_idx:]).strip()

    @staticmethod
    def _is_valid_job_block(chunk: str) -> bool:
        """Determines if a block is a job listing rather than an email footer / disclaimer."""
        if not chunk or len(chunk) < 20:
            return False

        first_line = chunk.strip().splitlines()[0].strip()
        footer_patterns = [
            r"^See all jobs\b",
            r"^Stand out and let hirers know\b",
            r"^See messages instantly\b",
            r"^This email was intended for\b",
            r"^You are receiving Job Alert emails\b",
            r"^Manage your job alerts\b",
            r"^Unsubscribe\b",
            r"^Privacy Policy\b",
            r"^©\s*\d{4}\b"
        ]
        for fp in footer_patterns:
            if re.search(fp, first_line, re.IGNORECASE):
                return False

        has_url = bool(re.search(r"https?://", chunk))
        has_role_kw = bool(re.search(
            r"\b(?:engineer|developer|architect|designer|lead|manager|specialist|sde|analyst|consultant|programmer)\b",
            chunk,
            re.IGNORECASE
        ))
        has_explicit = bool(re.search(r"(?:Job Title|Role|Position|View job|Apply)\s*:", chunk, re.IGNORECASE))

        return bool(has_url or has_explicit or has_role_kw)

    def _parse_single_block(self, block: str, email: RawEmail) -> Optional[JobAlert]:
        block_clean = block.replace("\xa0", " ").replace("\u200b", "").strip()
        lines = [l.strip() for l in block_clean.splitlines() if l.strip()]
        if not lines:
            return None

        # 1. Title Extraction
        title = None
        for pattern in self.TITLE_PATTERNS:
            match = pattern.search(block_clean)
            if match:
                title = (match.group(1) if match.groups() else match.group(0)).strip()
                break

        # Check for card positional title (e.g. LinkedIn digest where first line is Job Title)
        if not title and lines:
            first_line = lines[0]
            norm_first_line = re.sub(r"[_\-–—]+", " ", first_line)
            is_title_candidate = (
                not first_line.startswith(("http", "www", "View job", "Apply", "Location:", "Salary:", "Posted by", "Hiring for", "Hi "))
                and len(first_line) < 140
                and bool(re.search(
                    r"\b(?:engineer|developer|architect|designer|lead|manager|specialist|sde|analyst|consultant|programmer|frontend|front-end|ui|fullstack|full-stack|software|member of technical staff|mts|react|vue|angular|typescript|javascript)\b",
                    norm_first_line,
                    re.IGNORECASE
                ))
            )
            if is_title_candidate:
                title = first_line

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
            match = pattern.search(block_clean)
            if match:
                company = (match.group(1) if match.groups() else match.group(0)).strip()
                break

        # Check for card positional company (in LinkedIn cards, line 1 is company when line 0 is title)
        if not company and len(lines) >= 2 and title == lines[0]:
            candidate_company = lines[1]
            is_valid_comp = (
                not candidate_company.startswith(("http", "www", "View job", "Apply", "Location:", "Salary:"))
                and not re.search(r"^(?:\d+[\d,]*\s+(?:company\s+|school\s+)?alum|\d+[\d,]*\s+connection|actively hiring|easy apply|applicants?)", candidate_company, re.IGNORECASE)
                and len(candidate_company) < 80
            )
            if is_valid_comp:
                company = candidate_company

        if not company:
            # 1. Try to find company from subject like "at Stripe" or "@ Vercel"
            comp_match = re.search(r"(?:at|@)\s+([A-Za-z0-9\s&]{2,25})", email.subject, re.IGNORECASE)
            # 2. Try "<Company> is hiring" e.g. "Resonance Recruitment is hiring"
            is_hiring_match = re.search(r"^([A-Z][A-Za-z0-9&.\s]{1,30}?)\s+is hiring\b", email.subject, re.IGNORECASE)
            if comp_match:
                company = comp_match.group(1).strip()
            elif is_hiring_match:
                company = is_hiring_match.group(1).strip()
            else:
                # 3. Try subject prefix e.g. "Zelis Senior Front End Engineer" -> "Zelis"
                lead_comp = re.match(
                    r"^([A-Z][A-Za-z0-9&.\s]{1,25}?)\s+(?:Senior|Staff|Lead|Principal|Junior|Software|Frontend|Front-End|React|Web|UI|AVP)",
                    email.subject
                )
                if lead_comp and not any(lead_comp.group(1).lower().startswith(x) for x in ["linkedin", "indeed", "naukri", "wellfound", "alert", "fwd", "re"]):
                    company = lead_comp.group(1).strip()
                else:
                    company = "Company Unknown"

        # 3. URL / Application link
        url_match = None
        for pattern in self.URL_PATTERNS:
            m = pattern.search(block_clean)
            if m:
                url_match = m.group(1).strip()
                break

        # Prefer job view URLs over generic alerts/help/unsubscribe links
        if not url_match or any(bad in url_match for bad in ["jobs/alerts", "unsubscribe", "security", "linkedinmobileapp", "help"]):
            all_urls = re.findall(r"https?://[^\s<>\"']+", block_clean)
            job_urls = [u for u in all_urls if any(k in u for k in ["/jobs/view/", "/jobs/", "/careers/", "/apply"])]
            if job_urls:
                url_match = job_urls[0]

        # 4. Salary Extraction & Normalization
        raw_salary, min_sal, max_sal, sort_key, currency = self._extract_salary(block_clean)

        # 5. Company Alumni Extraction
        company_alumni = self._extract_company_alumni(block_clean)

        clean_title = html.unescape(title).strip() if title else "Untitled Job Opening"
        clean_company = html.unescape(company).strip() if company else "Company Unknown"

        return JobAlert(
            title=clean_title,
            company=clean_company,
            raw_salary=raw_salary,
            min_annual_salary=min_sal,
            max_annual_salary=max_sal,
            sort_key_salary=sort_key,
            url_or_contact=url_match,
            source_email_subject=email.subject,
            currency=currency,
            company_alumni=company_alumni
        )

    def _extract_company_alumni(self, text: str) -> Optional[int]:
        """Extracts the number of company alumni mentioned in the job card."""
        match = self.P_COMPANY_ALUMNI.search(text)
        if match:
            num_str = match.group(1) or match.group(2)
            if num_str:
                try:
                    return int(num_str.replace(",", "").strip())
                except ValueError:
                    return None
        return None

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
        "ui engineer", "ui developer", "ui architect", "ui/ux engineer", "web developer", "web engineer",
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
        min_salary_threshold: Optional[float] = None,
        min_alumni_threshold: Optional[int] = None,
        target_positions: Optional[List[str]] = None
    ):
        base_includes = list(include_keywords or self.DEFAULT_INCLUDE_KEYWORDS)
        base_excludes = list(exclude_keywords or self.DEFAULT_EXCLUDE_KEYWORDS)

        if target_positions:
            for pos in target_positions:
                pos_lower = pos.lower().strip()
                if pos_lower not in base_includes:
                    base_includes.append(pos_lower)
                # If target position mentions an excluded term (e.g. "backend", "data"),
                # remove that term from excludes so user can search for multiple positions
                for excl in list(base_excludes):
                    if excl in pos_lower:
                        base_excludes.remove(excl)

        self.include_keywords = [k.lower() for k in base_includes]
        self.exclude_keywords = [k.lower() for k in base_excludes]
        self.min_salary_threshold = min_salary_threshold
        self.min_alumni_threshold = min_alumni_threshold

    def is_match(self, job: JobAlert) -> Tuple[bool, str]:
        """
        Determines whether a job alert matches frontend criteria.
        Returns: (is_matched: bool, reason: str)
        """
        # Match primarily against normalized job.title to avoid subject bleeding in multi-job digests
        norm_title = re.sub(r"[_\-–—/]+", " ", job.title.lower()) if job.title else ""
        if job.title and job.title != "Untitled Job Opening":
            search_corpus = norm_title
        else:
            search_corpus = f"{norm_title} {job.source_email_subject.lower()}"

        # 1. Salary threshold filter
        if self.min_salary_threshold is not None:
            if job.sort_key_salary > 0 and job.sort_key_salary < self.min_salary_threshold:
                return False, f"Salary below threshold (${self.min_salary_threshold:,.0f})"

        # 2. Company alumni threshold filter
        if self.min_alumni_threshold is not None:
            alumni_count = job.company_alumni or 0
            if alumni_count < self.min_alumni_threshold:
                return False, f"Alumni count ({alumni_count}) below threshold ({self.min_alumni_threshold})"

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
        """
        Sorts jobs by:
        1. Compensation (descending: highest compensation first; unlisted at the end)
        2. Company Rank (ascending: 1 is top tier, 99 is unranked)
        3. Company Alumni (descending: highest alumni count first)
        4. Company Rating (descending: highest rating first)
        5. Number of Ratings (descending: most reviews first)
        """
        return sorted(
            jobs,
            key=lambda j: (
                -j.sort_key_salary,
                j.company_rank,
                -(j.company_alumni or 0),
                -(j.rating or 0.0),
                -(j.number_of_ratings or 0)
            )
        )

    @classmethod
    def render(cls, jobs: List[JobAlert], output_format: str = "table") -> str:
        sorted_jobs = cls.rank_and_sort(jobs)

        if output_format == "markdown":
            return cls._render_markdown(sorted_jobs)

        elif output_format == "csv":
            return cls._render_csv(sorted_jobs)

        else:
            return cls._render_terminal_table(sorted_jobs)

    @staticmethod
    def _render_terminal_table(jobs: List[JobAlert]) -> str:
        if not jobs:
            return "No matching jobs found."

        has_rankings = any(j.company_rank < 99 or j.target_position for j in jobs)
        has_ratings = any(j.rating is not None for j in jobs)
        has_alumni = any(j.company_alumni is not None and j.company_alumni > 0 for j in jobs)

        # Compute column widths
        col_rank = 4
        col_title = max(18, min(30, max(len(j.title) for j in jobs)))
        col_company = max(12, min(20, max(len(j.company) for j in jobs)))
        col_co_rank = 9
        col_target_pos = max(12, min(25, max(len(j.display_target_position) for j in jobs)))
        col_alumni = 12
        col_rating = 15
        col_salary = max(16, min(24, max(len(j.normalized_annual_summary) for j in jobs)))
        col_link = 26

        cols: List[Tuple[str, int, Any]] = [
            ("#", col_rank, lambda j, idx: str(idx)),
            ("Job Title", col_title, lambda j, idx: (j.title[:col_title - 3] + "...") if len(j.title) > col_title else j.title),
            ("Company", col_company, lambda j, idx: (j.company[:col_company - 3] + "...") if len(j.company) > col_company else j.company),
        ]
        if has_rankings:
            cols.append(("Co. Rank", col_co_rank, lambda j, idx: j.display_company_rank))
            cols.append(("Target Pos", col_target_pos, lambda j, idx: (j.display_target_position[:col_target_pos - 3] + "...") if len(j.display_target_position) > col_target_pos else j.display_target_position))
        if has_alumni:
            cols.append(("Alumni", col_alumni, lambda j, idx: j.display_company_alumni))
        if has_ratings:
            cols.append(("Rating", col_rating, lambda j, idx: j.display_rating))
        cols.append(("Est. Annual CTC", col_salary, lambda j, idx: (j.normalized_annual_summary[:col_salary - 3] + "...") if len(j.normalized_annual_summary) > col_salary else j.normalized_annual_summary))
        cols.append(("Application URL", col_link, lambda j, idx: ((j.url_or_contact or "N/A")[:col_link - 3] + "...") if len(j.url_or_contact or "N/A") > col_link else (j.url_or_contact or "N/A")))

        border_top = f"┌{'┬'.join('─' * (w + 2) for _, w, _ in cols)}┐"
        border_mid = f"├{'┼'.join('─' * (w + 2) for _, w, _ in cols)}┤"
        border_bot = f"└{'┴'.join('─' * (w + 2) for _, w, _ in cols)}┘"

        lines: List[str] = [border_top]
        header = f"│ {' │ '.join(f'{name:<{w}}' for name, w, _ in cols)} │"
        lines.append(header)
        lines.append(border_mid)

        for idx, job in enumerate(jobs, 1):
            row = f"│ {' │ '.join(f'{fn(job, idx):<{w}}' for _, w, fn in cols)} │"
            lines.append(row)

        lines.append(border_bot)
        return "\n".join(lines)

    @staticmethod
    def _render_markdown(jobs: List[JobAlert]) -> str:
        has_rankings = any(j.company_rank < 99 or j.target_position for j in jobs)
        has_ratings = any(j.rating is not None for j in jobs)
        has_alumni = any(j.company_alumni is not None and j.company_alumni > 0 for j in jobs)

        cols: List[Tuple[str, str, Any]] = [
            ("Rank", ":---", lambda j, idx: str(idx)),
            ("Job Title", ":---", lambda j, idx: f"**{j.title}**"),
            ("Company", ":---", lambda j, idx: j.company),
        ]
        if has_rankings:
            cols.append(("Co. Rank", ":---:", lambda j, idx: f"`{j.display_company_rank}`"))
            cols.append(("Target Pos", ":---", lambda j, idx: j.display_target_position))
        if has_alumni:
            cols.append(("Alumni", ":---:", lambda j, idx: j.display_company_alumni))
        if has_ratings:
            cols.append(("Rating", ":---:", lambda j, idx: j.display_rating))
            cols.append(("Target Salary", ":---", lambda j, idx: j.target_salary or "-"))
        cols.append(("Estimated Annual Salary", ":---", lambda j, idx: f"`{j.normalized_annual_summary}`"))
        cols.append(("Raw Salary", ":---", lambda j, idx: j.display_salary))
        cols.append(("Link", ":---", lambda j, idx: f"[Apply Here]({j.url_or_contact})" if j.url_or_contact else "N/A"))

        header_line = "| " + " | ".join(name for name, _, _ in cols) + " |"
        align_line = "| " + " | ".join(align for _, align, _ in cols) + " |"
        rows = [header_line, align_line]
        for idx, job in enumerate(jobs, 1):
            rows.append("| " + " | ".join(fn(job, idx) for _, _, fn in cols) + " |")
        return "\n".join(rows)

    @staticmethod
    def _render_csv(jobs: List[JobAlert]) -> str:
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        has_rankings = any(j.company_rank < 99 or j.target_position for j in jobs)
        if has_rankings:
            writer.writerow(["rank", "title", "company", "company_rank", "target_position", "company_alumni", "rating", "number_of_ratings", "target_salary", "normalized_annual_salary", "min_annual", "max_annual", "sort_key", "raw_salary", "url", "subject"])
            for idx, j in enumerate(jobs, 1):
                writer.writerow([
                    idx, j.title, j.company,
                    j.display_company_rank, j.display_target_position,
                    j.company_alumni if j.company_alumni is not None else "",
                    j.rating if j.rating is not None else "",
                    j.number_of_ratings if j.number_of_ratings is not None else "",
                    j.target_salary or "",
                    j.normalized_annual_summary,
                    j.min_annual_salary, j.max_annual_salary, j.sort_key_salary,
                    j.raw_salary, j.url_or_contact, j.source_email_subject
                ])
        else:
            writer.writerow(["rank", "title", "company", "company_alumni", "normalized_annual_salary", "min_annual", "max_annual", "sort_key", "raw_salary", "url", "subject"])
            for idx, j in enumerate(jobs, 1):
                writer.writerow([
                    idx, j.title, j.company,
                    j.company_alumni if j.company_alumni is not None else "",
                    j.normalized_annual_summary,
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
    show_all: bool = False,
    ranking_config: Optional[RankingConfig] = None
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

    # Apply company rankings if available
    if ranking_config:
        for job in all_jobs:
            ranking_config.apply_to_job(job)
        print(f"   ✓ Applied company rankings ({len(ranking_config.companies)} companies)", file=sys.stderr)

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
        print(f"   ✓ Filtered to {len(matched_jobs)} matching opening(s) ({discarded_count} discarded)", file=sys.stderr)

    # Render results
    output = JobReporter.render(matched_jobs, output_format=output_format)
    print("\n" + output)

    # Summary
    if output_format == "table" and matched_jobs:
        sorted_jobs = JobReporter.rank_and_sort(matched_jobs)
        top_sal = sorted_jobs[0].normalized_annual_summary if sorted_jobs[0].sort_key_salary > 0 else "N/A"
        rank_info = ""
        extras = []
        if sorted_jobs[0].company_rank < 99:
            extras.append(f"Co. {sorted_jobs[0].display_company_rank}")
        if sorted_jobs[0].target_position:
            extras.append(f"Target: {sorted_jobs[0].target_position}")
        if sorted_jobs[0].company_alumni:
            extras.append(sorted_jobs[0].display_company_alumni)
        if extras:
            rank_info = f" [{', '.join(extras)}]"
        print(f"\n📊 Summary: {len(matched_jobs)} position(s) found. Top pick: {sorted_jobs[0].company} - {sorted_jobs[0].title} ({top_sal}){rank_info}", file=sys.stderr)

    return 0


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Filter and rank email job alerts for preferred companies by rank and salary.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run against sample alerts.txt with automatic ranking detection (rankings.csv):
  python3 job_alert_filter.py -i alerts.txt

  # Explicitly specify a custom rankings CSV file:
  python3 job_alert_filter.py -i alerts.txt --rankings rankings.csv

  # Output as Markdown table:
  python3 job_alert_filter.py -i alerts.txt --format markdown

  # Export rankings to CSV:
  python3 job_alert_filter.py -i alerts.txt --format csv > ranked_jobs.csv

  # Disable rankings and sort purely by salary:
  python3 job_alert_filter.py -i alerts.txt --no-rankings

  # Filter only jobs paying at least $160,000 / year:
  python3 job_alert_filter.py -i alerts.txt --min-salary 160000
        """
    )
    parser.add_argument(
        "-i", "--input",
        default="alerts.txt",
        help="Path to alerts file (.txt, .eml, .json), directory of emails, or '-' for stdin (default: alerts.txt)"
    )
    parser.add_argument(
        "-r", "--rankings",
        default=None,
        help="Path to company rankings CSV file. Defaults to rankings.csv if present."
    )
    parser.add_argument(
        "--no-rankings",
        action="store_true",
        help="Disable company ranking; sort strictly by compensation."
    )
    parser.add_argument(
        "-f", "--format",
        choices=["table", "markdown", "csv"],
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
        "--min-alumni",
        type=int,
        default=None,
        help="Minimum number of company alumni required to match (e.g. 5)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show all parsed jobs without filtering (useful for inspecting raw alert streams)"
    )
    parser.add_argument(
        "--include",
        nargs="+",
        help="Additional custom keywords to match for target roles"
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

    ranking_config = None
    if not args.no_rankings:
        rankings_path = args.rankings or ("rankings.csv" if os.path.exists("rankings.csv") else None)
        if rankings_path:
            try:
                ranking_config = RankingConfig.load_from_file(rankings_path)
                print(f"🎯 Loaded rankings from CSV '{rankings_path}' ({len(ranking_config.companies)} companies)", file=sys.stderr)
            except Exception as e:
                print(f"[Warning] Could not load rankings from '{rankings_path}': {e}", file=sys.stderr)

    custom_include = None
    if args.include:
        custom_include = JobFilter.DEFAULT_INCLUDE_KEYWORDS + args.include

    custom_exclude = None
    if args.exclude:
        custom_exclude = JobFilter.DEFAULT_EXCLUDE_KEYWORDS + args.exclude

    target_positions = list(ranking_config.positions.keys()) if ranking_config else None

    job_filter = JobFilter(
        include_keywords=custom_include,
        exclude_keywords=custom_exclude,
        min_salary_threshold=args.min_salary,
        min_alumni_threshold=args.min_alumni,
        target_positions=target_positions
    )

    return run_pipeline(
        input_source=source,
        job_filter=job_filter,
        output_format=args.format,
        show_all=args.all,
        ranking_config=ranking_config
    )


if __name__ == "__main__":
    sys.exit(main())
