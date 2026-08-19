# Laura MCP Server

A Model Context Protocol (MCP) server that exposes the running Laura desktop application to Claude sessions. This server acts as a stdio-based bridge to the Laura API, enabling Claude to call Laura's editorial and analysis tools remotely. The server requires the Laura desktop application to be running on `127.0.0.1:8765`.

## Registration with Claude Code

To add the Laura MCP server to your Claude Code installation, run:

```bash
claude mcp add laura --env LAURA_TOKEN=<token> -- uv run --directory /absolute/path/to/Laura/services/mcp laura-mcp
```

Replace `/absolute/path/to/Laura` with the absolute path to the Laura repository on your machine, and `<token>` with your Laura session token (see "Token Source" below).

## Registration with Claude Desktop

To use the Laura MCP server with Claude Desktop, add the following entry to your `claude_desktop_config.json` file (create the file at `%APPDATA%\Claude\claude_desktop_config.json` on Windows if it does not exist):

```json
{
  "mcpServers": {
    "laura": {
      "command": "uv",
      "args": ["run", "--directory", "C:/Users/User/Desktop/Laura/services/mcp", "laura-mcp"],
      "env": { "LAURA_TOKEN": "<token>" }
    }
  }
}
```

Adjust the directory path if your Laura repository is located elsewhere. Replace `<token>` with your session token.

## Token Source

The `LAURA_TOKEN` is the same authentication token used by the Laura desktop application to authenticate with its backend service at `127.0.0.1:8765`. When you start the Laura app, the backend generates or loads this token and passes it to the renderer. You can find the token in the desktop app's development tools console or environment variables, or by examining the HTTP headers in requests made to the backend.

## Escape Hatch: Direct API Access

The server provides full access to the running Laura API via the `laura_api` tool, which allows you to invoke any Laura API endpoint directly. Note that this tool does not enforce schema validation on requests.

**Important:** The `laura_api` tool includes a confirmation prompt before executing any destructive operations (DELETE, method unsafe). Claude will always ask for your approval before performing deletions or other potentially harmful actions on your Laura projects.
