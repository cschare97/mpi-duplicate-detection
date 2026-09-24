"""
test_mcp_server.py

Calls check_duplicate through the FastMCP server object using FastMCP's
in-memory client, so the MCP tool layer (schema, argument passing, result
serialization) is exercised without starting a separate process.

Records are invented. Run: python3 test_mcp_server.py
"""

import asyncio
import json

from fastmcp import Client

from mpi_dedup_server import mcp
from patient_match import match_patients

A = {"last_name": "Whitlock", "first_name": "Maren", "dob": "Feb. 17, 1979", "ssn": "999-11-2233"}
B = {"last_name": "Whitlok", "first_name": "Maren", "dob": "Feb. 17, 1979", "ssn": "999-44-5566"}


async def main():
    args = {f"{k}_a": v for k, v in A.items()}
    args.update({f"{k}_b": v for k, v in B.items()})

    async with Client(mcp) as client:
        tools = await client.list_tools()
        print("tools exposed:", [t.name for t in tools])
        result = await client.call_tool("check_duplicate", args)

    data = result.data if getattr(result, "data", None) is not None else json.loads(result.content[0].text)
    direct = match_patients(A, B)

    print("via MCP :", data["verdict"], data["score"])
    print("direct  :", direct.verdict, direct.score)
    ok = data["verdict"] == direct.verdict and data["score"] == direct.score
    print("MCP result equals direct call:", ok)


asyncio.run(main())
