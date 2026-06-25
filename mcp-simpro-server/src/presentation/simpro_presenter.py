"""
Simpro Data Presenter - IMPROVED VERSION

Uses HTML tables for better rendering and cleaner structure.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import json

from src.utils import get_logger

logger = get_logger(__name__)


# ===================================================================
# Configuration
# ===================================================================
USE_HTML_TABLES = True  # Set to False for markdown tables
MAX_CUSTOM_FIELDS_DISPLAY = 10  # Show only first 10 custom fields by default
COMPACT_MODE = True  # More compact output


# ===================================================================
# Helper Functions
# ===================================================================
def escape_html(text: str) -> str:
    """Escape HTML special characters"""
    if not isinstance(text, str):
        text = str(text)
    return (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;"))


def format_currency(value: Any) -> str:
    """Format number as currency"""
    try:
        num = float(value) if value is not None else 0
        return f"${num:,.2f}"
    except:
        return str(value)


def safe_get(obj: Any, key: str, default: str = "-") -> str:
    """Safely get value from dict"""
    if not isinstance(obj, dict):
        return default
    value = obj.get(key, default)
    if value is None or value == "":
        return default
    return str(value)


def create_html_table(headers: List[str], rows: List[List[str]], caption: str = None) -> str:
    """Create HTML table"""
    html = '<table style="width:100%; border-collapse: collapse; margin: 10px 0;">\n'
    
    if caption:
        html += f'  <caption style="font-weight: bold; text-align: left; padding: 5px;">{caption}</caption>\n'
    
    # Headers
    html += '  <thead>\n    <tr style="background-color: #f0f0f0;">\n'
    for header in headers:
        html += f'      <th style="border: 1px solid #ddd; padding: 8px; text-align: left;">{escape_html(header)}</th>\n'
    html += '    </tr>\n  </thead>\n'
    
    # Rows
    html += '  <tbody>\n'
    for row in rows:
        html += '    <tr>\n'
        for cell in row:
            html += f'      <td style="border: 1px solid #ddd; padding: 8px;">{escape_html(cell)}</td>\n'
        html += '    </tr>\n'
    html += '  </tbody>\n'
    html += '</table>\n'
    
    return html


def create_markdown_table(headers: List[str], rows: List[List[str]]) -> str:
    """Create markdown table"""
    # Header row
    md = "| " + " | ".join(headers) + " |\n"
    
    # Separator
    md += "|" + "|".join(["---"] * len(headers)) + "|\n"
    
    # Data rows
    for row in rows:
        md += "| " + " | ".join(row) + " |\n"
    
    return md


def create_table(headers: List[str], rows: List[List[str]], caption: str = None) -> str:
    """Create table (HTML or Markdown based on config)"""
    if USE_HTML_TABLES:
        return create_html_table(headers, rows, caption)
    else:
        result = ""
        if caption:
            result += f"**{caption}**\n\n"
        result += create_markdown_table(headers, rows)
        return result


# ===================================================================
# Core Presenter Function
# ===================================================================
def format_simpro_data(tool_name: str, data: Dict[str, Any]) -> str:
    """Format Simpro data based on tool type"""
    logger.debug(f"Formatting data from tool: {tool_name}")
    
    if not data.get("success"):
        return format_error(tool_name, data)
    
    formatters = {
        "search_jobs": format_job_list,
        "get_job_details": format_job_details,
        "get_job_sections": format_job_sections,
        "search_customers": format_customer_list,
        "get_customer_details": format_customer_details,
        "get_job_section_cost_centres": format_cost_centre_list,
        "get_job_cost_centre_details": format_cost_centre_details,
        "get_credit_note_details": format_credit_note_details,
        "search_invoices": format_invoice_list,
        "get_invoice_details": format_invoice_details,
    }
    
    formatter = formatters.get(tool_name, format_generic)
    
    try:
        return formatter(data)
    except Exception as e:
        logger.error(f"Formatting error for {tool_name}: {e}")
        return format_generic(data)


# ===================================================================
# Error Formatter
# ===================================================================
def format_error(tool_name: str, data: Dict[str, Any]) -> str:
    """Format error responses"""
    error_msg = data.get("error", "Unknown error")
    return f"<div style='color: red; padding: 10px; border: 1px solid red; border-radius: 5px;'>❌ <strong>Error in {tool_name}:</strong><br>{escape_html(error_msg)}</div>"


# ===================================================================
# Job Formatters
# ===================================================================
def format_job_list(data: Dict[str, Any]) -> str:
    """Format search_jobs results"""
    jobs = data.get("data", {}).get("jobs", [])
    
    if not jobs:
        return "<p><em>No jobs found.</em></p>"
    
    result = f"<h3>📋 Job Search Results ({len(jobs)} jobs)</h3>\n\n"
    
    headers = ["ID", "Name", "Customer", "Status", "Total"]
    rows = []
    
    for job in jobs[:50]:
        customer = job.get("Customer", {})
        customer_name = customer.get("CompanyName", "-") if isinstance(customer, dict) else "-"
        
        total_obj = job.get("Total", {})
        total = format_currency(total_obj.get("IncTax")) if isinstance(total_obj, dict) else "-"
        
        rows.append([
            safe_get(job, "ID"),
            safe_get(job, "Name"),
            customer_name,
            safe_get(job, "Status"),
            total
        ])
    
    result += create_table(headers, rows)
    
    if len(jobs) > 50:
        result += f"<p><em>Showing 50 of {len(jobs)} jobs</em></p>"
    
    return result


def format_job_details(data: Dict[str, Any]) -> str:
    """Format get_job_details results - IMPROVED"""
    job = data.get("data", {}).get("job", {})
    
    if not job:
        return "<p><em>No job data found.</em></p>"
    
    result = f"<h2>🔧 Job: {escape_html(safe_get(job, 'Name'))} (ID: {safe_get(job, 'ID')})</h2>\n\n"
    
    # === SECTION 1: Key Information (Compact) ===
    result += "<h3>📌 Key Information</h3>\n"
    
    key_info = [
        ["Status", safe_get(job, "Status")],
        ["Stage", safe_get(job, "Stage")],
        ["Type", safe_get(job, "Type")],
        ["Date Issued", safe_get(job, "DateIssued")],
        ["Due Date", safe_get(job, "DueDate")],
    ]
    
    result += create_table(["Field", "Value"], key_info)
    
    # === SECTION 2: Customer & Site ===
    customer = job.get("Customer", {})
    site = job.get("Site", {})
    
    if customer or site:
        result += "<h3>👥 Customer & Location</h3>\n"
        contact_info = []
        
        if isinstance(customer, dict):
            contact_info.append(["Customer ID", safe_get(customer, "ID")])
            contact_info.append(["Company", safe_get(customer, "CompanyName")])
        
        if isinstance(site, dict):
            contact_info.append(["Site", safe_get(site, "Name")])
        
        result += create_table(["Field", "Value"], contact_info)
    
    # === SECTION 3: Financial Summary (Prominent) ===
    total = job.get("Total", {})
    if isinstance(total, dict):
        result += "<h3>💰 Financial Summary</h3>\n"
        
        financial_summary = [
            ["Subtotal (Ex Tax)", format_currency(total.get("ExTax"))],
            ["Tax", format_currency(total.get("Tax"))],
            ["<strong>Total (Inc Tax)</strong>", f"<strong>{format_currency(total.get('IncTax'))}</strong>"],
        ]
        
        result += create_table(["Item", "Amount"], financial_summary)
    
    # === SECTION 4: Cost Breakdown (If available) ===
    totals = job.get("Totals", {})
    if isinstance(totals, dict):
        result += "<h3>📊 Cost Breakdown</h3>\n"
        
        breakdown_headers = ["Category", "Actual", "Estimate", "Variance"]
        breakdown_rows = []
        
        for category in ["Materials", "Resources", "ContractorLabour", "Plant", "Miscellaneous"]:
            cat_data = totals.get(category, {})
            if isinstance(cat_data, dict):
                actual_val = cat_data.get("Actual", {})
                estimate_val = cat_data.get("Estimate", {})
                
                actual = actual_val.get("IncTax", 0) if isinstance(actual_val, dict) else 0
                estimate = estimate_val.get("IncTax", 0) if isinstance(estimate_val, dict) else 0
                variance = actual - estimate
                
                breakdown_rows.append([
                    category,
                    format_currency(actual),
                    format_currency(estimate),
                    format_currency(variance)
                ])
        
        if breakdown_rows:
            result += create_table(breakdown_headers, breakdown_rows)
    
    # === SECTION 5: Custom Fields (Collapsible/Limited) ===
    custom_fields = job.get("CustomFields", [])
    if custom_fields and isinstance(custom_fields, list):
        # Filter out empty/null fields
        populated_fields = [f for f in custom_fields if f.get("Value") not in [None, "", "-"]]
        
        if populated_fields:
            result += f"<h3>🔖 Custom Fields ({len(populated_fields)} set)</h3>\n"
            
            field_rows = []
            for field in populated_fields[:MAX_CUSTOM_FIELDS_DISPLAY]:
                if isinstance(field, dict):
                    field_rows.append([
                        safe_get(field, "Name"),
                        safe_get(field, "Value")
                    ])
            
            result += create_table(["Field Name", "Value"], field_rows)
            
            if len(populated_fields) > MAX_CUSTOM_FIELDS_DISPLAY:
                result += f"<p><em>... and {len(populated_fields) - MAX_CUSTOM_FIELDS_DISPLAY} more custom fields</em></p>"
    
    return result


def format_job_sections(data: Dict[str, Any]) -> str:
    """Format get_job_sections results"""
    sections = data.get("data", {}).get("sections", [])
    
    if not sections:
        return "<p><em>No sections found for this job.</em></p>"
    
    result = f"<h3>📑 Job Sections ({len(sections)} sections)</h3>\n\n"
    
    headers = ["ID", "Name", "Total (Inc Tax)"]
    rows = []
    
    for section in sections:
        if isinstance(section, dict):
            total = section.get("Total", {})
            total_val = format_currency(total.get("IncTax")) if isinstance(total, dict) else "-"
            
            rows.append([
                safe_get(section, "ID"),
                safe_get(section, "Name"),
                total_val
            ])
    
    result += create_table(headers, rows)
    return result


# ===================================================================
# Cost Centre Formatters
# ===================================================================
def format_cost_centre_list(data: Dict[str, Any]) -> str:
    """Format get_job_section_cost_centres results"""
    cost_centres = data.get("data", {}).get("cost_centres", [])
    
    if not cost_centres:
        return "<p><em>No cost centres found for this section.</em></p>"
    
    result = f"<h3>💼 Cost Centres ({len(cost_centres)} items)</h3>\n\n"
    
    headers = ["ID", "Name", "Type", "Total", "% Complete"]
    rows = []
    
    for cc in cost_centres:
        if isinstance(cc, dict):
            cc_type = cc.get("CostCenter", {})
            type_name = cc_type.get("Name", "-") if isinstance(cc_type, dict) else "-"
            
            total = cc.get("Total", {})
            total_val = format_currency(total.get("IncTax")) if isinstance(total, dict) else "-"
            
            pct = cc.get("PercentComplete", 0) or 0
            
            rows.append([
                safe_get(cc, "ID"),
                safe_get(cc, "Name"),
                type_name,
                total_val,
                f"{pct}%"
            ])
    
    result += create_table(headers, rows)
    return result


def format_cost_centre_details(data: Dict[str, Any]) -> str:
    """Format get_job_cost_centre_details results"""
    cc = data.get("data", {}).get("cost_centre", {})
    
    if not cc:
        return "<p><em>No cost centre data found.</em></p>"
    
    result = f"<h3>💼 Cost Centre: {escape_html(safe_get(cc, 'Name'))} (ID: {safe_get(cc, 'ID')})</h3>\n\n"
    
    cc_type = cc.get("CostCenter", {})
    total = cc.get("Total", {})
    
    info_rows = [
        ["Name", safe_get(cc, "Name")],
        ["Type", cc_type.get("Name", "-") if isinstance(cc_type, dict) else "-"],
        ["Job ID", safe_get(cc, "JobID")],
        ["% Complete", f"{cc.get('PercentComplete', 0)}%"],
    ]
    
    if isinstance(total, dict):
        info_rows.extend([
            ["Total (Ex Tax)", format_currency(total.get("ExTax"))],
            ["Tax", format_currency(total.get("Tax"))],
            ["<strong>Total (Inc Tax)</strong>", f"<strong>{format_currency(total.get('IncTax'))}</strong>"],
        ])
    
    result += create_table(["Field", "Value"], info_rows)
    return result


# ===================================================================
# Credit Note Formatter
# ===================================================================
def format_credit_note_details(data: Dict[str, Any]) -> str:
    """Format credit note details - IMPROVED"""
    cn = data.get("data", {}).get("credit_note", {})
    
    if not cn:
        return "<p><em>No credit note data found.</em></p>"
    
    result = f"<h2>💳 Credit Note #{safe_get(cn, 'ID')}</h2>\n\n"
    
    # === Basic Info ===
    result += "<h3>📋 Credit Note Information</h3>\n"
    
    basic_info = [
        ["ID", safe_get(cn, "ID")],
        ["Type", safe_get(cn, "Type")],
        ["Status", safe_get(cn, "Status")],
        ["Date Issued", safe_get(cn, "DateIssued")],
        ["Period", f"{safe_get(cn, 'PeriodStart')} to {safe_get(cn, 'PeriodEnd')}"],
    ]
    
    result += create_table(["Field", "Value"], basic_info)
    
    # === Customer ===
    customer = cn.get("Customer", {})
    if isinstance(customer, dict):
        result += "<h3>👤 Customer</h3>\n"
        
        cust_info = [
            ["ID", safe_get(customer, "ID")],
            ["Name", safe_get(customer, "CustomerName")],
            ["Address", safe_get(customer, "CustomerAddress")],
            ["Phone", safe_get(customer, "CustomerPhone")],
        ]
        
        result += create_table(["Field", "Value"], cust_info)
    
    # === Financial Totals ===
    result += "<h3>💰 Totals</h3>\n"
    
    totals = [
        ["Total (Ex Tax)", format_currency(cn.get("TotalExTax"))],
        ["Total Tax", format_currency(cn.get("TotalTax"))],
        ["<strong>Total (Inc Tax)</strong>", f"<strong>{format_currency(cn.get('TotalIncTax'))}</strong>"],
    ]
    
    result += create_table(["Item", "Amount"], totals)
    
    # === Job Details (if linked) ===
    job = cn.get("Job", {})
    if isinstance(job, dict) and job.get("JobID"):
        result += "<h3>🔧 Linked Job</h3>\n"
        
        job_info = [
            ["Job ID", safe_get(job, "JobID")],
            ["Salesperson", safe_get(job, "Salesperson")],
            ["Site", safe_get(job, "Site")],
            ["Converted Quote", safe_get(job, "ConvertedQuote")],
        ]
        
        # Cost center details
        cost_center = job.get("CostCenterDetails", {})
        if isinstance(cost_center, dict):
            job_info.extend([
                ["Cost Center ID", safe_get(cost_center, "CostCenterID")],
                ["Cost Center Name", safe_get(cost_center, "Name")],
                ["Cost Center Type", safe_get(cost_center.get("CostCenter", {}), "Name") if isinstance(cost_center.get("CostCenter"), dict) else "-"],
            ])
        
        result += create_table(["Field", "Value"], job_info)
    
    return result


# ===================================================================
# Customer/Invoice Formatters
# ===================================================================
def format_customer_list(data: Dict[str, Any]) -> str:
    """Format search_customers results"""
    customers = data.get("data", {}).get("customers", [])
    
    if not customers:
        return "<p><em>No customers found.</em></p>"
    
    result = f"<h3>👥 Customer Search Results ({len(customers)} customers)</h3>\n\n"
    
    headers = ["ID", "Company", "Type", "Email", "Phone"]
    rows = []
    
    for c in customers[:50]:
        rows.append([
            safe_get(c, "ID"),
            safe_get(c, "CompanyName"),
            safe_get(c, "Type"),
            safe_get(c, "Email"),
            safe_get(c, "Phone")
        ])
    
    result += create_table(headers, rows)
    return result


def format_customer_details(data: Dict[str, Any]) -> str:
    """Format get_customer_details results"""
    customer = data.get("data", {}).get("customer", {})
    
    if not customer:
        return "<p><em>No customer data found.</em></p>"
    
    result = f"<h2>👤 Customer: {escape_html(safe_get(customer, 'CompanyName'))} (ID: {safe_get(customer, 'ID')})</h2>\n\n"
    
    info = [
        ["Type", safe_get(customer, "Type")],
        ["Email", safe_get(customer, "Email")],
        ["Phone", safe_get(customer, "Phone")],
        ["Mobile", safe_get(customer, "Mobile")],
        ["ABN", safe_get(customer, "ABN")],
    ]
    
    result += create_table(["Field", "Value"], info)
    return result


def format_invoice_list(data: Dict[str, Any]) -> str:
    """Format search_invoices results"""
    return format_generic(data)


def format_invoice_details(data: Dict[str, Any]) -> str:
    """Format get_invoice_details results"""
    return format_generic(data)


# ===================================================================
# Generic Formatter
# ===================================================================
def format_generic(data: Dict[str, Any]) -> str:
    """Generic formatter - returns clean JSON"""
    actual_data = data.get("data", data)
    
    # Try to create a simple summary
    if isinstance(actual_data, dict):
        json_str = json.dumps(actual_data, indent=2, default=str)
        return f"<pre style='background-color: #f5f5f5; padding: 10px; border-radius: 5px; overflow-x: auto;'>{escape_html(json_str[:5000])}</pre>"
    elif isinstance(actual_data, list):
        return f"<p>Found {len(actual_data)} items</p><pre style='background-color: #f5f5f5; padding: 10px; border-radius: 5px; overflow-x: auto;'>{escape_html(json.dumps(actual_data[:10], indent=2, default=str))}</pre>"
    else:
        return f"<p>{escape_html(str(actual_data))}</p>"