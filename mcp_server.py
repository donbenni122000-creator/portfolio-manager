"""Entry point for Claude Desktop (stdio MCP server).

Claude Desktop starts this file itself; you never need to run it by hand.
See README -> "Connect to Claude / ChatGPT".
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app.mcp_server import build_server  # noqa: E402

if __name__ == "__main__":
    build_server(remote=False).run()          # stdio transport
