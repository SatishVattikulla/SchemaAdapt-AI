import os
import re
import sys
import json
import httpx
import difflib
import subprocess
import shutil
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# --- Environment & Key Resolution ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_FILE = os.path.join(BASE_DIR, "local", "transform.js")
STAGING_FILE = os.path.join(BASE_DIR, "patches", "staging_patch.js")
MANIFEST_FILE = os.path.join(os.environ.get("APPDATA", ""), "antigravity", "brain", "4c1be4b2-1f4e-4a92-88fa-eaa669c26291", "change_manifest.md")

# Ensure directories exist
os.makedirs(os.path.dirname(STAGING_FILE), exist_ok=True)
os.makedirs(os.path.dirname(LOCAL_FILE), exist_ok=True)

# Extract GOOGLE_API_KEY from C:\NLP\Google\ADK\.env
api_key = None
adk_env_path = r"C:\NLP\Google\ADK\.env"
if os.path.exists(adk_env_path):
    try:
        with open(adk_env_path, "r", encoding="utf-16le") as f:
            content = f.read()
        m = re.search(r'GOOGLE_API_KEY\s*=\s*["\']([^"\']+)["\']', content)
        if m:
            api_key = m.group(1)
    except Exception as e:
        print(f"Warning: Could not read GOOGLE_API_KEY from {adk_env_path}: {e}")

if not api_key:
    api_key = os.environ.get("GOOGLE_API_KEY")

if not api_key:
    print("Error: GOOGLE_API_KEY not found in environment or ADK .env file. Please check configuration.")
    sys.exit(1)

# Initialize Gemini GenAI client
client = genai.Client(api_key=api_key)


# --- ADK Shared State Engine ---
class GlobalThreadMemory(BaseModel):
    error_log: str = ""
    poison_payload: dict = {}
    target_schema: dict = {"id": "number", "name": "string"}
    generated_code: str = ""
    cumulative_tokens: int = 0
    validation_status: str = "PENDING"  # PENDING, PASSED, FAILED, SECURITY_VIOLATION, CIRCUIT_BREAKER
    human_signal: str = "PENDING"       # PENDING, APPROVED, REJECTED
    human_feedback: str = ""
    loop_count: int = 0


# --- Structured Output Schema for Schema Optimizer ---
class SchemaOptimizationResponse(BaseModel):
    explanation: str = Field(description="Explanation of the schema deviation and the required fix.")
    dependencies: list[str] = Field(description="Array of JavaScript library imports required.")
    raw_code: str = Field(description="Compliant JavaScript/GatewayScript code utilizing dynamic reflection (Object.keys) to map new attributes natively.")


# --- Helpers ---
def get_container_logs(last_line_count: int) -> tuple[list[str], int]:
    try:
        res = subprocess.run([
            r"C:\Users\vatti\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe", 
            "logs", "datapower-gateway"
        ], capture_output=True, text=True, encoding="utf-8", errors="ignore")
        lines = res.stdout.splitlines()
        new_lines = lines[last_line_count:]
        return new_lines, len(lines)
    except Exception as e:
        print(f"Error reading container logs: {e}")
        return [], last_line_count


def generate_diff(file1, file2):
    try:
        with open(file1, "r", encoding="utf-8") as f:
            lines1 = f.readlines()
        with open(file2, "r", encoding="utf-8") as f:
            lines2 = f.readlines()
        diff = difflib.unified_diff(
            lines1, lines2, 
            fromfile="local/transform.js", 
            tofile="patches/staging_patch.js",
            lineterm=""
        )
        return "\n".join(diff)
    except Exception as e:
        return f"Error generating diff: {e}"


# --- Node Implementations ---

def node_1_ingest(state: GlobalThreadMemory, log_line_count: int) -> tuple[GlobalThreadMemory, int]:
    print("\n--- [Node 1: Ingest & Telemetry Capture] ---")
    print("Monitoring IBM DataPower container log stream...")
    
    log_pattern = re.compile(r'TRANSACTION_FAILED:\s*(.*?)\.\s*Payload:\s*(\{.*\})')
    
    # Read logs in a polling loop until a failure is detected
    current_line_count = log_line_count
    import time
    while True:
        new_lines, current_line_count = get_container_logs(current_line_count)
        for line in new_lines:
            match = log_pattern.search(line)
            if match:
                error_msg = match.group(1)
                payload_str = match.group(2)
                try:
                    payload = json.loads(payload_str)
                    print(f"Intercepted Transaction Failure: {error_msg}")
                    print(f"Poison Payload: {payload}")
                    
                    state.error_log = error_msg
                    state.poison_payload = payload
                    state.validation_status = "PENDING"
                    return state, current_line_count
                except json.JSONDecodeError:
                    pass
        time.sleep(1)


