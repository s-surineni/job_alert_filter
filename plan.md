# Implementation Plan: Position as Column in CSV

A numbered plan of small, testable steps to transition `rankings.csv` to use `position` as a column, update data models, matching logic, sorting hierarchy, outputs, and tests.

---

## Step 1: Update Data Models in `job_alert_filter.py`
- Modify `CompanyPreference` dataclass to include `position: Optional[str] = None`.
- Update `JobAlert` dataclass:
  - Add `target_position: Optional[str] = None`.
  - Remove or deprecate `position_rank` (keep default 99 for backward compatibility if referenced, but remove from active sorting/reporting).
  - Update display helpers: replace `display_position_rank` with `display_target_position`.
- **Test criteria**: Python syntax check and existing basic dataclass instantiation tests pass.

---

## Step 2: Refactor CSV Loader in `RankingConfig`
- In `RankingConfig.load_from_csv`:
  - Update column identification to look strictly for `company`, `position`, `rank`, `salary`, `rating`, `number_of_ratings`, `alumni` (no fallback to `name`).
  - Parse rows without requiring a `category` column.
  - Store entries preserving file order so that "first matching company found in CSV" semantics are respected.
  - Populate `CompanyPreference(name=company, position=..., rank=..., target_salary=..., rating=..., number_of_ratings=..., company_alumni=...)`.
- **Test criteria**: Unit test loading a sample CSV with `company,position,rank,salary,rating,number_of_ratings` asserting correct fields in `RankingConfig`.

---

## Step 3: Implement Company-First Matching and Target Position/Salary Logic
- In `RankingConfig.apply_to_job(job: JobAlert)`:
  - Iterate through preferences in CSV order.
  - Match job company against `pref.name` (exact, substring, or token overlap).
  - Select the first matching company row.
  - Assign company tier rank, rating, number of ratings, and alumni.
  - Assign `job.target_position = pref.position`.
  - Assign `target_salary`:
    - If `pref.position` is defined and matches the job's title (or if no position is defined in the row), apply `pref.target_salary`.
  - Stop after the first company match.
- **Test criteria**: Unit test verifying first-match company behavior and target position / salary inheritance.

---

## Step 4: Update Sorting Priority Order
- In `JobReporter.rank_and_sort`:
  - Update sort key to follow the user-specified order (3, 2, 1):
    1. Salary / compensation (`-j.sort_key_salary`)
    2. Company tier rank (`j.company_rank`)
    3. LinkedIn company alumni (`-alumni`)
- **Test criteria**: Unit test asserting that high salary beats lower salary even with higher tier/alumni, tier breaks salary ties, and alumni breaks tier ties.

---

## Step 5: Update Output Formatters & Remove JSON Output
- In `JobReporter._render_terminal_table`:
  - Replace `Pos. Rank` column with `Target Pos`.
- In `JobReporter._render_markdown`:
  - Replace `Pos. Rank` column with `Target Pos`.
- In `JobReporter._render_csv`:
  - Replace `position_rank` with `target_position`.
- Remove `json` output format from `JobReporter.render` and argparse `--format` choices (`choices=["table", "markdown", "csv"]`).
- Update CLI summary message (e.g. `Loaded rankings from CSV (...)`).
- **Test criteria**: Verify output format strings for table, markdown, and CSV contain `Target Pos` / `target_position` instead of `Pos. Rank`. Verify `--format json` is rejected or removed.

---

## Step 6: Convert `rankings.csv` to the New Format
- Rewrite `/home/sampath/projects/job_alert_filter/rankings.csv`:
  - Use header `company,position,rank,salary,rating,number_of_ratings`.
  - Remove all `category,position,...` rows.
  - Migrate all company rows into the new format with their respective position entries.
- **Test criteria**: Verify `rankings.csv` has valid syntax and loads cleanly through `RankingConfig.load_from_file("rankings.csv")`.

---

## Step 7: Update and Expand Unit Tests
- In `test_job_alert_filter.py`:
  - Update `test_csv_loading_and_matching` with the new CSV format.
  - Update `test_ranked_sorting_order` to reflect `Salary -> Company Rank -> Alumni`.
  - Add tests verifying multiple rows for the same company and first-match selection.
  - Add tests verifying `Target Pos` rendering in table, markdown, and CSV.
- Run `python3 -m unittest discover` to ensure 100% test pass rate.
