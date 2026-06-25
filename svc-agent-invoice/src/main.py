from __future__ import annotations
"""
CLI wrapper for the Invoice Agent.

Keeps the same interface as before:
  python -m src.main --excel path.xlsx --print-json
"""

import argparse
import json
import os

from .invoice_agent import run_invoice_agent  # package-relative import


def _excel_to_csv_text(xlsx_path: str) -> str:
    try:
        import pandas as pd  # type: ignore
        df = pd.read_excel(xlsx_path)
        return df.to_csv(index=False)
    except Exception:
        # Fallback marker; the agent will treat this as a parse failure.
        return f"EXCEL_PATH::{os.path.abspath(xlsx_path)}"


def main():
    parser = argparse.ArgumentParser(description="Invoice Creation Agent (LLM + DOCX SOP)")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--excel", type=str, help="Path to Excel file (.xlsx)")
    src.add_argument("--paste", type=str, help="CSV text to process")

    parser.add_argument(
        "--hints",
        type=str,
        help="JSON object with hints (e.g. CompanyID) extracted by chat backend, optional.",
    )
    parser.add_argument("--text", type=str, help="Original user command (optional, for trace)")
    parser.add_argument("--print-json", action="store_true", help="Print raw JSON result to stdout")
    args = parser.parse_args()

    user_text = args.text or "Create invoices from the provided table using SOP rules."
    any_uploaded_text = _excel_to_csv_text(args.excel) if args.excel else args.paste

    hints = {}
    if args.hints:
        try:
            hints = json.loads(args.hints)
            if not isinstance(hints, dict):
                raise ValueError("hints must be a JSON object")
        except Exception as e:
            raise SystemExit(f"--hints must be valid JSON object: {e}")

    result = run_invoice_agent(
        user_text=user_text,
        any_uploaded_text=any_uploaded_text,
        hints=hints,
    )

    if args.print_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Human-readable summary
    if "error" in result:
        print(f"ERROR: {result.get('error')}: {result.get('details')}")
        return

    if result.get("needs_clarification"):
        print("Agent requires clarification:")
        for q in result.get("questions", []):
            print(f" - {q}")
        return

    jobs = result.get("jobs", [])
    created = 0
    failed = 0
    for j in jobs:
        resp = j.get("response", {})
        if resp.get("status") == "created":
            created += 1
        else:
            failed += 1

    print(f"Invoices attempted: {len(jobs)} | Created: {created} | Failed: {failed}")


if __name__ == "__main__":
    main()
