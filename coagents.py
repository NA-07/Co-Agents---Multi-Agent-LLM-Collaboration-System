"""
CoAgents WebUI - Enhanced startup with auto port detection
"""
import socket
import sys
import os
import subprocess
import argparse
from dotenv import load_dotenv

load_dotenv()


def is_port_in_use(host, port):
    """Check if a port is already in use"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def kill_port(port):
    """Kill any process occupying the given port (Windows only)"""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                subprocess.run(["taskkill", "/PID", pid, "/F"],
                               capture_output=True, timeout=5)
                print(f"   Killed stale process PID {pid} on port {port}")
                return True
    except Exception:
        pass
    return False


def find_free_port(host="127.0.0.1", start_port=7788, max_attempts=20):
    """Find an available port starting from start_port"""
    for port in range(start_port, start_port + max_attempts):
        if not is_port_in_use(host, port):
            return port
    raise OSError(f"Could not find a free port in range {start_port}-{start_port + max_attempts}")


def main():
    parser = argparse.ArgumentParser(description="CoAgents WebUI - AI Browser Automation")
    parser.add_argument("--ip", type=str, default="127.0.0.1", help="IP address to bind to")
    parser.add_argument("--port", type=int, default=7788, help="Port to listen on")
    parser.add_argument("--theme", type=str, default="Ocean",
                        choices=["Default", "Soft", "Monochrome", "Glass", "Origin", "Citrus", "Ocean", "Base"],
                        help="Theme to use for the UI")
    parser.add_argument("--auto-port", action="store_true", default=True,
                        help="Auto-find free port if default is occupied (default: True)")
    args = parser.parse_args()

    desired_port = args.port

    # If default port is busy, try to free it or find another
    if is_port_in_use(args.ip, desired_port):
        print(f"⚠️  Port {desired_port} is in use.")
        killed = kill_port(desired_port)
        if killed:
            import time
            time.sleep(1)  # Wait for port to free up

        if is_port_in_use(args.ip, desired_port):
            if args.auto_port:
                desired_port = find_free_port(args.ip, desired_port + 1)
                print(f"🔍 Auto-selected free port: {desired_port}")
            else:
                print(f"❌ Port {desired_port} is still occupied. Use --auto-port or specify another --port.")
                sys.exit(1)

    print()
    print("=" * 60)
    print("  🤖 CoAgents WebUI")
    print("=" * 60)
    print(f"  📍 URL:   http://{args.ip}:{desired_port}")
    print(f"  🎨 Theme: {args.theme}")
    print("-" * 60)
    print("  💡 Configure your LLM in the 'Agent Settings' tab")
    print("     • Ollama (FREE):  https://ollama.com/download")
    print("     • Google Gemini:  https://aistudio.google.com/apikey")
    print("     • DeepSeek:       https://platform.deepseek.com/")
    print("=" * 60)
    print("  Press Ctrl+C to stop the server")
    print("=" * 60)
    print()

    from src.webui.interface import create_ui

    demo = create_ui(theme_name=args.theme)
    demo.queue().launch(
        server_name=args.ip,
        server_port=desired_port,
        show_error=True,
        quiet=False,
    )


if __name__ == "__main__":
    main()
