# Presentation Deck: SchemaAdapt-AI Self-Healing Gateway

This document serves as a comprehensive presentation outline and reference deck for the **SchemaAdapt-AI** project.

---

## 1. Executive Summary & Problem Statement

### The Problem
- **Data Schema Drift**: Upstream services and external clients frequently change data types (e.g., sending an ID as a string instead of an integer) or add undocumented fields without warning.
- **Service Outages**: Traditional API gateways are static. When unexpected schema drift happens, the gateway rejects the transactions, crashing downstream integrations and causing business downtime.
- **Manual Overhead**: Resolving these failures currently requires operations engineers to manually inspect logs, rewrite gateway validation scripts, verify them in staging, and manually deploy patches—a process taking hours.

### The Solution: SchemaAdapt-AI
- An autonomous, self-healing adapter layer operating on **Google ADK 2.0 primitives**.
- It continuously monitors API gateway logs, uses Google Gemini to generate adaptive validation policies on the fly, validates syntax and security checks, and hot-swaps the runtime configuration live under human supervision.

---

## 2. Technical Tech Stack

- **Gateway Layer**: IBM DataPower Developer Docker Container (v10.6.0.0)
  - Configured with a Loopback XML Firewall, custom Style Policies, and an active GatewayScript validation interceptor.
- **Backend Orchestrator**: Python 3.11 & `uv` workspace
  - Coordinates telemetry, graphs state machine transitions, and maintains system memory.
- **Cognitive Engine**: Gemini 2.5 Flash (`google-genai` SDK)
  - Performs schema inference and compiles adaptive, backward-compatible JavaScript patches.
- **Tooling Interface**: Model Context Protocol (FastMCP)
  - Exposes validation checkers, vulnerability/threat scanners, and curl test tools to the state graph.

---

## 3. Component Details & Architecture

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        Local Workspace (Host OS)                       │
│                                                                        │
│   ┌────────────────────┐   ┌─────────────────┐   ┌─────────────────┐   │
│   │  graph_engine.py   │──>│  mcp_server.py  │──>│ patches/staging │   │
│   │  (ADK Orchestrator)│   │  (MCP Toolset)  │   │ _patch.js       │   │
│   └────────────────────┘   └─────────────────┘   └─────────────────┘   │
│             │                                             │            │
│             │ Monitors Logs                               │ Hot-swaps  │
│             ▼                                             ▼            │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │                 IBM DataPower Gateway (Docker)                 │   │
│   │                                                                │   │
│   │   ┌───────────────────────┐       ┌────────────────────────┐   │   │
│   │   │ config/auto-startup.cfg│       │ local/transform.js     │   │   │
│   │   │ (XML Firewall Service)│       │ (Active GatewayScript) │   │   │
│   │   └───────────────────────┘       └────────────────────────┘   │   │
│   └────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

### Component Roles
1. **`auto-startup.cfg`**: Configures the XML Firewall to listen on port `8000` and pass all incoming traffic through the validation script.
2. **`transform.js`**: The active gateway validation rule file. It rejects malformed requests and prints transaction errors to standard output logs.
3. **`mcp_server.py`**: Hosts modular tools for:
   - Compiling JavaScript patches.
   - Performing Node.js syntax validations.
   - Sweep scanning for security threats (e.g. blocking imports of `fs` or `child_process`).
   - Running live target curl requests to test endpoint health.
4. **`graph_engine.py`**: The state-graph machine implementing 5 functional nodes (Monitor -> Optimize -> Scan -> Triage -> Hot-Swap).

---

## 4. Sequential Flow Lifecycle (The 6 Steps)

