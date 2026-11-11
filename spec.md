# Specification: Position as Column in CSV and Company-First Hierarchy

## 1. Overview
Previously, `rankings.csv` stored positions as individual rows under a `category` column (e.g. `category=position` with position titles in the `name` column), alongside companies (`category=company`). Position ranking was also used as a secondary sorting criteria.

This feature simplifies and restructures `rankings.csv`:
- Eliminates the `category` column entirely.
- Makes `position` a dedicated column on each company row.
- Removes standalone position ranking (`position_rank`), keeping only company tier ranking (`company_rank`).
- Adjusts the global sorting hierarchy to prioritize:
  1. Compensation / Salary (Descending)
  2. Company Tier Rank (Ascending)
  3. LinkedIn Company Alumni Count (Descending)

---

## 2. CSV Format Specification

### 2.1 File: `rankings.csv`
- Header:
  ```csv
  company,position,rank,salary,rating,number_of_ratings
  ```
- Optional columns supported: `alumni` / `company_alumni`.
- Format Rules:
  - Header must strictly contain the `company` column (not `name`).
  - Each row corresponds to a company entry.
  - The `position` column represents the target/reference position for that company.
  - A single company may appear across multiple rows (e.g. targeting different positions or salary benchmarks).
  - The `rank` column represents the company tier (e.g. 1 for Tier 1, 2 for Tier 2; blank defaults to 99/unranked).
  - The `salary` column represents the expected/target compensation for that company or specific position.
  - `rating` and `number_of_ratings` represent company review metrics (e.g., Glassdoor).

---

## 3. Matching & Attribute Inheritance

### 3.1 Company-First Matching
- An incoming job alert matches against `rankings.csv` by matching the alert's company against the `company` column.
- First match wins: The first matching company entry found in the CSV is selected.
- Company-level details (`company_rank`, `rating`, `number_of_ratings`, `company_alumni`) are applied to the `JobAlert`.

### 3.2 Target Position & Salary Resolution
- The `position` from the matched row is assigned to `JobAlert.target_position` (and used for display in reports).
- Target salary fallback:
  - If the alert's title matches the row's `position`, the row's `salary` is applied as the job's `target_salary`.
  - If no position was specified on the CSV row, the row's `salary` is applied as a general company target salary.

---

## 4. Sorting Hierarchy

When ranking and sorting opportunities via `JobReporter.rank_and_sort`:
1. **Normalized Compensation / Salary** (Descending): Highest paying roles first. Unlisted salaries are sorted to the bottom.
2. **Company Tier Rank** (Ascending): Tier 1 before Tier 2, etc. (Unranked = 99).
3. **Company Alumni Count** (Descending): Roles with higher alumni connections prioritized next.

---

## 5. Reporting & Output Formats

### 5.1 Terminal Table & Markdown
- Remove the `Pos. Rank` column.
- Add `Target Pos` column displaying `job.target_position` (or `-` if none configured).
- Display Columns:
  `# | Job Title | Company | Co. Rank | Target Pos | Alumni | Rating | Salary | Apply / Info`

### 5.2 CSV Output (`--format csv`)
- Remove `position_rank`.
- Replace `matched_position` / `position_rank` with `target_position`.

### 5.3 Removal of JSON Output Support
- Remove `json` from `--format` CLI choices (`table`, `markdown`, `csv` only).
- Remove `json` rendering branch in `JobReporter.render`.

