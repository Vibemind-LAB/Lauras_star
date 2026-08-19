# Laura MCP Server

A Model Context Protocol (MCP) server that exposes the running Laura desktop application to Claude sessions. This server acts as a stdio-based bridge to the Laura API on your local machine, enabling Claude to call Laura's editorial and analysis tools via the app's backend at `127.0.0.1:8765`. The server requires the Laura desktop application to be running on your computer.

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

Before you start the Laura desktop application, set the environment variable `LAURA_TOKEN` to a value of your choice (e.g., a UUID or passphrase). When you launch the Laura app with this variable set, it will use that value to authenticate with its backend service at `127.0.0.1:8765`. If `LAURA_TOKEN` is not set before launch, the app generates a random UUID for that session, which is not discoverable afterward.

Use the same token value you set before launching Laura in both the `claude mcp add` command and the Claude Desktop `claude_desktop_config.json` configuration shown above.

## Escape Hatch: Direct API Access

The server provides full access to the running Laura API via the `laura_api` tool, which allows you to invoke any Laura API endpoint directly. Note that this tool does not enforce schema validation on requests.

**Important:** Destructive operations (DELETE requests and unsafe methods) require explicit user approval. Claude is instructed to always ask for your confirmation before performing deletions or other potentially harmful actions on your Laura projects—this is an instruction to the AI, not a code-enforced dialog.
