# Optificial Demo Script
### AI-Powered Back Office Assistant for Construction Companies
**Audience:** Non-technical construction professionals
**Duration:** ~20-25 minutes
**Tone:** Conversational, practical, no jargon

---

## Opening (2 minutes)

> **Presenter:**
>
> "Good morning everyone. Today I'm going to show you Optificial — an AI assistant built specifically for construction companies that use Simpro.
>
> Think of it as a smart office assistant that lives in your browser. You talk to it in plain English — no training needed, no complicated forms — and it handles your scheduling, invoicing, and work orders for you.
>
> Let me show you what I mean."

---

## Part 1 — The Chat Interface (2 minutes)

> **Presenter:** *(Opens the application in browser)*
>
> "This is the main screen. It looks like a chat window — because that's exactly what it is. You type what you need, and the assistant figures out what to do.
>
> You can also toggle between light and dark mode up here — whatever's easier on your eyes.
>
> There are two ways to work with it:
> 1. **Type a question** — like you would text a colleague
> 2. **Upload a file** — upload a spreadsheet and tell it what to do with it
>
> Let's start with some simple questions."

---

## Part 2 — Asking Questions About Your Data (3 minutes)

> **Presenter:** *(Types into chat)*
>
> **Demo:** *"Show me all active jobs"*
>
> "I've just asked it to show me our active jobs. Watch — it goes into Simpro, pulls the data, and presents it in a clean table right here in the chat. No need to log into Simpro and click through multiple screens.
>
> Let me try something more specific."
>
> **Demo:** *"What schedules do we have for next week?"*
>
> "It understands 'next week' — I don't need to type exact dates. It figures out the date range and pulls the schedules.
>
> One more."
>
> **Demo:** *"Show me all invoices for the Smith renovation project"*
>
> "Notice I typed the project name, not the job number. The assistant is smart enough to search by name and find the right job.
>
> **Key point:** You can ask questions in plain English. No codes, no filters, no forms. Just ask what you want to know."

---

## Part 3 — The Schedule Agent (5 minutes)

> **Presenter:**
>
> "Now let's get into the real power — the agents. The first one handles **scheduling**.
>
> Let's say I need to schedule a team member for a job."
>
> **Demo:** *"Schedule John for the Henderson project on Tuesday for 8 hours"*
>
> "I've typed that just like I'd say it to someone in the office. The assistant now:
> - Finds which 'John' I mean in our staff list
> - Finds the Henderson project in our jobs
> - Works out next Tuesday's date
> - Creates the schedule in Simpro
>
> If there are two Johns — say John Smith and John Davies — it doesn't guess. It asks me."

### Clarification Demo

> **Presenter:**
>
> "Watch what happens here — a dropdown just appeared asking me: *'Did you mean John Smith or John Davies?'* I pick the right one, and it continues.
>
> Same thing happens if the project name matches multiple jobs. It always checks with you before doing anything.
>
> This also works with **bulk scheduling from a spreadsheet**."

### Bulk Upload Demo

> **Presenter:** *(Uploads a schedule spreadsheet)*
>
> **Demo:** *Upload file → "Create these schedules"*
>
> "I've just uploaded an Excel file with 15 schedule entries — staff names, job names, dates, hours. The assistant reads the file, matches every name to the right person and the right job in Simpro, and creates all 15 schedules.
>
> If any names are ambiguous — say there's a 'Mike' that matches three people — it groups all the questions together and asks me once, rather than interrupting me 15 times.
>
> You can also **update** and **delete** schedules the same way. Just tell it what to change."
>
> **Demo:** *"Change John's schedule on Tuesday to 6 hours instead of 8"*
>
> **Demo:** *"Delete the schedule for Sarah on Friday"*

### Schedule Agent — What It Can Do

