#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from .analyzer import classify_saved_rip_frames, resolve_stack_canary

canary_server = FastMCP(
    "stack_canary",
    instructions=(
        "This server derives the __stack_chk_fail frame-pointer chain and the exact canary slot "
        "for stack-protector crashes."
    ),
)


@canary_server.tool()
def resolve_stack_canary_slot(
    command: Annotated[
        str,
        Field(
            description=(
                "Resolve a stack-protector canary slot and the __stack_chk_fail frame-pointer chain.\n"
                "Syntax: <canary_function> [--panic-return-address <hex>] [--stack-chk-fail-frame <hex>]\n"
                "Example: search_module_extables --panic-return-address ffffffffb4b1f419"
            )
        ),
    ],
    vmcore_path: Annotated[
        str, Field(description="The absolute path to the vmcore file.")
    ],
    vmlinux_path: Annotated[
        str, Field(description="The absolute path to the vmlinux file.")
    ],
) -> str:
    """Derive the canary slot, caller RBP, and live gs:0x28 canary for a stack-protector crash."""
    try:
        return resolve_stack_canary(vmcore_path, vmlinux_path, command)
    except Exception as exc:
        return f"Failed to resolve stack canary slot: {exc}"


@canary_server.tool()
def classify_saved_rip_frames_tool(
    command: Annotated[
        str,
        Field(
            description=(
                "Classify saved-RIP reliability and phantom-frame candidates from bt/raw stack data.\n"
                "Syntax: [--start-frame <int>] [--end-frame <int>]\n"
                "Example: --start-frame 4 --end-frame 12"
            )
        ),
    ],
    vmcore_path: Annotated[
        str, Field(description="The absolute path to the vmcore file.")
    ],
    vmlinux_path: Annotated[
        str, Field(description="The absolute path to the vmlinux file.")
    ],
) -> str:
    """Classify saved RIP values, last trusted frame, and first unreliable phantom-frame candidate."""
    try:
        return classify_saved_rip_frames(vmcore_path, vmlinux_path, command)
    except Exception as exc:
        return f"Failed to classify saved RIP frames: {exc}"


if __name__ == "__main__":
    canary_server.run(transport="stdio", show_banner=False)
