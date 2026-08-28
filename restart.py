"""
Full stack restart script for CNO IVR (dev).

Starts:
  0. ngrok          — public tunnel → localhost:8888 (required for Twilio callbacks)
  1. Mock CNO API   — port 8001 (mock_cno_api.py)
  2. IVR server     — port 8888 (run.py → WindowsSelectorEventLoopPolicy → uvicorn)
  3. MLflow UI      — port 5000 (experiment tracking dashboard)

Health checks all four before exiting so you know the stack is ready.

Usage:
  venv/Scripts/python restart.py
"""
import subprocess
import sys
import time
import urllib.request
import urllib.error
import os
import signal

PYTHON = os.path.join(os.path.dirname(__file__), "venv", "Scripts", "python.exe")
ROOT   = os.path.dirname(__file__)


def _kill_port(port: int):
    """Kill whatever process is listening on a given port (Windows netstat approach)."""
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                pid = int(line.strip().split()[-1])
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(0.5)
                    os.kill(pid, signal.SIGTERM)  # SIGTERM == SIGKILL on Windows via os.kill
                except (ProcessLookupError, PermissionError):
                    pass
                print(f"  Killed PID {pid} on port {port}")
    except Exception:
        pass


def _wait_healthy(url: str, label: str, retries: int = 15, delay: float = 1.0) -> bool:
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    print(f"  [{label}] healthy ({url})")
                    return True
        except Exception:
            pass
        time.sleep(delay)
        print(f"  [{label}] waiting... ({i+1}/{retries})")
    print(f"  [{label}] FAILED to become healthy at {url}")
    return False


def _read_env_value(key: str) -> str:
    """Read a single key from the .env file (no dependencies needed)."""
    env_path = os.path.join(ROOT, ".env")
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


NGROK_EXE = os.path.join(os.path.expanduser("~"), "ngrok.exe")


def _ngrok_agent_up() -> bool:
    """Return True if ngrok management API is responding on localhost:4040."""
    try:
        with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _ensure_ngrok() -> bool:
    """
    Make sure ngrok is running with the public tunnel pointing to port 8888.

    If already running  → reuse it (nothing to do).
    If not running      → start ngrok.exe, wait up to 10 s for the agent to come up,
                          then verify the public URL (TWILIO_BASE_URL) routes back
                          to the IVR server.
    """
    # ── Already running? ──────────────────────────────────────────────────────
    if _ngrok_agent_up():
        print("  [ngrok] already running — reusing existing tunnel")
    else:
        # ── Start ngrok ───────────────────────────────────────────────────────
        if not os.path.exists(NGROK_EXE):
            print(f"  [ngrok] ngrok.exe not found at {NGROK_EXE}")
            print("  Download from https://ngrok.com/download and place at ~/ngrok.exe")
            return False

        print(f"  [ngrok] starting tunnel → localhost:8888 ...")
        subprocess.Popen(
            [NGROK_EXE, "http", "8888"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait up to 10 s for the agent to register
        for i in range(10):
            time.sleep(1)
            if _ngrok_agent_up():
                print(f"  [ngrok] agent ready after {i+1}s")
                break
        else:
            print("  [ngrok] timed out waiting for agent — check ngrok.exe manually")
            return False

    # ── Verify the public tunnel reaches our /health endpoint ─────────────────
    base_url = _read_env_value("TWILIO_BASE_URL").rstrip("/")
    if not base_url:
        # Read the live tunnel URL from ngrok's management API as fallback
        try:
            import json
            with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=3) as r:
                tunnels = json.loads(r.read()).get("tunnels", [])
                https = [t["public_url"] for t in tunnels if t["public_url"].startswith("https")]
                if https:
                    base_url = https[0]
                    print(f"  [ngrok] tunnel URL: {base_url}  (add TWILIO_BASE_URL to .env to skip this step)")
        except Exception:
            pass

    if base_url:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=6) as r:
                if r.status == 200:
                    print(f"  [ngrok] public URL reachable: {base_url}")
                    return True
        except Exception:
            pass
        print(f"  [ngrok] WARNING: public URL {base_url} not reachable yet — tunnel may still be warming up")

    return True  # agent is up; public check is best-effort


def main():
    print("\n=== CNO IVR — Full Stack Restart ===\n")

    # ── 1. ngrok — must be up before Twilio can call webhooks ─────────────────
    print("[1/6] Starting ngrok tunnel...")
    ngrok_ok = _ensure_ngrok()

    # ── 2. Kill existing processes on all ports ───────────────────────────────
    print("\n[2/6] Stopping existing processes on ports 8001, 8888, 5000...")
    _kill_port(8001)
    _kill_port(8888)
    _kill_port(5000)
    time.sleep(1)

    # ── 3. Start mock CNO API ─────────────────────────────────────────────────
    print("\n[3/6] Starting mock CNO API on port 8001...")
    mock_proc = subprocess.Popen(
        [PYTHON, os.path.join(ROOT, "mock_cno_api.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
    )
    print(f"  PID: {mock_proc.pid}")

    # ── 4. Start IVR server ───────────────────────────────────────────────────
    print("\n[4/6] Starting IVR server on port 8888 (via run.py)...")
    ivr_proc = subprocess.Popen(
        [PYTHON, os.path.join(ROOT, "run.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
    )
    print(f"  PID: {ivr_proc.pid}")

    # ── 5. Start MLflow UI ────────────────────────────────────────────────────
    print("\n[5/6] Starting MLflow UI on port 5000...")
    mlflow_db = os.path.join(ROOT, "mlflow.db")
    mlflow_proc = subprocess.Popen(
        [PYTHON, "-m", "mlflow", "ui",
         "--host", "0.0.0.0",
         "--port", "5000",
         "--backend-store-uri", f"sqlite:///{mlflow_db}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
    )
    print(f"  PID: {mlflow_proc.pid}")

    # ── 6. Health checks ──────────────────────────────────────────────────────
    print("\n[6/6] Waiting for health checks...")
    time.sleep(2)

    mock_ok   = _wait_healthy("http://localhost:8001/health",  "Mock CNO API :8001")
    ivr_ok    = _wait_healthy("http://localhost:8888/health",  "IVR server   :8888", retries=20)
    mlflow_ok = _wait_healthy("http://localhost:5000/health",  "MLflow UI    :5000", retries=15)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n=== Status ===")
    print(f"  ngrok tunnel : {'OK'   if ngrok_ok  else 'FAILED'}")
    print(f"  Mock CNO API : {'UP'   if mock_ok   else 'DOWN'}")
    print(f"  IVR server   : {'UP'   if ivr_ok    else 'DOWN'}")
    print(f"  MLflow UI    : {'UP'   if mlflow_ok else 'DOWN'}")

    if mock_ok and ivr_ok and ngrok_ok:
        print("\nStack is ready. You can make calls now.")
    elif mock_ok and ivr_ok:
        print("\nIVR server is up but ngrok failed — calls will get error 31005.")
        print("Check that ~/ngrok.exe exists and your authtoken is configured.")
    else:
        print("\nOne or more core services failed to start. Check ivr.log for details.")
        sys.exit(1)

    print("\n  IVR Dashboard : http://localhost:8888/dashboard")
    print("  MLflow UI     : http://localhost:5000")
    print("  Mock API      : http://localhost:8001")
    if not mlflow_ok:
        print("\n  (MLflow UI slow to start — try http://localhost:5000 in a moment)")


if __name__ == "__main__":
    main()
