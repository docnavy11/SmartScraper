"""MCP subsystem.

`server` exposes SmartScraper to other agents (tools, resources, bearer auth,
streamable HTTP and stdio transports). `client` is the outbound half delivery
uses to push rows to somebody else's MCP server.

Nothing is imported here: both halves pull in fastmcp, and a run that uses
neither should not pay for it.
"""
