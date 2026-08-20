# Agnostic GA4 E-commerce Analytics Engine

This repository provides an **agnostic analytics runner** to execute GA4 e-commerce metrics and funnel auditing directly against Google Cloud BigQuery.

---

## Getting Started

### 1. Prerequisites
* Authenticated gcloud CLI (`gcloud auth login`)
* Configured billing project (`gcloud config set project <PROJECT_ID>`)
* Python 3.x installed with the BigQuery library:
  ```bash
  pip install google-cloud-bigquery
  ```

### 2. Running a Scan
To run a scan against the default public dataset for the default window:
```bash
python run_analysis.py
```

### 3. Customizing the Target & Dates
You can point the system at any client's GA4 export table and define custom dates:
```bash
python run_analysis.py --dataset my-gcp-project.analytics_12345.events_2026* --start 2026-06-01 --end 2026-06-30
```

---

## Architecture & Outputs

* **No database pollution:** The system queries directly in-memory.
* **History Log (`reports/`)**: Every execution creates a timestamped markdown file (e.g. `reports/report_YYYYMMDD_HHMMSS.md`) so you keep all historical scans.
* **Latest Report (`REPORT.md`)**: The root report is automatically updated to match the latest scan.
* **Trend Log (`reports/history_log.json`)**: A machine-readable log containing historical baseline metrics (like conversion rate) is appended to on each run, allowing agents to chart and check trends across runs.
