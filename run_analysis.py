import os
import re
import json
import argparse
import subprocess
from datetime import datetime, timedelta

# Default constants
DEFAULT_DATASET = "bigquery-public-data.thelook_ecommerce.events"
DEFAULT_END = datetime(2026, 8, 16)  # Aligned to Look dataset bounds
DEFAULT_START = DEFAULT_END - timedelta(days=180)

def parse_args():
    parser = argparse.ArgumentParser(description="Dynamic N-Step Discovery GA4 Analytical Pipeline")
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="Target BigQuery GA4 event table")
    parser.add_argument("--start", default=None, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--output-dir", default="reports", help="Directory to save report history")
    return parser.parse_args()

def run_query(sql):
    # Runs bq query via subprocess with stdin to bypass Windows command-line quoting issues
    process = subprocess.Popen(
        ['bq', 'query', '--use_legacy_sql=false', '--format=json'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        shell=True
    )
    stdout, stderr = process.communicate(input=sql)
    if process.returncode != 0:
        raise Exception(stderr.strip() or stdout.strip())
    
    rows = json.loads(stdout)
    if not rows:
        return [], []
    columns = list(rows[0].keys())
    return columns, rows

def expand_sql_loops(sql_template, steps):
    # Matches: -- [LOOP_START] ... -- [LOOP_END]
    # Extracts the body and repeats it for every step, joining them with a comma and newline
    # Supports both {step} and {next_step} for step-over-step dropoffs
    pattern = r"--\s*\[LOOP_START\](.*?)--\s*\[LOOP_END\]"
    
    def replace_block(match):
        body = match.group(1).strip()
        lines = []
        for i, step in enumerate(steps):
            if "{next_step}" in body:
                # If doing adjacent step calculations, skip the final step since there is no next step
                if i == len(steps) - 1:
                    continue
                next_step = steps[i+1]
                line_content = body.replace("{step}", step).replace("{next_step}", next_step)
            else:
                line_content = body.replace("{step}", step)
            lines.append(line_content)
        return ",\n    ".join(lines)
        
    return re.sub(pattern, replace_block, sql_template, flags=re.DOTALL)

def parameterize_sql(sql_content, dataset, start_date, end_date, steps, is_raw_ga4):
    # Replace target table
    sql = re.sub(
        r"`bigquery-public-data\.thelook_ecommerce\.events`|`bigquery-public-data\.thelook_ecommerce\.events`|\'bigquery-public-data\.thelook_ecommerce\.events\'",
        f"windowed" if is_raw_ga4 else f"`{dataset}`",
        sql_content
    )
    sql = re.sub(r"DATE\s+'\d{4}-\d{2}-\d{2}'\s+AND\s+DATE\s+'\d{4}-\d{2}-\d{2}'", f"DATE '{start_date}' AND DATE '{end_date}'", sql)
    sql = re.sub(r"'\d{4}-\d{2}-\d{2}'\s+AND\s+'\d{4}-\d{2}-\d{2}'", f"'{start_date}' AND '{end_date}'", sql)
    
    # Expand loops first
    sql = expand_sql_loops(sql, steps)
    
    # Bind the final conversion step dynamically
    if steps:
        sql = sql.replace("{final_step}", steps[-1])
    
    # Inject translation CTE wrapper if targeting raw nested GA4 schemas
    if is_raw_ga4:
        adapter_cte = f"""WITH windowed AS (
  SELECT 
    TIMESTAMP_MICROS(event_timestamp) AS created_at,
    COALESCE(user_id, user_pseudo_id) AS user_id,
    CAST((SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS STRING) AS session_id,
    COALESCE(
      (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location'),
      event_name
    ) AS id,
    event_name AS event_type,
    device.web_info.browser AS browser,
    traffic_source.source AS traffic_source,
    geo.state AS state
  FROM `{dataset}`
  WHERE _TABLE_SUFFIX BETWEEN '{start_date.replace("-", "")}' AND '{end_date.replace("-", "")}'
),
"""
        # Inject the CTE right after the WITH of the query
        sql = sql.replace("WITH windowed AS (", adapter_cte)
        # If the query did not use "WITH windowed AS", prepend it
        if "WITH windowed AS" not in sql_content:
            sql = f"{adapter_cte.rstrip()[:-1]}\n{sql}"
            
    return sql

def format_markdown_table(columns, rows):
    if not rows:
        return "*No data returned*"
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join(["---"] * len(columns)) + " |"
    formatted_rows = []
    for r in rows:
        line = "| " + " | ".join(str(r.get(col, "")) for col in columns) + " |"
        formatted_rows.append(line)
    return "\n".join([header, divider] + formatted_rows)
def main():
    args = parse_args()
    
    import sys
    if not args.start:
        if sys.stdin.isatty():
            try:
                user_start = input(f"Enter start date (YYYY-MM-DD) [default: {DEFAULT_START.strftime('%Y-%m-%d')}]: ").strip()
                args.start = user_start if user_start else DEFAULT_START.strftime('%Y-%m-%d')
            except Exception:
                args.start = DEFAULT_START.strftime('%Y-%m-%d')
        else:
            args.start = DEFAULT_START.strftime('%Y-%m-%d')
            
    if not args.end:
        if sys.stdin.isatty():
            try:
                user_end = input(f"Enter end date (YYYY-MM-DD) [default: {DEFAULT_END.strftime('%Y-%m-%d')}]: ").strip()
                args.end = user_end if user_end else DEFAULT_END.strftime('%Y-%m-%d')
            except Exception:
                args.end = DEFAULT_END.strftime('%Y-%m-%d')
        else:
            args.end = DEFAULT_END.strftime('%Y-%m-%d')
            
    print(f"Initializing dynamic discovery pipeline on: {args.dataset}...")
    
    os.makedirs(args.output_dir, exist_ok=True)
    run_time = datetime.now()
    run_date_str = run_time.strftime("%Y-%m-%d %H:%M:%S")
    file_suffix = run_time.strftime("%Y%m%d_%H%M%S")
    
    report_content = []
    report_content.append(f"# Dynamic GA4 Discovery Analytics Report")
    report_content.append(f"* **Executed On:** {run_date_str}")
    report_content.append(f"* **Target Dataset:** `{args.dataset}`")
    report_content.append(f"* **Analysis Window:** `{args.start}` to `{args.end}`\n")
    
    # 1. Check if table matches raw GA4 schema or flat schema to resolve event type column name
    is_raw_ga4 = "analytics_" in args.dataset or "events_" in args.dataset or "google-analytics" in args.dataset
    
    # 2. DISCOVER THE ACTUAL EVENT INVENTORY AND SEQUENCE
    print("Discovering event inventory directly from dataset...")
    if is_raw_ga4:
        discovery_sql = f"""
        SELECT event_name AS event_type, COUNT(*) AS n, ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER(), 1) AS pct
        FROM `{args.dataset}`
        WHERE _TABLE_SUFFIX BETWEEN '{args.start.replace("-", "")}' AND '{args.end.replace("-", "")}'
        GROUP BY event_name
        ORDER BY n DESC
        """
    else:
        discovery_sql = f"""
        SELECT event_type, COUNT(*) AS n, ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER(), 1) AS pct
        FROM `{args.dataset}`
        WHERE DATE(created_at) BETWEEN DATE '{args.start}' AND DATE '{args.end}'
        GROUP BY event_type
        ORDER BY n DESC
        """
        
    try:
        cols, event_rows = run_query(discovery_sql)
        report_content.append("## Discovered Event Inventory")
        report_content.append(format_markdown_table(cols, event_rows))
        report_content.append("\n")
        
        discovered_events = [row['event_type'] for row in event_rows]
        print(f"Discovered {len(discovered_events)} event types: {discovered_events}")
    except Exception as e:
        print(f"Failed to scan event inventory: {e}")
        report_content.append(f"## Event Inventory\n*Discovery scan failed: {e}*\n")
        return

    # Filter out common navigation noise events to extract the conversion funnel progression steps
    funnel_exclusions = {"home", "page_view", "session_start", "user_engagement", "first_visit"}
    funnel_steps = [ev for ev in discovered_events if ev not in funnel_exclusions]
    
    if not funnel_steps:
        funnel_steps = discovered_events
        
    print(f"Dynamically generated funnel sequence: {funnel_steps}")
    report_content.append("## Dynamically Constructed Funnel Steps")
    report_content.append(f"Based on discovery rules, the conversion path is evaluated across: " + " &rarr; ".join(f"`{s}`" for s in funnel_steps) + "\n")
    
    # 3. RUN EACH QUERY BY EXPANDING TEMPLATE LOOPS DYNAMICALLY
    queries_dir = "queries"
    query_order = [
        "baseline_funnel.sql",
        "dropoff_traffic.sql",
        "dropoff_browser.sql",
        "segment_state.sql",
        "user_conv_traffic.sql",
        "segment_browser.sql",
        "segment_traffic.sql"
    ]
    
    for filename in query_order:
        filepath = os.path.join(queries_dir, filename)
        if not os.path.exists(filepath):
            continue
            
        print(f"Processing and executing template: {filename}...")
        with open(filepath, 'r', encoding='utf-8') as f:
            raw_sql = f.read()
            
        parameterized_sql_str = parameterize_sql(raw_sql, args.dataset, args.start, args.end, funnel_steps, is_raw_ga4)
        
        try:
            cols, rows = run_query(parameterized_sql_str)
            section_title = filename.replace('.sql', '').replace('_', ' ').title()
            report_content.append(f"## {section_title}")
            report_content.append(format_markdown_table(cols, rows))
            report_content.append("\n")
        except Exception as e:
            print(f"Error running {filename}: {e}")
            report_content.append(f"## {filename}\n*Execution Error:* {e}\n")
            
    # Save outputs
    report_md = "\n".join(report_content)
    
    # Historical report save
    history_file_path = os.path.join(args.output_dir, f"report_{file_suffix}.md")
    with open(history_file_path, 'w', encoding='utf-8') as f:
        f.write(report_md)
    print(f"Saved historical snapshot to: {history_file_path}")
    
    # Overwrite master root REPORT.md with the latest run
    with open("REPORT.md", 'w', encoding='utf-8') as f:
        f.write(report_md)
    print(f"Updated current master report: REPORT.md")

if __name__ == "__main__":
    main()
