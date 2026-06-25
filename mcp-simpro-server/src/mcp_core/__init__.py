"""
MCP (Model Context Protocol) implementation for Simpro Server.

This package implements the MCP protocol for exposing Simpro tools
to LLM clients (like your Node.js backend).
"""
"""MCP server implementation"""
from .server import MCPServer, get_mcp_server
from .protocol import MCPProtocolHandler

__all__ = ['MCPServer', 'get_mcp_server', 'MCPProtocolHandler']