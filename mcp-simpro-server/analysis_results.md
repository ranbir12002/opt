# MCP Simpro Server - Codebase Exploration & Directory Analysis

This document provides a comprehensive analysis of the `config` and `src` directories of the **MCP Simpro Server**. It details how the application works as a whole, maps the architecture, and deep-dives into individual directories and files. Each component is assessed for relevance, highlighting critical systems, optional components, and files that are unused or redundant and can be deleted.

---

## 1. System Overview

The **MCP Simpro Server** is a Python-based Model Context Protocol (MCP) server that exposes Simpro ERP operations as tools for Large Language Models (LLMs) like Claude or GPT. It operates as an asynchronous FastAPI application, allowing client connections (e.g., from a Node.js backend) via **Server-Sent Events (SSE)**.

### How it Works as a Whole
1. **Bootstrap**: [src/main.py](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/main.py) starts FastAPI, initializes the [MCPServer](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/mcp_core/server.py), and registers all tools (61 tools across 19 categories) via [src/tools/registry.py](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/registry.py).
2. **Connection**: A client establishes a connection through the SSE route `/mcp/sse` handled in [src/api/routes.py](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/api/routes.py).
3. **Orchestration**: Depending on the LLM's capabilities, an orchestration strategy (Native, Assisted, or Manual) is selected via [src/orchestration/selector.py](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/orchestration/selector.py) to manage multi-step tool calls.
4. **Execution**: The LLM requests tool execution. [src/tools/executor.py](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/executor.py) validates arguments, runs the tool, and automatically applies formatting via [src/presentation/simpro_presenter.py](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/presentation/simpro_presenter.py) to render raw JSON into clean HTML/Markdown tables.
5. **API Calls**: Tools call Simpro endpoints using the async [SimproClient](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/simpro/client.py) which handles credentials injection, automatic retries, exponential backoffs, rate-limiting, and error handling.

### High-Level Architectural Flow
```mermaid
graph TD
    Client[Node.js Backend / Agent Client] -->|1. Connects via SSE| FastAPIRoute["FastAPI Routes (/mcp/sse)"]
    FastAPIRoute -->|2. Delegates to| protocol["MCP Protocol Handler"]
    protocol -->|3. Routes to| MCPServer["MCP Server (Core)"]
    
    MCPServer -->|4. Checks| Registry["Tool Registry (61 Tools)"]
    MCPServer -->|5. Executes via| Executor["Tool Executor"]
    
    Executor -->|6a. Calls Tool| Tool["MCP Tool Class"]
    Tool -->|7a. Calls API Wrapper| APIWrapper["Simpro API Wrap (Jobs, Invoices, etc.)"]
    APIWrapper -->|8a. Async Request| SimproClient["SimproClient"]
    
    Tool -->|7b. Direct Call (Bypass)*| SimproClient
    
    SimproClient -->|9. HTTPS API Call| SimproAPI["Simpro ERP API"]
    
    Executor -->|6b. Renders Output| Presenter["Simpro Data Presenter (HTML/MD Tables)"]
```

---

## 2. Global Directory & File Analysis

This section provides a detailed breakdown of the directories, categorizing their files based on their status: **Core/Critical**, **Important**, **Optional**, or **Unused / Canditate for Deletion**.

### 📂 `config/`
Manages application configuration, LLM capabilities, sub-agent endpoints, and environment variables.

| File | Status | Role & Context | Recommendation |
| :--- | :--- | :--- | :--- |
| [`__init__.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/config/__init__.py) | **Important** | Exposes the global settings instance. | Keep. |
| [`settings.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/config/settings.py) | **Core / Critical** | Pydantic Settings model. Configures rate-limits, LLM credentials, and endpoints. | Keep. |
| [`agent_endpoints.json`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/config/agent_endpoints.json) | **Important** | Configures sub-agent endpoints (Invoice, WorkOrder, Extractor). | Keep. |
| [`llm_capabilities.json`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/config/llm_capabilities.json) | **Important** | Maps LLM models to their token limit and orchestration strategy. | Keep. |

---

### 📂 `src/` (Root Files)
Root layer of the application code.

