#!/usr/bin/env python3
"""
Unit tests for Job Alert Filter & Salary Ranker
"""

import unittest
from job_alert_filter import (
    JobAlert,
    RawEmail,
    JobParser,
    JobFilter,
    JobReporter,
    LocalFileEmailSource
)


class TestJobParser(unittest.TestCase):
    def setUp(self):
        self.parser = JobParser()

    def test_annual_range_shorthand(self):
        email = RawEmail(
            subject="Job Alert: Senior Frontend Developer",
            body="Position: Senior Frontend Engineer\nCompany: Acme Corp\nSalary: $140k - $175k / year\nApply: https://acme.com/apply"
        )
        jobs = self.parser.parse_email(email)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.title, "Senior Frontend Engineer")
        self.assertEqual(job.company, "Acme Corp")
        self.assertEqual(job.min_annual_salary, 140000.0)
        self.assertEqual(job.max_annual_salary, 175000.0)
        self.assertEqual(job.sort_key_salary, 175000.0)
        self.assertEqual(job.url_or_contact, "https://acme.com/apply")

    def test_hourly_rate_normalization(self):
        email = RawEmail(
            subject="Contract opening",
            body="Role: React UI Specialist\nCompany: TechFirm\nPay: $70 - $90/hr\nLink: https://techfirm.io/job"
        )
        jobs = self.parser.parse_email(email)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        # 70 * 2000 = 140,000, 90 * 2000 = 180,000
        self.assertEqual(job.min_annual_salary, 140000.0)
        self.assertEqual(job.max_annual_salary, 180000.0)
        self.assertEqual(job.sort_key_salary, 180000.0)

    def test_full_annual_numbers(self):
        email = RawEmail(
            subject="Alert",
            body="Position: Frontend Architect\nCompany: GlobalTech\nCompensation: $200,000 - $250,000 per year"
        )
        jobs = self.parser.parse_email(email)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.min_annual_salary, 200000.0)
        self.assertEqual(job.max_annual_salary, 250000.0)

    def test_unlisted_salary(self):
        email = RawEmail(
            subject="Job Lead",
            body="Job Title: Lead Web Developer\nCompany: StartupX\nSalary: Competitive + Stock Options\nURL: https://startupx.com"
        )
        jobs = self.parser.parse_email(email)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertIsNone(job.min_annual_salary)
        self.assertIsNone(job.max_annual_salary)
        self.assertEqual(job.sort_key_salary, 0.0)


class TestJobFilter(unittest.TestCase):
    def setUp(self):
        self.job_filter = JobFilter()

    def test_frontend_match(self):
        job = JobAlert(
            title="Senior Frontend Engineer (React)",
            company="TestCo",
            raw_salary="$150k",
            min_annual_salary=150000.0,
            max_annual_salary=150000.0,
            sort_key_salary=150000.0,
            url_or_contact=None,
            source_email_subject="Alert"
        )
        matched, _ = self.job_filter.is_match(job)
        self.assertTrue(matched)

    def test_backend_exclusion(self):
        job = JobAlert(
            title="Senior Backend Systems Engineer",
            company="TestCo",
            raw_salary="$180k",
            min_annual_salary=180000.0,
            max_annual_salary=180000.0,
            sort_key_salary=180000.0,
            url_or_contact=None,
            source_email_subject="Alert"
        )
        matched, _ = self.job_filter.is_match(job)
        self.assertFalse(matched)

    def test_min_salary_threshold(self):
        filt = JobFilter(min_salary_threshold=160000.0)
        low_paying = JobAlert(
            title="Frontend Developer",
            company="TestCo",
            raw_salary="$120k",
            min_annual_salary=120000.0,
            max_annual_salary=120000.0,
            sort_key_salary=120000.0,
            url_or_contact=None,
            source_email_subject="Alert"
        )
        high_paying = JobAlert(
            title="Frontend Developer",
            company="TestCo",
            raw_salary="$180k",
            min_annual_salary=180000.0,
            max_annual_salary=180000.0,
            sort_key_salary=180000.0,
            url_or_contact=None,
            source_email_subject="Alert"
        )
        self.assertFalse(filt.is_match(low_paying)[0])
        self.assertTrue(filt.is_match(high_paying)[0])


class TestJobReporter(unittest.TestCase):
    def test_salary_ranking_order(self):
        job_low = JobAlert(
            title="Role A", company="Co A", raw_salary=None,
            min_annual_salary=100000.0, max_annual_salary=120000.0,
            sort_key_salary=120000.0, url_or_contact=None, source_email_subject="S1"
        )
        job_high = JobAlert(
            title="Role B", company="Co B", raw_salary=None,
            min_annual_salary=180000.0, max_annual_salary=220000.0,
            sort_key_salary=220000.0, url_or_contact=None, source_email_subject="S2"
        )
        job_unlisted = JobAlert(
            title="Role C", company="Co C", raw_salary=None,
            min_annual_salary=None, max_annual_salary=None,
            sort_key_salary=0.0, url_or_contact=None, source_email_subject="S3"
        )

        ranked = JobReporter.rank_and_sort([job_low, job_unlisted, job_high])
        self.assertEqual(ranked[0].title, "Role B")  # Highest ($220k)
        self.assertEqual(ranked[1].title, "Role A")  # Middle ($120k)
        self.assertEqual(ranked[2].title, "Role C")  # Unlisted (0.0)


if __name__ == "__main__":
    unittest.main()
