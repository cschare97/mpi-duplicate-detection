"""
mpi_dedup_server.py

MCP server exposing patient duplicate-matching as a tool.
Run: python mpi_dedup_server.py
"""

from fastmcp import FastMCP
from patient_match import match_patients

mcp = FastMCP("mpi-dedup")


@mcp.tool()
def check_duplicate(
    last_name_a: str, first_name_a: str, dob_a: str, ssn_a: str,
    last_name_b: str, first_name_b: str, dob_b: str, ssn_b: str,
) -> dict:
    """
    Compare two patient records and score how likely they are the same person.

    Returns a match score (0-1), a verdict (likely_duplicate /
    possible_duplicate_review / not_a_match), and the specific reasons
    behind the score (SSN match type, DOB match, name similarity).
    """
    record_a = {"last_name": last_name_a, "first_name": first_name_a, "dob": dob_a, "ssn": ssn_a}
    record_b = {"last_name": last_name_b, "first_name": first_name_b, "dob": dob_b, "ssn": ssn_b}
    result = match_patients(record_a, record_b)
    return {
        "score": result.score,
        "verdict": result.verdict,
        "reasons": result.reasons,
        "detail": result.detail,
    }


if __name__ == "__main__":
    mcp.run()