| File | Status | Role & Context | Recommendation |
| :--- | :--- | :--- | :--- |
| [`__init__.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/__init__.py) | **Important** | Package boundary marker. | Keep. |
| [`main.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/main.py) | **Core / Critical** | Main FastAPI and Uvicorn entrypoint; registers tools and initializes server lifespan. | Keep. |
| [`simpro_api_reference.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/simpro_api_reference.py) | **Important** | Contains concise API hints injected dynamically into tool descriptions to guide LLM query generation. | Keep. |

---

### 📂 `src/api/`
Handles HTTP routing and communication endpoints.

| File | Status | Role & Context | Recommendation |
| :--- | :--- | :--- | :--- |
| [`__init__.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/api/__init__.py) | **Important** | Package initialization. | Keep. |
| [`routes.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/api/routes.py) | **Core / Critical** | Exposes the SSE endpoint (`/mcp/sse`), health checks, and debug endpoints. | Keep. |

---

### 📂 `src/llm/`
Abstraction layer for integrating various LLM providers.

| File | Status | Role & Context | Recommendation |
| :--- | :--- | :--- | :--- |
| [`__init__.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/llm/__init__.py) | **Important** | Package exports. | Keep. |
| [`base.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/llm/base.py) | **Important** | Defines the abstract `BaseLLMProvider` interface. | Keep. |
| [`claude_provider.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/llm/claude_provider.py) | **Important** | Integrates Anthropic Claude models. | Keep. |
| [`openai_provider.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/llm/openai_provider.py) | **Important** | Integrates OpenAI/Azure models. | Keep. |
| [`factory.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/llm/factory.py) | **Important** | Instantiates LLM providers based on environment config. | Keep. |
| [`adapters.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/llm/adapters.py) | ⚠️ **Unused** | Completely empty (0 bytes), unused file. | **Straight-up Delete.** |

---

### 📂 `src/mcp_core/`
Implements the Model Context Protocol communication engine.

| File | Status | Role & Context | Recommendation |
| :--- | :--- | :--- | :--- |
| [`__init__.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/mcp_core/__init__.py) | **Important** | Package initialization. | Keep. |
| [`protocol.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/mcp_core/protocol.py) | **Core / Critical** | Handles JSON-RPC 2.0 protocol translation and message parsing. | Keep. |
| [`server.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/mcp_core/server.py) | **Core / Critical** | Wraps the MCP Server library; configures list and execution decorators. | Keep. |
| [`sse_transport.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/mcp_core/sse_transport.py) | **Core / Critical** | Manages client connections via Server-Sent Events. | Keep. |

---

### 📂 `src/orchestration/`
Controls the tool-chaining orchestration strategy.

| File | Status | Role & Context | Recommendation |
| :--- | :--- | :--- | :--- |
| [`__init__.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/orchestration/__init__.py) | **Important** | Package exports. | Keep. |
| [`base.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/orchestration/base.py) | **Important** | Defines `BaseOrchestrator` abstract class. | Keep. |
| [`selector.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/orchestration/selector.py) | **Important** | Inspects config to select the correct orchestrator. | Keep. |
| [`llm_native.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/orchestration/llm_native.py) | **Important** | Strategy for smart models that chain tool calls natively. | Keep. |
| [`assisted.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/orchestration/assisted.py) | **Important** | Strategy for mid-tier models requiring prompts/hints. | Keep. |
| [`manual.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/orchestration/manual.py) | **Important** | Fallback regex-based router that executes code-defined workflows. | Keep. |

---

### 📂 `src/presentation/`
Converts raw JSON data structures from Simpro API into user-friendly displays.

| File | Status | Role & Context | Recommendation |
| :--- | :--- | :--- | :--- |
| [`__init__.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/presentation/__init__.py) | **Important** | Package boundary marker. | Keep. |
| [`simpro_presenter.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/presentation/simpro_presenter.py) | **Important** | Layout logic that renders tables (HTML / Markdown) for Jobs, Customers, Invoices, etc. | Keep. |

---

### 📂 `src/simpro/`
Low-level networking, auth, and company API wrappers for Simpro ERP.

| File | Status | Role & Context | Recommendation |
| :--- | :--- | :--- | :--- |
| [`__init__.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/simpro/__init__.py) | **Important** | Package initialization. | Keep. |
| [`auth.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/simpro/auth.py) | **Core / Critical** | Signs requests, extracts headers, and manages Bearer tokens. | Keep. |
| [`client.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/simpro/client.py) | **Core / Critical** | Robust async HTTP client with rate-limiting, retries, multi-tenant credential injection, and filter fallback parsing. | Keep. |

#### 📂 Subfolder: `src/simpro/api/`
Wraps HTTP endpoint calls in high-level classes.
> [!NOTE]
> Almost all files in this subfolder are **Core/Critical** classes (e.g., `jobs.py`, `quotes.py`, `invoices.py`, `schedules.py`) which map endpoint paths and params. However, several are unused placeholders.

##### ⚠️ Redundant API Files (Unused)
The following files in `src/simpro/api/` are stubs that are never imported or called in the codebase:

