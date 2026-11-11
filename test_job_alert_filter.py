#!/usr/bin/env python3
"""
Unit tests for Job Alert Filter & Salary Ranker
"""

import os
import tempfile
import unittest
from job_alert_filter import (
    CompanyPreference,
    JobAlert,
    RawEmail,
    JobParser,
    JobFilter,
    JobReporter,
    LocalFileEmailSource,
    RankingConfig,
    build_cli
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


class TestLinkedInDigest(unittest.TestCase):
    def setUp(self):
        self.parser = JobParser()
        self.filter = JobFilter()

    def test_linkedin_digest_parsing_and_filtering(self):
        body = """Your job alert for senior frontend in IndiaManage your job alerts: https://linkedin.com/alerts

Software Engineer - Frontend (ReactJS, NextJs)
H&M
Bengaluru

This company is actively hiring
View job: https://www.linkedin.com/comm/jobs/view/1001/

---------------------------------------------------------

SDE-2, Partner Engineering
Zinier
Bengaluru

7 company alumni
View job: https://www.linkedin.com/comm/jobs/view/1002/

---------------------------------------------------------

Senior Software Engineer – Front-End (Angular)
Deloitte
Hyderabad

5 connections
View job: https://www.linkedin.com/comm/jobs/view/1003/

---------------------------------------------------------

See all jobs on LinkedIn: https://www.linkedin.com/comm/jobs/search
Unsubscribe: https://www.linkedin.com/unsubscribe
"""
        email = RawEmail(
            subject="Software Engineer - Frontend (ReactJS, NextJs) at H&M",
            body=body
        )
        jobs = self.parser.parse_email(email)
        self.assertEqual(len(jobs), 3)

        # Job 1
        self.assertIn("Frontend", jobs[0].title)
        self.assertEqual(jobs[0].company, "H&M")
        self.assertEqual(jobs[0].url_or_contact, "https://www.linkedin.com/comm/jobs/view/1001/")

        # Job 2
        self.assertEqual(jobs[1].title, "SDE-2, Partner Engineering")
        self.assertEqual(jobs[1].company, "Zinier")
        self.assertEqual(jobs[1].url_or_contact, "https://www.linkedin.com/comm/jobs/view/1002/")

        # Job 3
        self.assertIn("Front-End", jobs[2].title)
        self.assertEqual(jobs[2].company, "Deloitte")
        self.assertEqual(jobs[2].url_or_contact, "https://www.linkedin.com/comm/jobs/view/1003/")

        # Filtering test: SDE-2 should NOT match frontend even if email subject mentions frontend
        matched_jobs = [j for j in jobs if self.filter.is_match(j)[0]]
        self.assertEqual(len(matched_jobs), 2)
        titles = [j.title for j in matched_jobs]
        self.assertTrue(any("H&M" in j.company for j in matched_jobs))
        self.assertTrue(any("Deloitte" in j.company for j in matched_jobs))
        self.assertFalse(any("Zinier" in j.company for j in matched_jobs))

    def test_ui_architect_with_underscore_in_digest(self):
        body = """
UI_Architect
WPP Enterprise Solutions
Bengaluru

11 company alumni
View job: https://www.linkedin.com/comm/jobs/view/9999/
"""
        email = RawEmail(subject="Zelis Senior Front End Engineer", body=body)
        jobs = self.parser.parse_email(email)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "UI_Architect")
        self.assertEqual(jobs[0].company, "WPP Enterprise Solutions")
        matched, _ = self.filter.is_match(jobs[0])
        self.assertTrue(matched)

    def test_naukri_format_extraction(self):
        body = """
Hiring for an IT Services & Consulting company
Posted by Resonance Recruitment.
React Developer
Australia, Canada, Germany
₹80L - ₹90L/year
Apply: https://dl.naukri.com/apply
"""
        email = RawEmail(subject="Resonance Recruitment is hiring", body=body)
        jobs = self.parser.parse_email(email)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "React Developer")
        self.assertEqual(jobs[0].company, "Resonance Recruitment")
        self.assertEqual(jobs[0].min_annual_salary, 8000000.0)
        self.assertEqual(jobs[0].max_annual_salary, 9000000.0)
        self.assertEqual(jobs[0].currency, "₹")
        matched, _ = self.filter.is_match(jobs[0])
        self.assertTrue(matched)


