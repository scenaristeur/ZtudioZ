#!/usr/bin/env python3
"""Lancer le serveur MCP ZtudioZ.

Usage:
    python run_mcp_server.py [--mode stdio|sse]

Mode stdio (défaut) : pour intégration avec Claude Desktop, VS Code, etc.
Mode sse : pour serveur HTTP distant.
"""

import argparse
import logging
import sys

from pipeline.ztudioz_toolkit import ZtudioZToolkit


def main():
    parser = argparse.ArgumentParser(description="ZtudioZ MCP Server")
    parser.add_argument(
        "--mode",
        choices=["stdio", "sse"],
        default="stdio",
        help="Mode de transport MCP (défaut: stdio)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    toolkit = ZtudioZToolkit()

    # Le metaclass AgentOpsMeta a déjà créé self.mcp (FastMCP)
    # et enregistré tous les outils. On lance le serveur.
    print(f"🚀 ZtudioZ MCP Server — mode {args.mode}", file=sys.stderr)

    try:
        toolkit.run_mcp_server(args.mode)
    except KeyboardInterrupt:
        print("\nArrêt du serveur MCP.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