> **Presenter:**
>
> "To summarise, the Schedule Agent can:
> - **Create** schedules — one at a time or in bulk from a file
> - **Update** schedules — change hours, dates, staff, cost centres
> - **Delete** schedules
> - **Lock and unlock** schedules
> - Handle **flexible dates** — 'tomorrow', 'next Monday', 'March 15th'
> - **Smart name matching** — you don't need exact names or ID numbers"

---

## Part 4 — The Invoice Agent (5 minutes)

> **Presenter:**
>
> "Next up — invoicing. This is where things get really interesting.
>
> Let's say you have a spreadsheet of completed work that needs to be invoiced."
>
> **Demo:** *(Uploads an invoice spreadsheet)*
> *"Create invoices from this file"*
>
> "The assistant reads the spreadsheet, figures out how to group the items into invoices, and creates them in Simpro.
>
> But here's the clever part — it follows your company's invoicing rules."

### SOP-Driven Invoicing

> **Presenter:**
>
> "We've set up your company's Standard Operating Procedure — your SOP — so the assistant knows your rules. For example:
> - 'Always create a separate invoice per cost centre'
> - 'Use Progress Invoice type for jobs over $10,000'
> - 'Default payment terms are 30 days'
>
> The assistant reads these rules and applies them automatically. You don't have to remember them every time.
>
> It supports different invoicing modes:
> - **Per Job** — one invoice per job
> - **Per Cost Centre** — separate invoices for each cost centre within a job
> - **Per Item** — one invoice per line item
>
> Your SOP determines which mode is used by default."

### Chat-Based Invoicing

> **Presenter:**
>
> "You can also create invoices without a file."
>
> **Demo:** *"Create an invoice for the Henderson project, progress claim, $25,000"*
>
> "It creates the invoice directly in Simpro. You can also update and delete invoices."
>
> **Demo:** *"Update invoice 4521 — change the date to March 1st"*
>
> **Demo:** *"Delete invoice 4523"*

### Invoice Agent — What It Can Do

> **Presenter:**
>
> "The Invoice Agent can:
> - **Create invoices** from spreadsheets or by chat
> - **Follow your company's invoicing rules** automatically (SOP)
> - **Group items** into invoices (per job, per cost centre, or per item)
> - **Update** invoice details — dates, types, descriptions, payment terms
> - **Delete** invoices
> - Support multiple invoice types — Tax Invoice, Deposit, Progress Invoice, Request for Claim"

---

## Part 5 — The Work Order Agent (5 minutes)

> **Presenter:**
>
> "The third agent handles work orders — sending jobs out to subcontractors.
>
> This one uses a **two-step process** because we know you want to review what's going out before it's sent."

### Phase 1 — Prepare

> **Presenter:**
>
> **Demo:** *"Create a work order for the Henderson project, plumbing cost centre, contractor ABC Plumbing"*
>
> "The assistant goes into Simpro, pulls all the materials and labour items from that cost centre, and generates an Excel file for you to download.
>
> *(Downloads file)*
>
> This Excel has all the available items pre-filled — materials, labour, quantities, costs. You review it, mark which items to include, adjust quantities or prices if needed, and upload it back."

### Phase 2 — Create

> **Presenter:**
>
> **Demo:** *(Uploads the edited Excel)*
> *"Create the work order from this file"*
>
> "It reads your selections — only the items you marked 'Include' — builds the work order, and creates it in Simpro.
>
> This way you always have full control over what goes out to the contractor."

### Duplicate Detection

> **Presenter:**
>
> "One more smart feature — if there's already an open work order for the same contractor and cost centre, the assistant will flag it and ask: *'There's already an open work order here. Do you want to update the existing one or create a new one?'*
>
> It doesn't just blindly create duplicates."

### Work Order Agent — What It Can Do

> **Presenter:**
>
> "The Work Order Agent can:
> - **Prepare** work orders — pulls available materials and labour, generates a review spreadsheet
> - **Create** work orders — from your reviewed and approved spreadsheet
> - **Update** work order details — materials, labour, description, tax code, dates
> - **Delete** work orders
> - **Detect duplicates** — warns you about existing open work orders
> - Follow your **SOP** for department mappings and description formats"