class TestRankingConfig(unittest.TestCase):
    def test_csv_loading_and_matching(self):
        sample_csv = """company,position,rank,salary,rating,number_of_ratings
Stripe,Staff Front-End Architect,1,,4.3,12500
Vercel,Senior Frontend Engineer,1,,4.5,850
"""
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8") as tmp:
            tmp.write(sample_csv)
            tmp_path = tmp.name

        try:
            config = RankingConfig.load_from_file(tmp_path)
            self.assertIn("Stripe", config.companies)
            self.assertEqual(config.companies["Stripe"].rank, 1)
            self.assertEqual(config.companies["Stripe"].rating, 4.3)
            self.assertEqual(config.companies["Stripe"].number_of_ratings, 12500)
            self.assertEqual(config.companies["Stripe"].position, "Staff Front-End Architect")

            self.assertIn("Vercel", config.companies)
            self.assertEqual(config.companies["Vercel"].rank, 1)
            self.assertEqual(config.companies["Vercel"].rating, 4.5)
            self.assertEqual(config.companies["Vercel"].number_of_ratings, 850)
            self.assertEqual(config.companies["Vercel"].position, "Senior Frontend Engineer")

            # Verify ordered preferences list
            self.assertEqual(len(config.preferences), 2)
            self.assertEqual(config.preferences[0].name, "Stripe")
            self.assertEqual(config.preferences[1].name, "Vercel")

            job = JobAlert(
                title="Staff Front-End Architect",
                company="Stripe",
                raw_salary="$250k",
                min_annual_salary=250000.0,
                max_annual_salary=250000.0,
                sort_key_salary=250000.0,
                url_or_contact="https://stripe.com",
                source_email_subject="Stripe alert"
            )
            config.apply_to_job(job)
            self.assertEqual(job.company_rank, 1)
            self.assertEqual(job.rating, 4.3)
            self.assertEqual(job.number_of_ratings, 12500)
            self.assertEqual(job.display_company_rank, "Tier 1")
            self.assertEqual(job.display_rating, "★ 4.3 (12.5k)")

            job_vercel = JobAlert(
                title="Senior Frontend Engineer (React)",
                company="Vercel Inc",
                raw_salary="$180k",
                min_annual_salary=180000.0,
                max_annual_salary=180000.0,
                sort_key_salary=180000.0,
                url_or_contact="https://vercel.com",
                source_email_subject="Vercel alert"
            )
            config.apply_to_job(job_vercel)
            self.assertEqual(job_vercel.company_rank, 1)
            self.assertEqual(job_vercel.rating, 4.5)
            self.assertEqual(job_vercel.number_of_ratings, 850)
            self.assertEqual(job_vercel.display_rating, "★ 4.5 (850)")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_strict_company_column_required(self):
        sample_csv = """name,position,rank
Google,Architect,1
"""
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8") as tmp:
            tmp.write(sample_csv)
            tmp_path = tmp.name

        try:
            with self.assertRaises(ValueError) as ctx:
                RankingConfig.load_from_file(tmp_path)
            self.assertIn("must contain a 'company' column", str(ctx.exception))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_multiple_rows_per_company_parsing(self):
        sample_csv = """company,position,rank,salary,rating,number_of_ratings
Google,Staff Engineer,1,300000,4.4,120000
Google,Frontend Engineer,1,200000,4.4,120000
"""
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8") as tmp:
            tmp.write(sample_csv)
            tmp_path = tmp.name

        try:
            config = RankingConfig.load_from_file(tmp_path)
            self.assertEqual(len(config.preferences), 2)
            self.assertEqual(config.preferences[0].name, "Google")
            self.assertEqual(config.preferences[0].position, "Staff Engineer")
            self.assertEqual(config.preferences[0].target_salary, "300000")
            self.assertEqual(config.preferences[1].name, "Google")
            self.assertEqual(config.preferences[1].position, "Frontend Engineer")
            self.assertEqual(config.preferences[1].target_salary, "200000")
            # First match in dictionary
            self.assertEqual(config.companies["Google"].position, "Staff Engineer")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_first_company_match_and_target_position(self):
        sample_csv = """company,position,rank,salary,rating,number_of_ratings
Meta,Staff UI Engineer,1,320000,4.1,45000
Meta,Frontend Engineer,2,180000,4.1,45000
"""
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8") as tmp:
            tmp.write(sample_csv)
            tmp_path = tmp.name

        try:
            config = RankingConfig.load_from_file(tmp_path)
            job = JobAlert(
                title="Staff UI Engineer",
                company="Meta Platforms",
                raw_salary=None,
                min_annual_salary=None,
                max_annual_salary=None,
                sort_key_salary=0.0,
                url_or_contact=None,
                source_email_subject=""
            )
            config.apply_to_job(job)
            # Should select first matching row (rank 1, Staff UI Engineer)
            self.assertEqual(job.company_rank, 1)
            self.assertEqual(job.target_position, "Staff UI Engineer")
            self.assertEqual(job.target_salary, "320000")
            self.assertEqual(job.position_rank, 99)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_target_salary_matching_behavior(self):
        # 1. Matching title matches target position -> target salary applied
        # 2. Non-matching title does NOT get position-specific target salary
        # 3. Row with no position specifies general company target salary
        sample_csv = """company,position,rank,salary,rating,number_of_ratings
Apple,Lead iOS Developer,1,300000,4.2,65000
Amazon,,2,220000,4.0,80000
"""
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8") as tmp:
            tmp.write(sample_csv)
            tmp_path = tmp.name

        try:
            config = RankingConfig.load_from_file(tmp_path)

            job_match = JobAlert(
                title="Lead iOS Developer",
                company="Apple Inc",
                raw_salary=None,
                min_annual_salary=None,
                max_annual_salary=None,
                sort_key_salary=0.0,
                url_or_contact=None,
                source_email_subject=""
            )
            config.apply_to_job(job_match)
            self.assertEqual(job_match.target_position, "Lead iOS Developer")
            self.assertEqual(job_match.target_salary, "300000")

            job_mismatch = JobAlert(
                title="Frontend Specialist",
                company="Apple Inc",
                raw_salary=None,
                min_annual_salary=None,
                max_annual_salary=None,
                sort_key_salary=0.0,
                url_or_contact=None,
                source_email_subject=""
            )
            config.apply_to_job(job_mismatch)
            self.assertEqual(job_mismatch.target_position, "Lead iOS Developer")
            self.assertIsNone(job_mismatch.target_salary)

            job_general = JobAlert(
                title="Frontend Engineer",
                company="Amazon",
                raw_salary=None,
                min_annual_salary=None,
                max_annual_salary=None,
                sort_key_salary=0.0,
                url_or_contact=None,
                source_email_subject=""
            )
            config.apply_to_job(job_general)
            self.assertIsNone(job_general.target_position)
            self.assertEqual(job_general.target_salary, "220000")
            self.assertEqual(job_general.sort_key_salary, 220000.0)
            self.assertEqual(job_general.normalized_annual_summary, "$220,000/yr (Target)")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_target_salary_compensation_fallback_and_sorting(self):
        sample_csv = """company,position,rank,salary,rating,number_of_ratings
Sage,Senior Engineer,,2620000,3.7,40
Cisco,Software Engineer IV,,5104697,3.6,2808
"""
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8") as tmp:
            tmp.write(sample_csv)
            tmp_path = tmp.name

        try:
            config = RankingConfig.load_from_file(tmp_path)

            # Unlisted alert for Sage
            job_sage = JobAlert(
                title="Senior Engineer", company="Sage", raw_salary=None,
                min_annual_salary=None, max_annual_salary=None,
                sort_key_salary=0.0, url_or_contact=None, source_email_subject="",
                company_alumni=60
            )
            config.apply_to_job(job_sage)
            self.assertEqual(job_sage.sort_key_salary, 2620000.0)
            self.assertEqual(job_sage.currency, "₹")
            self.assertEqual(job_sage.normalized_annual_summary, "₹2,620,000/yr (Target)")

            # Unlisted alert for Cisco
            job_cisco = JobAlert(
                title="Software Engineer IV", company="Cisco", raw_salary=None,
                min_annual_salary=None, max_annual_salary=None,
                sort_key_salary=0.0, url_or_contact=None, source_email_subject="",
                company_alumni=2808
            )
            config.apply_to_job(job_cisco)
            self.assertEqual(job_cisco.sort_key_salary, 5104697.0)
            self.assertEqual(job_cisco.currency, "₹")
            self.assertEqual(job_cisco.normalized_annual_summary, "₹5,104,697/yr (Target)")

            # Unlisted alert with no target salary in rankings.csv
            job_other = JobAlert(
                title="Engineer", company="OtherCo", raw_salary=None,
                min_annual_salary=None, max_annual_salary=None,
                sort_key_salary=0.0, url_or_contact=None, source_email_subject="",
                company_alumni=10000
            )
            config.apply_to_job(job_other)
            self.assertEqual(job_other.sort_key_salary, 0.0)
            self.assertEqual(job_other.normalized_annual_summary, "Unlisted")

            # Alert with actual listed salary in email ($100k) should NOT be overridden by target salary
            job_with_email_salary = JobAlert(
                title="Software Engineer IV", company="Cisco", raw_salary="$100k",
                min_annual_salary=100000.0, max_annual_salary=100000.0,
                sort_key_salary=100000.0, url_or_contact=None, source_email_subject=""
            )
            config.apply_to_job(job_with_email_salary)
            self.assertEqual(job_with_email_salary.sort_key_salary, 100000.0)
            self.assertEqual(job_with_email_salary.normalized_annual_summary, "$100,000/yr")

            # Check sorting order: Cisco ($5.1M) -> Sage ($2.6M) -> OtherCo (Unlisted, 10k alumni)
            ranked = JobReporter.rank_and_sort([job_other, job_sage, job_cisco])
            self.assertEqual(ranked[0].company, "Cisco")
            self.assertEqual(ranked[1].company, "Sage")
            self.assertEqual(ranked[2].company, "OtherCo")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_unranked_fallback(self):
        from job_alert_filter import CompanyPreference, PositionPreference
        config = RankingConfig(
            companies={"Google": CompanyPreference(rank=1)},
            positions={"Architect": PositionPreference(rank=1)}
        )
        job = JobAlert(
            title="Junior Support Tech",
            company="Unknown Inc",
            raw_salary=None,
            min_annual_salary=None,
            max_annual_salary=None,
            sort_key_salary=0.0,
            url_or_contact=None,
            source_email_subject="Alert"
        )
        config.apply_to_job(job)
        self.assertEqual(job.company_rank, 99)
        self.assertEqual(job.position_rank, 99)
        self.assertEqual(job.display_company_rank, "-")
        self.assertEqual(job.display_position_rank, "-")

    def test_ranked_sorting_order(self):
        tier1_top_role = JobAlert(
            title="Architect", company="Tier 1 Corp", raw_salary=None,
            min_annual_salary=150000.0, max_annual_salary=150000.0,
            sort_key_salary=150000.0, url_or_contact=None, source_email_subject="",
            company_rank=1
        )
        tier2_high_pay = JobAlert(
            title="Developer", company="Tier 2 Corp", raw_salary=None,
            min_annual_salary=250000.0, max_annual_salary=250000.0,
            sort_key_salary=250000.0, url_or_contact=None, source_email_subject="",
            company_rank=2
        )
        tier1_lower_pay = JobAlert(
            title="Developer", company="Tier 1 Corp", raw_salary=None,
            min_annual_salary=120000.0, max_annual_salary=120000.0,
            sort_key_salary=120000.0, url_or_contact=None, source_email_subject="",
            company_rank=1
        )
        sorted_jobs = JobReporter.rank_and_sort([tier2_high_pay, tier1_lower_pay, tier1_top_role])
        # Hierarchy: Salary (descending) -> Company Tier (ascending) -> Alumni (descending)
        # 1. tier2_high_pay ($250,000)
        # 2. tier1_top_role ($150,000)
        # 3. tier1_lower_pay ($120,000)
        self.assertEqual(sorted_jobs[0].company, "Tier 2 Corp")
        self.assertEqual(sorted_jobs[0].sort_key_salary, 250000.0)
        self.assertEqual(sorted_jobs[1].title, "Architect")
        self.assertEqual(sorted_jobs[1].sort_key_salary, 150000.0)
        self.assertEqual(sorted_jobs[2].title, "Developer")
        self.assertEqual(sorted_jobs[2].sort_key_salary, 120000.0)

    def test_salary_company_alumni_tiebreaking(self):
        # Tied salary ($200k), different tiers
        tier1_same_salary = JobAlert(
            title="Lead Engineer", company="Co Alpha", raw_salary=None,
            min_annual_salary=200000.0, max_annual_salary=200000.0,
            sort_key_salary=200000.0, url_or_contact=None, source_email_subject="",
            company_rank=1, company_alumni=10
        )
        tier2_same_salary = JobAlert(
            title="Senior Engineer", company="Co Beta", raw_salary=None,
            min_annual_salary=200000.0, max_annual_salary=200000.0,
            sort_key_salary=200000.0, url_or_contact=None, source_email_subject="",
            company_rank=2, company_alumni=500
        )
        # Tied salary ($200k) and tied tier (Tier 1), different alumni count
        tier1_higher_alumni = JobAlert(
            title="Staff Engineer", company="Co Gamma", raw_salary=None,
            min_annual_salary=200000.0, max_annual_salary=200000.0,
            sort_key_salary=200000.0, url_or_contact=None, source_email_subject="",
            company_rank=1, company_alumni=100
        )
        # Lower salary ($180k) but huge alumni and Tier 1
        tier1_lower_salary_huge_alumni = JobAlert(
            title="Principal Engineer", company="Co Delta", raw_salary=None,
            min_annual_salary=180000.0, max_annual_salary=180000.0,
            sort_key_salary=180000.0, url_or_contact=None, source_email_subject="",
            company_rank=1, company_alumni=1000
        )

        ranked = JobReporter.rank_and_sort([
            tier1_lower_salary_huge_alumni,
            tier2_same_salary,
            tier1_same_salary,
            tier1_higher_alumni
        ])

        # 1. $200k, Tier 1, 100 alumni (Co Gamma)
        # 2. $200k, Tier 1, 10 alumni (Co Alpha)
        # 3. $200k, Tier 2, 500 alumni (Co Beta)
        # 4. $180k, Tier 1, 1000 alumni (Co Delta - lower salary sorts after $200k)
        self.assertEqual(ranked[0].company, "Co Gamma")
        self.assertEqual(ranked[1].company, "Co Alpha")
        self.assertEqual(ranked[2].company, "Co Beta")
        self.assertEqual(ranked[3].company, "Co Delta")

    def test_company_alumni_priority_sorting(self):
        job_162_alumni = JobAlert(
            title="Full Stack Engineer III", company="CrowdStrike", raw_salary=None,
            min_annual_salary=None, max_annual_salary=None,
            sort_key_salary=0.0, url_or_contact="https://linkedin.com/1", source_email_subject="Alert",
            company_alumni=162
        )
        job_60_alumni = JobAlert(
            title="Senior Engineer", company="Sage", raw_salary=None,
            min_annual_salary=None, max_annual_salary=None,
            sort_key_salary=0.0, url_or_contact="https://linkedin.com/2", source_email_subject="Alert",
            company_alumni=60
        )
        job_4_alumni = JobAlert(
            title="Lead React Developer", company="Verisk", raw_salary=None,
            min_annual_salary=None, max_annual_salary=None,
            sort_key_salary=0.0, url_or_contact="https://linkedin.com/3", source_email_subject="Alert",
            company_alumni=4
        )
        job_no_alumni = JobAlert(
            title="Senior Frontend Engineer", company="GitLab", raw_salary=None,
            min_annual_salary=None, max_annual_salary=None,
            sort_key_salary=0.0, url_or_contact="https://linkedin.com/4", source_email_subject="Alert",
            company_alumni=None
        )

        ranked = JobReporter.rank_and_sort([job_4_alumni, job_no_alumni, job_162_alumni, job_60_alumni])
        self.assertEqual(ranked[0].company, "CrowdStrike")
        self.assertEqual(ranked[0].company_alumni, 162)
        self.assertEqual(ranked[1].company, "Sage")
        self.assertEqual(ranked[1].company_alumni, 60)
        self.assertEqual(ranked[2].company, "Verisk")
        self.assertEqual(ranked[2].company_alumni, 4)
        self.assertEqual(ranked[3].company, "GitLab")
        self.assertIsNone(ranked[3].company_alumni)

    def test_company_alumni_within_tiers(self):
        tier1_low_alumni = JobAlert(
            title="Frontend Engineer", company="Tier1 Co A", raw_salary=None,
            min_annual_salary=None, max_annual_salary=None,
            sort_key_salary=0.0, url_or_contact=None, source_email_subject="",
            company_rank=1, company_alumni=5
        )
        tier1_high_alumni = JobAlert(
            title="Frontend Engineer", company="Tier1 Co B", raw_salary=None,
            min_annual_salary=None, max_annual_salary=None,
            sort_key_salary=0.0, url_or_contact=None, source_email_subject="",
            company_rank=1, company_alumni=50
        )
        tier2_huge_alumni = JobAlert(
            title="Frontend Engineer", company="Tier2 Co C", raw_salary=None,
            min_annual_salary=None, max_annual_salary=None,
            sort_key_salary=0.0, url_or_contact=None, source_email_subject="",
            company_rank=2, company_alumni=200
        )

        sorted_jobs = JobReporter.rank_and_sort([tier2_huge_alumni, tier1_low_alumni, tier1_high_alumni])
        # Tier 1 comes first; within Tier 1, higher alumni (50 > 5) comes first; then Tier 2
        self.assertEqual(sorted_jobs[0].company, "Tier1 Co B")
        self.assertEqual(sorted_jobs[1].company, "Tier1 Co A")
        self.assertEqual(sorted_jobs[2].company, "Tier2 Co C")

    def test_alumni_rendering_table_markdown_csv(self):
        jobs = [
            JobAlert(
                title="Staff Engineer", company="Acme Corp", raw_salary=None,
                min_annual_salary=None, max_annual_salary=None,
                sort_key_salary=0.0, url_or_contact="https://acme.com", source_email_subject="",
                company_alumni=15
            )
        ]
        table = JobReporter.render(jobs, output_format="table")
        self.assertIn("Alumni", table)
        self.assertIn("15 alumni", table)

        markdown = JobReporter.render(jobs, output_format="markdown")
        self.assertIn("| Alumni |", markdown)
        self.assertIn("15 alumni", markdown)

        csv_out = JobReporter.render(jobs, output_format="csv")
        self.assertIn("company_alumni", csv_out)
        self.assertIn(",15,", csv_out)

    def test_target_pos_rendering_table_markdown_csv(self):
        jobs = [
            JobAlert(
                title="Staff Engineer", company="Acme Corp", raw_salary=None,
                min_annual_salary=None, max_annual_salary=None,
                sort_key_salary=0.0, url_or_contact="https://acme.com", source_email_subject="",
                company_rank=1, target_position="Principal Frontend"
            )
        ]
        table = JobReporter.render(jobs, output_format="table")
        self.assertIn("Target Pos", table)
        self.assertIn("Principal Frontend", table)
        self.assertNotIn("Pos. Rank", table)

        markdown = JobReporter.render(jobs, output_format="markdown")
        self.assertIn("| Target Pos |", markdown)
        self.assertIn("Principal Frontend", markdown)
        self.assertNotIn("Pos. Rank", markdown)

        csv_out = JobReporter.render(jobs, output_format="csv")
        self.assertIn("target_position", csv_out)
        self.assertIn("Principal Frontend", csv_out)
        self.assertNotIn("position_rank", csv_out)

    def test_cli_json_format_removed(self):
        parser = build_cli()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--format", "json"])
        # table, markdown, csv should be valid
        args = parser.parse_args(["--format", "csv"])
        self.assertEqual(args.format, "csv")

    def test_default_rankings_csv_file_validity(self):
        rankings_file = os.path.join(os.path.dirname(__file__), "rankings.csv")
        if os.path.exists(rankings_file):
            config = RankingConfig.load_from_file(rankings_file)
            self.assertGreaterEqual(len(config.companies), 10)
            self.assertIn("Sage", config.companies)
            self.assertEqual(config.companies["Sage"].position, "Senior Engineer")
            self.assertIn("Cisco", config.companies)
            self.assertEqual(config.companies["Cisco"].position, "Software Engineer IV")
            self.assertIn("Google", config.companies)


