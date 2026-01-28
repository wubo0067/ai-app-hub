"""
Source Patch MCP Server Implementation

This module provides tools for generating and managing source code patches.
"""

import os
import difflib
import time
from typing import Annotated
from pydantic import Field
from fastmcp import FastMCP

patch_server = FastMCP(
    "source_patch_server",
    instructions="This server provides tools for generating and saving source code patches.",
)


@patch_server.tool()
def create_patch_from_content(
    file_path: Annotated[
        str, Field(description="The absolute path of the original source file.")
    ],
    original_content: Annotated[
        str, Field(description="The content of the original file.")
    ],
    modified_content: Annotated[
        str, Field(description="The modified content of the file.")
    ],
    output_dir: Annotated[
        str,
        Field(
            description="Directory to save the patch file. Defaults to current directory if not provided.",
            default=".",
        ),
    ],
) -> str:
    """
    Generates a unified diff patch between original and modified content and saves it to a file.
    """
    try:
        # Generate unified diff
        file_name = os.path.basename(file_path)
        diff_lines = list(
            difflib.unified_diff(
                original_content.splitlines(keepends=True),
                modified_content.splitlines(keepends=True),
                fromfile=f"a/{file_name}",
                tofile=f"b/{file_name}",
                lineterm="",
            )
        )

        if not diff_lines:
            return "No differences found between original and modified content."

        # Create output directory
        if output_dir == ".":
            # Try to save in an 'patches' folder in the current working directory or user's preference
            output_dir = os.path.join(os.getcwd(), "patches")

        os.makedirs(output_dir, exist_ok=True)

        # Generate patch filename
        timestamp = int(time.time())
        patch_filename = f"{file_name}_{timestamp}.patch"
        patch_path = os.path.join(output_dir, patch_filename)

        # Write patch file
        with open(patch_path, "w", encoding="utf-8") as f:
            f.writelines(diff_lines)

        return f"Patch successfully generated and saved to: {patch_path}\n\nContent Preview:\n{''.join(diff_lines[:10])}..."

    except Exception as e:
        return f"Failed to generate patch: {str(e)}"


@patch_server.tool()
def save_raw_patch(
    file_path: Annotated[
        str, Field(description="The absolute path of the target source file.")
    ],
    patch_content: Annotated[
        str, Field(description="The raw content of the unified diff patch.")
    ],
    output_dir: Annotated[
        str,
        Field(
            description="Directory to save the patch file.",
            default="patches",
        ),
    ],
) -> str:
    """
    Saves a raw patch content string to a file.
    """
    try:
        file_name = os.path.basename(file_path)

        # Ensure output directory exists
        if not os.path.isabs(output_dir):
            output_dir = os.path.join(os.getcwd(), output_dir)

        os.makedirs(output_dir, exist_ok=True)

        # Generate patch filename
        timestamp = int(time.time())
        patch_filename = f"{file_name}_{timestamp}.patch"
        patch_path = os.path.join(output_dir, patch_filename)

        # Write patch file
        with open(patch_path, "w", encoding="utf-8") as f:
            f.write(patch_content)
            # Ensure it ends with a newline if not present, though patch files strictness varies
            if not patch_content.endswith("\n"):
                f.write("\n")

        return f"Patch saved to: {patch_path}"

    except Exception as e:
        return f"Failed to save patch: {str(e)}"


if __name__ == "__main__":
    patch_server.run(transport="stdio", show_banner=False)
