"""Optional MCP SDK worker: initialize and list tools only, then close the server."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile


async def discover(profile: dict, errlog=None) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(command=profile["command"], args=profile.get("args", []), cwd=profile.get("cwd"), env=profile.get("env"))
    async with stdio_client(server, errlog=errlog or sys.stderr) as (read, write):
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


def _join_parent_job(name: str):
    """Join a parent-created, purpose-scoped Job before spawning the MCP server.

    The worker is executed as a file by an optional SDK interpreter, so use the
    trusted sibling module directory rather than importing from the caller's
    working directory.  The worker closes only *its own* job handle; the parent
    owns termination and is able to reclaim the complete descendant tree on a
    timeout without touching unrelated host MCP processes.
    """
    if not name:
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import windows_job

    job = windows_job.Job.open(name)
    if job is None:
        raise ValueError("desktop probe parent job no longer exists; MCP server was not started")
    try:
        job.assign_current_process()
    except BaseException:
        job.close()
        raise
    return job


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--job", default="", help="parent-owned AEPProbe job; internal use only")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    job = None
    try:
        profile = json.loads(args.profile.read_text(encoding="utf-8"))
        job = _join_parent_job(args.job)
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
            try:
                result = asyncio.run(asyncio.wait_for(discover(profile, errlog), args.timeout))
            except Exception as exc:
                errlog.seek(0)
                diagnostic = errlog.read(4000)
                print(json.dumps({"error": str(exc), "server_stderr": diagnostic}, ensure_ascii=False))
                raise SystemExit(2) from None
    finally:
        if job is not None:
            job.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