*   [`contractors.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/simpro/api/contractors.py) (122 bytes - comments only) — **Straight-up Delete.**
*   [`employees.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/simpro/api/employees.py) (118 bytes - comments only) — **Straight-up Delete.**
*   [`customers.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/simpro/api/customers.py) (0 bytes) — **Straight-up Delete.**
*   [`staff.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/simpro/api/staff.py) (0 bytes) — **Straight-up Delete.**

> [!WARNING]
> These files are empty because their corresponding tools ([src/tools/contractors.py](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/contractors.py), [src/tools/employees.py](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/employees.py), and [src/tools/customers.py](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/customers.py)) bypass the API wrapper layer and invoke the `SimproClient` directly to hit the `/contractors/`, `/employees/`, and `/customers/` endpoints. 

---

### 📂 `src/tools/`
Declares the tool signatures, schemas, and execution blocks exposed to LLMs.

| File | Status | Role & Context | Recommendation |
| :--- | :--- | :--- | :--- |
| [`__init__.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/__init__.py) | **Important** | Package exports. | Keep. |
| [`base.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/base.py) | **Core / Critical** | Exposes `BaseTool`, implementing parameter auto-injection for filters, schema validation, and wildcard-wrapping helpers (`smart_wrap_filters`, `safe_decode_filters`). | Keep. |
| [`executor.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/executor.py) | **Core / Critical** | Safely runs tools by name, catches validation/network errors, and formats results. | Keep. |
| [`registry.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/registry.py) | **Core / Critical** | Imports and registers all 61 active tools (jobs, quotes, cost centers, companies, etc.). | Keep. |
| [`handoff.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/handoff.py) | **Important** | Tool `HandoffToAgentTool` bridges the MCP flow to internal agents. | Keep. |
| *Category Tool Files* | **Core / Critical** | Implement specific tool behaviors (e.g. `jobs.py`, `invoices.py`, `schedules.py`). | Keep. |
| [`staff.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/staff.py) | ⚠️ **Unused** | Stub file containing only 1 comment. It is NOT registered in `registry.py`. | **Straight-up Delete.** |

---

### 📂 `src/utils/`
Logging, formatting, and performance utilities.

| File | Status | Role & Context | Recommendation |
| :--- | :--- | :--- | :--- |
| [`__init__.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/utils/__init__.py) | **Important** | Exposes log utilities. | Keep. |
| [`cache.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/utils/cache.py) | **Important** | Thread-safe in-memory LRU cache with TTL support and caching decorator (used for API performance). | Keep. |
| [`logger.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/utils/logger.py) | **Core / Critical** | Configures colored logs, JSON formatting, and monkey-patches `logging.StreamHandler` to prevent Windows subprocess pipe crashes. | Keep. |
| [`formatters.py`](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/utils/formatters.py) | ⚠️ **Unused** | Completely empty (0 bytes) unused file. | **Straight-up Delete.** |

---

## 3. List of Clean-up Candidates (Recommended for Deletion)

The following **7 files** are redundant or empty, and should be deleted to clean up the codebase.

```
config/
└── (All files are active and required)
src/
├── llm/
│   └── adapters.py                <-- [DELETE] (Empty)
├── simpro/
│   └── api/
│       ├── contractors.py         <-- [DELETE] (Stub bypassed; tools query Client directly)
│       ├── employees.py           <-- [DELETE] (Stub bypassed; tools query Client directly)
│       ├── customers.py           <-- [DELETE] (Stub bypassed; tools query Client directly)
│       └── staff.py               <-- [DELETE] (Empty)
├── tools/
│   └── staff.py                   <-- [DELETE] (Unregistered stub; 28 bytes)
└── utils/
    └── formatters.py              <-- [DELETE] (Empty; superseded by presentation layer)
```

### Deletion Summary
1. **`src/llm/adapters.py`**: Empty, 0 bytes. LLM providers are instantiated directly via factory.
2. **`src/utils/formatters.py`**: Empty, 0 bytes. All formatters have been implemented inside the presentation layer (`simpro_presenter.py`).
3. **`src/tools/staff.py`**: Redundant stub (28 bytes). It is not registered inside [src/tools/registry.py](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/registry.py).
4. **`src/simpro/api/staff.py`**: Empty, 0 bytes. Bypassed by tools.
5. **`src/simpro/api/customers.py`**: Empty, 0 bytes. Bypassed; [src/tools/customers.py](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/customers.py) issues direct queries to `SimproClient`.
6. **`src/simpro/api/employees.py`**: Stub, 118 bytes. Bypassed; [src/tools/employees.py](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/employees.py) issues direct queries.
7. **`src/simpro/api/contractors.py`**: Stub, 122 bytes. Bypassed; [src/tools/contractors.py](file:///c:/Users/91970/Downloads/Optificial.AI-master/Optificial.AI-master/mcp-simpro-server/src/tools/contractors.py) issues direct queries.
