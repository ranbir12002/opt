# src/config.py
"""Configuration for schedule agent."""

import os
from dotenv import load_dotenv

load_dotenv()

# Maximum clarifications for interactive UI
# If exceeded, generate pre-filled Excel instead
MAX_INTERACTIVE_CLARIFICATIONS = 5

# Session timeout for clarifications (seconds)
CLARIFICATION_TIMEOUT_SECONDS = 30 * 60  # 30 minutes

# Corrected Excel file TTL (seconds)
CORRECTED_EXCEL_TTL_SECONDS = 60 * 60  # 1 hour

# Fuzzy matching threshold for staff names (0-100)
FUZZY_MATCH_THRESHOLD = 70

# Default Simpro company ID
DEFAULT_COMPANY_ID = int(os.getenv("SIMPRO_COMPANY_ID", "2"))

# SOP file path
SOP_MD_PATH = os.getenv(
    "SCHEDULE_SOP_MD_PATH",
    os.path.join(os.path.dirname(__file__), "sop", "schedule_sop.md"),
)