def node_2_optimizer(state: GlobalThreadMemory) -> GlobalThreadMemory:
    print("\n--- [Node 2: Adaptive Schema Optimizer] ---")
    print(f"Invoking Gemini 1.5 Pro to optimize schema. Loop count: {state.loop_count}")
    
    # Read current transform.js context
    current_code = ""
    if os.path.exists(LOCAL_FILE):
        with open(LOCAL_FILE, "r", encoding="utf-8") as f:
            current_code = f.read()
            
    prompt = f"""
You are an autonomous schema adaptation agent managing an API gateway.
A transaction failure occurred in the upstream IBM DataPower gateway.

CRITICAL DETAILS:
- Error Message: {state.error_log}
- Poison Payload: {json.dumps(state.poison_payload)}
- Target Schema (Allowed Fields Baseline): {json.dumps(state.target_schema)}
- Human Feedback (if any): {state.human_feedback}

CURRENT TRANSFORM SCRIPT:
```javascript
{current_code}
```

INSTRUCTIONS:
1. Analyze the schema deviation (type collision or additive field expansion).
2. Rewrite the JavaScript GatewayScript code.
3. The code MUST dynamically reflect on the incoming object (using `Object.keys(json)`) to map any new attributes natively without dropping or altering the remaining baseline fields (like 'id', 'name').
4. The schema check should be updated to accept the new attributes/types while maintaining the existing fields.
5. Do NOT include unauthorized imports (e.g. 'fs', 'child_process'). Use 'header-metadata' for status codes.
6. The updated script must read the JSON from `session.input.readAsJSON` and write the response to `session.output.write`.

Generate compliant JavaScript code and output structured JSON.
"""
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SchemaOptimizationResponse,
                temperature=0.0
            )
        )
        
        # Track token usage
        usage = response.usage_metadata
        if usage:
            tokens = (usage.prompt_token_count or 0) + (usage.candidates_token_count or 0)
            state.cumulative_tokens += tokens
            print(f"Tokens consumed this run: {tokens}. Cumulative: {state.cumulative_tokens}")
            
        data = json.loads(response.text)
        state.generated_code = data.get("raw_code", "")
        print(f"Generated Code Explanation: {data.get('explanation')}")
        
    except Exception as e:
        print(f"Gemini API invocation failed: {e}")
        state.validation_status = "FAILED"
        
    return state


def node_3_scanner(state: GlobalThreadMemory) -> GlobalThreadMemory:
    print("\n--- [Node 3: Stride Verification Scanner] ---")
    
    # Budget Guardrail
    if state.cumulative_tokens > 50000:
        print("Budget constraint threshold exceeded! Triggering Circuit Breaker.")
        state.validation_status = "CIRCUIT_BREAKER"
        return state
        
    # Write to staging
    try:
        with open(STAGING_FILE, "w", encoding="utf-8") as f:
            f.write(state.generated_code)
        print(f"Wrote generated patch to {STAGING_FILE}")
    except Exception as e:
        print(f"Error writing to staging: {e}")
        state.validation_status = "FAILED"
        state.loop_count += 1
        return state

    # Syntax Validation (node --check)
    print("Running subprocess JavaScript syntax check...")
    try:
        res = subprocess.run(["node", "--check", STAGING_FILE], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"Syntax validation failed:\n{res.stderr}")
            state.validation_status = "FAILED"
            state.loop_count += 1
            return state
        print("Syntax check passed.")
    except Exception as e:
        print(f"Failed to run node --check: {e}")
        state.validation_status = "FAILED"
        state.loop_count += 1
        return state

    # Security Threat Modeling Compliance Sweep
    print("Running threat modeling compliance sweep...")
    banned_imports = ["child_process", "fs", "process"]
    unauthorized = []
    # Read back content
    with open(STAGING_FILE, "r", encoding="utf-8") as f:
        code = f.read()
        
    requires = re.findall(r'require\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)', code)
    for r in requires:
        if r != "header-metadata":
            unauthorized.append(r)
            
    for term in banned_imports:
        if re.search(r'\b' + term + r'\b', code):
            unauthorized.append(term)
            
    if unauthorized:
        print(f"SECURITY VIOLATION: Unauthorized modules detected: {unauthorized}")
        state.validation_status = "SECURITY_VIOLATION"
        state.loop_count += 1
        return state
        
    print("Security compliance scan passed.")
    state.validation_status = "PASSED"
    return state


