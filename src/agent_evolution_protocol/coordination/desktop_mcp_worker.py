"""Optional MCP SDK worker: initialize and list tools only, then close the server."""
import argparse
import asyncio
import json
from pathlib import Path
import sys


async def discover(profile: dict) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(command=profile["command"], args=profile.get("args", []), cwd=profile.get("cwd"))
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = []
            cursor = None
            seen = set()
            for _ in range(20):
                page = await session.list_tools(cursor=cursor)
                tools.extend(t.model_dump(by_alias=True, exclude_none=True) for t in page.tools)
                cursor = page.nextCursor
                if not cursor:
                    return {"tools": tools}
                if cursor in seen:
                    raise ValueError("MCP tools pagination loop")
                seen.add(cursor)
            raise ValueError("MCP tools pagination exceeded 20 pages")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    print(json.dumps(asyncio.run(asyncio.wait_for(discover(profile), args.timeout)), ensure_ascii=False))


if __name__ == "__main__":
    main()