```mermaid
sequenceDiagram
    autonumber
    Client->>Gateway: Sends invalid type collision payload
    Gateway-->>Client: Returns 400 Bad Request
    Gateway->>Log Stream: Writes TRANSACTION_FAILED log
    Orchestrator->>Log Stream: Intercepts fault log & payload
    Orchestrator->>Gemini 2.5: Requests adaptive patch code
    Gemini 2.5-->>Orchestrator: Generates healed JavaScript
    Orchestrator->>MCP Tool: Runs syntax check & threat scan
    MCP Tool-->>Orchestrator: Scan passed
    Orchestrator->>Developer: Displays unified Git diff (Triage Gate)
    Developer->>Orchestrator: Inputs APPROVE
    Orchestrator->>Gateway: Hot-swaps local/transform.js
    Orchestrator->>Gateway: Sends test transaction (Verification)
    Gateway-->>Orchestrator: Returns 200 OK (Gateway Healed!)
```

---

## 5. Case Study: Resolving the Mismatched Field Fault

### Runtime Schema Evolution Example

#### Original Contract
```json
{
  "storeId": 120,
  "transactionId": 456789,
  "productId": 101,
  "quantity": 2
}
```

#### Incoming Runtime Payload (Type Collision & Additive Field Drift)
```json
{
  "storeId": "STORE_120",
  "transactionId": "TXN-456789",
  "productId": "SKU-101",
  "quantity": 2,
  "discountCode": "SUMMER25"
}
```

This scenario highlights two simultaneous schema drifts:
1. **Type Collision**: `storeId`, `transactionId`, and `productId` changed from numbers to alphanumeric strings.
2. **Additive Field Drift**: A new field `discountCode` was added without being registered.

The baseline gateway would immediately reject this payload with:
`TRANSACTION_FAILED: type collision for field 'storeId', expected number, got string`

### The Autonomous Healing Action
1. The Orchestrator captured the failure.
2. Gemini 2.5 was called, analyzing the input schema drift.
3. Gemini generated an updated `transform.js` utilizing JavaScript reflection:
   ```javascript
   var adaptedPayload = {};
   Object.keys(json).forEach(function(key) {
       adaptedPayload[key] = json[key];
   });
   ```
   This removed the strict check for `number` while maintaining the mapping of all fields.
4. The scanner verified zero syntax errors and confirmed there were no insecure system calls.
5. The engineer approved the patch.
6. The orchestrator replaced the production file, healing the pipeline immediately.

---

## 6. Core Concepts: ADK, MCP & Orchestration Design

### Google ADK 2.0 (Agent Development Kit)
- **What it is**: Google's developer framework designed to model complex autonomous tasks as state machines, state graphs, and structured memory threads.
- **Role in SchemaAdapt-AI**: Powers the core graph loop (`graph_engine.py`). It structures execution into explicit nodes, handles memory propagation (e.g. tracking the poison payload and token usage), and implements conditional routers to recover from syntax/security scanner failures.

### Model Context Protocol (MCP)
- **What it is**: An open standard protocol created to securely and dynamically expose local resources, files, and terminal utilities as tools that LLMs can invoke.
- **Role in SchemaAdapt-AI**: Decouples tool execution from orchestrator logic. The FastMCP tool server (`mcp_server.py`) hosts verification cURLs, Node.js syntax checkers, and security sweeps, providing them to the AI agent as a set of structured API tool schemas.

### Orchestration Design & Lifecycle Concepts
1. **Telemetry Tailing**: Node 1 monitors the DataPower stdout log buffer. It captures transaction faults in real time without polling files or hitting API endpoints.
2. **Backward-Compatible Schema Synthesis**: Instead of rewriting the static schema rules, the AI optimizer uses JavaScript reflection (`Object.keys()`) to dynamically inspect keys and allow permissive types, preserving the old fields while accepting new structures.
3. **AST-Based Compliance Sweeps**: Node 3 implements a security sweep, preventing command injection or system reads (e.g. blocking imports of Node's `child_process` or `fs`).
4. **Human Governance Gate**: A visual unified git-diff and approval stage (Node 4) guarantees that no code is promoted to the production DataPower gateway without engineer triage.


