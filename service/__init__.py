"""mem0-rvaim local memory service.

A plugin-managed local Memory Daemon that embeds the Mem0 Python library,
uses Qdrant Local for vector persistence and SQLite for state.  All cloud
(Mem0 Platform) dependencies have been removed.

Only the daemon process may touch Qdrant / SQLite.  Hooks and the MCP
proxy talk to it through a loopback HTTP API protected by a random token.
"""

__version__ = "0.3.1"