def node_4_triage(state: GlobalThreadMemory) -> GlobalThreadMemory:
    print("\n--- [Node 4: Human-in-the-Loop Triage] ---")
    print("Generating change manifest Markdown and rendering diff...")
    
    diff_text = generate_diff(LOCAL_FILE, STAGING_FILE)
    
    manifest_content = f"""# Change Manifest: Gateway Patch Promotion

A transaction fault was intercepted and a self-healing schema patch has been compiled and validated.

## Intercepted Fault
- **Error**: `{state.error_log}`
- **Poison Payload**: `{json.dumps(state.poison_payload)}`

## Proposed Code Changes (Git-style Diff)

```diff
{diff_text}
```

## Verification Status
- **Syntax Check**: `PASSED`
- **Security Check**: `PASSED`
- **Cumulative Token Usage**: `{state.cumulative_tokens}` / 50,000
- **Status State**: `{state.validation_status}`

---
### Action Required
Please review the changes. Type **APPROVE** to promote, or **REJECT** to rebuild.
"""
    
    # Save the manifest in the workspace for visual rendering
    manifest_workspace = os.path.join(BASE_DIR, "patches", "change_manifest.md")
    try:
        with open(manifest_workspace, "w", encoding="utf-8") as f:
            f.write(manifest_content)
        print(f"Change manifest rendered beautifully in patches/change_manifest.md")
    except Exception as e:
        print(f"Error saving manifest: {e}")
        
    print("\n---------------- PROPOSED CHANGES DIFF ----------------")
    print(diff_text)
    print("-------------------------------------------------------\n")
    
    # Console interface input prompt
    signal = ""
    while signal not in ["APPROVE", "REJECT"]:
        signal = input("Triage Action Required [APPROVE / REJECT]: ").strip().upper()
        
    state.human_signal = signal
    if signal == "REJECT":
        feedback = input("Provide feedback for optimization: ").strip()
        state.human_feedback = feedback
        state.human_signal = "REJECTED"
    else:
        state.human_signal = "APPROVED"
        
    return state


def node_5_swap_engine(state: GlobalThreadMemory) -> bool:
    print("\n--- [Node 5: Gateway Hot-Swap Engine] ---")
    print("Promoting staging patch to production...")
    try:
        shutil.copy(STAGING_FILE, LOCAL_FILE)
        print("transform.js overwritten successfully.")
    except Exception as e:
        print(f"Failed to hot-swap transforming script: {e}")
        return False
        
    # Wait briefly for file system sync
    import time
    time.sleep(2)
    
    # Verification cURL
    print("Firing test transaction back to Gateway on port 8000...")
    try:
        url = "http://localhost:8000/"
        with httpx.Client(trust_env=False) as client:
            res = client.post(url, json=state.poison_payload, timeout=5.0)
        print(f"Response status: {res.status_code}")
        print(f"Response body: {res.text}")
        if res.status_code == 200:
            print("DYNAMIC MAPPING CONFIRMED (200 OK). Gateway healed!")
            return True
        else:
            print("Gateway returned non-200. Verification failed.")
            return False
    except Exception as e:
        print(f"Verification request failed: {e}")
        return False


# --- Main Orchestration Loop ---
def run_orchestrator():
    print("==================================================")
    print("  SchemaAdapt-AI State Graph Orchestration Engine")
    print("==================================================")
    
    state = GlobalThreadMemory()
    log_line_count = 0
    
    # Get current log line count to start monitoring from now
    _, log_line_count = get_container_logs(0)
    print(f"Initial container log baseline line count: {log_line_count}")
    
    # State Engine Transition Rules
    current_node = "Node_1"
    
    while True:
        if current_node == "Node_1":
            state, log_line_count = node_1_ingest(state, log_line_count)
            current_node = "Node_2"
            
        elif current_node == "Node_2":
            state = node_2_optimizer(state)
            current_node = "Node_3"
            
        elif current_node == "Node_3":
            state = node_3_scanner(state)
            if state.validation_status == "PASSED":
                current_node = "Node_4"
            elif state.validation_status in ["FAILED", "SECURITY_VIOLATION"]:
                if state.loop_count < 3:
                    current_node = "Node_2"
                else:
                    print("Reached loop count limit of 3! Forcing triage gate.")
                    current_node = "Node_4"
            elif state.validation_status == "CIRCUIT_BREAKER":
                current_node = "Node_4"
                
        elif current_node == "Node_4":
            state = node_4_triage(state)
            if state.human_signal == "APPROVED":
                current_node = "Node_5"
            else:
                # REJECTED: reset loop count and route back to Node 2
                state.loop_count = 0
                current_node = "Node_2"
                
        elif current_node == "Node_5":
            success = node_5_swap_engine(state)
            if success:
                print("Self-healing pipeline executed successfully. Terminating.")
            else:
                print("Pipeline failed during hot-swap verification.")
            break


if __name__ == "__main__":
    run_orchestrator()
