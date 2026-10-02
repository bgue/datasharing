"""Start the SiteBuilder game headless with the control API and wait until it accepts connections.

    python3 -m sitebuilder_mcp.launch_game --scenario minimal --port 8765

Env GODOT_BIN (default: ``godot`` on PATH). Command line (see godot/README.md if it documents otherwise):
    GODOT_BIN --headless --path <repo>/godot --api=<port> [--api-token=T] -- --scenario=<id>
Prints the pid and keeps running until interrupted when used as a script.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GODOT_PROJECT = REPO / "godot"


def build_command(scenario: str = "minimal", port: int = 8765, token: str | None = None,
                  godot_bin: str | None = None, headless: bool = True, extra: list[str] | None = None) -> list[str]:
    binary = godot_bin or os.environ.get("GODOT_BIN") or shutil.which("godot") or "godot"
    cmd = [binary]
    if headless:
        cmd.append("--headless")
    cmd += ["--path", str(GODOT_PROJECT), f"--api={port}"]
    if token:
        cmd.append(f"--api-token={token}")
    cmd += list(extra or [])
    cmd += ["--", f"--scenario={scenario}"]
    return cmd


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def launch_game(scenario: str = "minimal", port: int = 8765, token: str | None = None,
                godot_bin: str | None = None, wait: float = 60.0, log_path: str | None = None,
                headless: bool = True) -> subprocess.Popen:
    """Spawn the game and block until ``port`` accepts a TCP connection (up to ``wait`` seconds).

    Raises RuntimeError if the process exits early or the port never opens (the process is killed)."""
    if port_open(port):
        raise RuntimeError(f"port {port} is already in use; stop the running game or pick another --port")
    cmd = build_command(scenario, port, token, godot_bin, headless)
    log = open(log_path, "w") if log_path else subprocess.DEVNULL
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT if log_path else subprocess.DEVNULL)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"game exited early with code {proc.returncode}: {' '.join(cmd)}")
        if port_open(port):
            return proc
        time.sleep(0.25)
    stop_game(proc)
    raise RuntimeError(f"game did not open port {port} within {wait}s: {' '.join(cmd)}")


def stop_game(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default="minimal")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token")
    ap.add_argument("--godot-bin", help="default: $GODOT_BIN or godot on PATH")
    ap.add_argument("--wait", type=float, default=60.0)
    ap.add_argument("--log", help="write the game's stdout/stderr here")
    ap.add_argument("--windowed", action="store_true", help="do not pass --headless")
    ap.add_argument("--print-command", action="store_true")
    a = ap.parse_args(argv)
    if a.print_command:
        print(" ".join(build_command(a.scenario, a.port, a.token, a.godot_bin, not a.windowed)))
        return 0
    proc = launch_game(a.scenario, a.port, a.token, a.godot_bin, a.wait, a.log, not a.windowed)
    print(f"game running: pid {proc.pid}, ws://127.0.0.1:{a.port}, scenario {a.scenario} (Ctrl-C to stop)", flush=True)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        stop_game(proc)
        return 0


if __name__ == "__main__":
    sys.exit(main())
