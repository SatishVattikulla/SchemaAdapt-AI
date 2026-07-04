import os
import subprocess
import re
import shutil
import httpx
from fastmcp import FastMCP

mcp = FastMCP(name="SchemaAdapt-AI MCP Host Server")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_FILE = os.path.join(BASE_DIR, "local", "transform.js")
STAGING_FILE = os.path.join(BASE_DIR, "patches", "staging_patch.js")

@mcp.tool
def write_staging_patch(content: str) -> str:
    """Writes the generated JavaScript code to patches/staging_patch.js."""
    try:
        os.makedirs(os.path.dirname(STAGING_FILE), exist_ok=True)
        with open(STAGING_FILE, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote staging patch to {STAGING_FILE}"
    except Exception as e:
        return f"Error writing staging patch: {str(e)}"

@mcp.tool
def verify_patch_syntax() -> dict:
    """Runs node --check on patches/staging_patch.js to check syntax stability."""
    if not os.path.exists(STAGING_FILE):
        return {"status": "error", "message": "Staging patch file does not exist"}
    try:
        res = subprocess.run(["node", "--check", STAGING_FILE], capture_output=True, text=True)
        if res.returncode == 0:
            return {"status": "success", "message": "Syntax validation passed"}
        else:
            return {"status": "error", "message": f"Syntax error: {res.stderr.strip() or res.stdout.strip()}"}
    except Exception as e:
        return {"status": "error", "message": f"Execution error running node --check: {str(e)}"}

@mcp.tool
def scan_threat_modeling() -> dict:
    """Scans patches/staging_patch.js for banned modules (child_process, fs, etc.)."""
    if not os.path.exists(STAGING_FILE):
        return {"status": "error", "message": "Staging patch file does not exist"}
    try:
        with open(STAGING_FILE, "r", encoding="utf-8") as f:
            code = f.read()
        
        # Banned imports check
        banned = ["child_process", "fs", "process"]
        requires = re.findall(r'require\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)', code)
        unauthorized = []
        for r in requires:
            if r != "header-metadata":
                unauthorized.append(r)
        
        # Also check direct references to sensitive names
        for term in banned:
            # We want to match whole words/patterns to avoid false positives but block any usage
            if re.search(r'\b' + term + r'\b', code):
                unauthorized.append(term)
                
        if unauthorized:
            return {
                "status": "security_violation",
                "message": f"Security scan failed: Banned imports/modules detected: {list(set(unauthorized))}"
            }
        return {"status": "success", "message": "Security scan passed. No unauthorized imports found."}
    except Exception as e:
        return {"status": "error", "message": f"Error scanning file: {str(e)}"}

@mcp.tool
def deploy_patch() -> str:
    """Deploys patches/staging_patch.js over local/transform.js."""
    if not os.path.exists(STAGING_FILE):
        return "Error: Staging patch file does not exist"
    try:
        os.makedirs(os.path.dirname(LOCAL_FILE), exist_ok=True)
        shutil.copy(STAGING_FILE, LOCAL_FILE)
        return f"Successfully deployed staging patch over production {LOCAL_FILE}"
    except Exception as e:
        return f"Error deploying patch: {str(e)}"

@mcp.tool
def execute_verification_curl(payload: dict) -> dict:
    """Fires a POST statement back to the IBM DataPower container on port 8000 and asserts output."""
    try:
        with httpx.Client(trust_env=False) as client:
            res = client.post(url, json=payload, timeout=5.0)
        return {
            "status": "success" if res.status_code == 200 else "failed",
            "http_status_code": res.status_code,
            "response": res.text
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to execute verification request: {str(e)}"
        }

if __name__ == "__main__":
    mcp.run()
