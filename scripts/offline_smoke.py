"""Exercise a real local HTTP server with synthetic data; never calls a provider."""

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="sixteen-smoke-") as directory:
        key = secrets.token_urlsafe(32)
        environment = {
            **os.environ,
            "MEMORY_MODE": "lexical",
            "ENABLE_FACTS": "false",
            "MEMORY_API_KEY": key,
            "DATABASE_PATH": directory + "/memory.sqlite3",
        }
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "sixteen_memory.app:create_app", "--factory",
             "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
            env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False) as client:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError("Local server exited before readiness")
                    try:
                        response = client.get("/health")
                        response.raise_for_status()
                        break
                    except httpx.HTTPError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError("Local server did not become ready")
                assert response.json()["mode"] == "lexical"
                body = {
                    "request_id": "synthetic-write", "user_id": "synthetic-user",
                    "session_id": "synthetic-session",
                    "messages": [{"role": "user", "content": "Alice prefers green tea."}],
                }
                assert client.post("/v1/memories/add", json=body).status_code == 401
                headers = {"Authorization": "Bearer " + key}
                for _ in range(2):
                    result = client.post("/v1/memories/add", json=body, headers=headers)
                    result.raise_for_status()
                    assert result.json()["success"] is True
                query = {"query": "Alice", "user_id": "synthetic-user", "top_k": 100}
                result = client.post("/v1/memories/search", json=query, headers=headers)
                result.raise_for_status()
                assert len(result.json()["data"]) == 1
                assert result.json()["data"][0]["content"].endswith("Alice prefers green tea.")
                query["user_id"] = "different-user"
                assert client.post("/v1/memories/search", json=query, headers=headers).json() == {"data": []}
                print("PASS: real local HTTP auth, Add, retry, Search and isolation; no model calls.")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