class TestCompanyAlumniExtraction(unittest.TestCase):
    def setUp(self):
        self.parser = JobParser()

    def test_alumni_extraction_formats(self):
        body = """
Lead React Developer
Verisk
Telangana, India

4 company alumni
View job: https://www.linkedin.com/comm/jobs/view/4450209035/
---------------------------------------------------------
Tech Lead
WPP
Gurgaon

19 company alumni
View job: https://www.linkedin.com/comm/jobs/view/4485820370/
---------------------------------------------------------
Senior Frontend Engineer
GitLab
Bengaluru

1 school alum
View job: https://www.linkedin.com/comm/jobs/view/4469392345/
---------------------------------------------------------
Frontend Developer
Single Alum Corp
Bengaluru

1 company alum
View job: https://www.linkedin.com/comm/jobs/view/4469399999/
---------------------------------------------------------
Mega Corp Lead
MegaCorp
Bengaluru

1,250 company alumni
View job: https://www.linkedin.com/comm/jobs/view/4469398888/
"""
        email = RawEmail(subject="LinkedIn Job Alert", body=body)
        jobs = self.parser.parse_email(email)
        self.assertEqual(len(jobs), 5)
        self.assertEqual(jobs[0].company, "Verisk")
        self.assertEqual(jobs[0].company_alumni, 4)

        self.assertEqual(jobs[1].company, "WPP")
        self.assertEqual(jobs[1].company_alumni, 19)

        # School alumni should NOT be parsed as company alumni
        self.assertEqual(jobs[2].company, "GitLab")
        self.assertIsNone(jobs[2].company_alumni)

        # Singular "1 company alum"
        self.assertEqual(jobs[3].company, "Single Alum Corp")
        self.assertEqual(jobs[3].company_alumni, 1)
        self.assertEqual(jobs[3].display_company_alumni, "1 alum")

        # Comma formatted "1,250 company alumni"
        self.assertEqual(jobs[4].company, "MegaCorp")
        self.assertEqual(jobs[4].company_alumni, 1250)
        self.assertEqual(jobs[4].display_company_alumni, "1,250 alumni")

    def test_min_alumni_filter(self):
        filt = JobFilter(min_alumni_threshold=10)
        job_low = JobAlert(
            title="Frontend Dev", company="Co Low", raw_salary=None,
            min_annual_salary=None, max_annual_salary=None, sort_key_salary=0.0,
            url_or_contact=None, source_email_subject="", company_alumni=4
        )
        job_high = JobAlert(
            title="Frontend Dev", company="Co High", raw_salary=None,
            min_annual_salary=None, max_annual_salary=None, sort_key_salary=0.0,
            url_or_contact=None, source_email_subject="", company_alumni=15
        )
        self.assertFalse(filt.is_match(job_low)[0])
        self.assertTrue(filt.is_match(job_high)[0])


    def test_company_preference_and_target_position_fields(self):
        pref = CompanyPreference(name="Google", position="Senior Frontend Engineer", rank=1)
        self.assertEqual(pref.name, "Google")
        self.assertEqual(pref.position, "Senior Frontend Engineer")
        self.assertEqual(pref.rank, 1)

        job = JobAlert(
            title="Frontend Dev",
            company="Google",
            raw_salary=None,
            min_annual_salary=None,
            max_annual_salary=None,
            sort_key_salary=0.0,
            url_or_contact=None,
            source_email_subject="Test",
            target_position="Senior Frontend Engineer"
        )
        self.assertEqual(job.target_position, "Senior Frontend Engineer")
        self.assertEqual(job.display_target_position, "Senior Frontend Engineer")

        job_empty = JobAlert(
            title="Frontend Dev",
            company="Google",
            raw_salary=None,
            min_annual_salary=None,
            max_annual_salary=None,
            sort_key_salary=0.0,
            url_or_contact=None,
            source_email_subject="Test"
        )
        self.assertIsNone(job_empty.target_position)
        self.assertEqual(job_empty.display_target_position, "-")


if __name__ == "__main__":
    unittest.main()