---

## Part 6 — Smart Features That Work Across All Agents (3 minutes)

> **Presenter:**
>
> "Before I wrap up, let me highlight a few things that work across all three agents."

### Fuzzy Name Matching

> "You never need to type exact names or ID numbers. Type 'Henderson' and it finds 'Henderson Residence Renovation - Stage 2'. Type 'John' and it finds 'John Smith'. It handles typos, partial names, and abbreviations."

### Smart Clarification

> "When something is ambiguous, the assistant asks you — but it's smart about it. If it needs to confirm both the staff member AND the job, it asks both questions at the same time instead of going back and forth. It respects your time."

### Structured Output

> "Results come back as clean, formatted tables — not walls of text. You can see your schedules, invoices, and work orders in a layout that makes sense. You can also download the results as CSV or Excel files."

### Data Protection

> "Sensitive information like personal details is protected. The AI only sees what it needs to — job names, IDs, dates, amounts. It never sees or stores personal employee data like phone numbers or salaries."

---

## Part 7 — Current Development Status & Limitations (2 minutes)

> **Presenter:**
>
> "Let me be upfront about where we are today and what's still in progress."

### What's Working Well
> - "All three agents — scheduling, invoicing, and work orders — are functional and tested
> - Name matching and disambiguation is solid
> - File upload and processing works for Excel and CSV
> - The chat interface handles both simple lookups and complex operations
> - SOP integration is active for invoicing and work orders"

### Current Limitations
> - "**Simpro dependency** — this only works with Simpro as your ERP. The data has to be in Simpro for the assistant to find it.
> - **Existing data required** — you can't create brand new jobs or staff from here yet. Jobs, staff, sections, and cost centres need to exist in Simpro first.
> - **One conversation at a time** — it handles one request per conversation flow. You can't ask it to do two unrelated things simultaneously.
> - **Work order review step** — the two-phase process for work orders is by design, but some users may want a faster one-step option in the future.
> - **No mobile app yet** — it's browser-based right now. Works on tablets and phones in the browser, but there's no dedicated mobile app.
> - **LLM accuracy** — like any AI, it can occasionally misinterpret a vague request. The clarification system catches most of these, but clear instructions always give better results."

---

## Closing (1 minute)

> **Presenter:**
>
> "That's Optificial. An AI assistant that speaks your language — not computer language. You tell it what you need in plain English, it handles the back-office work in Simpro.
>
> The goal is simple: less time clicking through screens, more time on the job site.
>
> Happy to take any questions."

---

## Quick Reference — Demo Commands Cheat Sheet

Use these exact phrases during the live demo:

| Moment | Type This |
|--------|-----------|
| Simple lookup | "Show me all active jobs" |
| Date query | "What schedules do we have for next week?" |
| Name search | "Show me all invoices for the Smith renovation project" |
| Create schedule | "Schedule John for the Henderson project on Tuesday for 8 hours" |
| Update schedule | "Change John's schedule on Tuesday to 6 hours instead of 8" |
| Delete schedule | "Delete the schedule for Sarah on Friday" |
| Chat invoice | "Create an invoice for the Henderson project, progress claim, $25,000" |
| Update invoice | "Update invoice 4521 — change the date to March 1st" |
| Work order prep | "Create a work order for the Henderson project, plumbing cost centre, contractor ABC Plumbing" |
| File uploads | Drag spreadsheet + "Create these schedules" / "Create invoices from this file" |

---

## Pre-Demo Checklist

- [ ] Application running and accessible in browser
- [ ] Test data loaded in Simpro (Henderson project, staff John/Sarah, contractors)
- [ ] Sample spreadsheets ready (schedule bulk file, invoice file, work order file)
- [ ] Dark/light mode toggle visible
- [ ] Internet connection stable
- [ ] SOP document configured for invoice and work order agents
- [ ] Microphone working (if showing voice input)
