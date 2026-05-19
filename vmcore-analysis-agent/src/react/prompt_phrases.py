#!/usr/bin/env python3
# -*- coding: utf-8 -*-

CANARY_POINTER_VALUE_RULE = (
    "If the overwritten canary value is a valid kernel pointer, including an in-stack pointer "
    "or a task_struct-derived pointer, treat it as a high-priority provenance clue rather than "
    "a completed diagnosis. You MUST immediately execute `rd -x <overwritten_value> <bounded_count>` "
    "and inspect the surrounding layout to determine whether the value is a saved RBP, saved RIP, "
    "spilled local pointer, or nearby object reference before you pivot to unrelated suspects."
)

CANARY_POINTER_VALUE_PARTIAL_DUMP_RULE = (
    "You are FORBIDDEN from invoking `partial dump` as an excuse to skip that provenance read "
    "before attempting it; only an actual read failure may establish inaccessibility."
)

CANARY_RESIDUAL_DATA_RULE = (
    "Do NOT attribute canary corruption (__stack_chk_fail) to residual stack data, stale data "
    "from prior function calls, or pre-fault stack pollution. The stack protector prologue "
    "unconditionally writes the canary at function entry, overwriting any prior data. Only writes "
    "occurring DURING the canary-bearing function's execution can corrupt the canary."
)

CANARY_SLOT_ONLY_SCOPE_NOTE = (
    "This restriction applies ONLY to the canary slot, not to saved-RBP, saved-RIP, or non-canary "
    "locals."
)

LITERAL_ADDRESS_RULE = (
    "Any address argument emitted in action must already be a fully computed literal address. "
    "Never emit arithmetic expressions inside crash commands, including +, -, parentheses, "
    "register syntax, or shell-style substitution. Compute the final literal address in reasoning "
    "first, then issue the crash command against that literal target."
)

S1_S5_DMA_GATE_RULE = (
    "Before considering or promoting DMA or hardware, explicitly close the system-layer S1-S5 "
    "exclusion reasoning."
)

DMA_PROMOTION_EVIDENCE_RULE = (
    "Do not promote DMA unless the stronger non-DMA explanations have been explicitly closed first "
    "and the device-side evidence threshold is met."
)

STACK_CAUSALITY_RED_LINE_RULE = (
    "If standard x86-64 stack-growth causality has already proved that a candidate frame sits at "
    "a HIGHER address than the corrupted canary slot, you are strictly FORBIDDEN from spending `dis` "
    "or `rd` on that function merely to hunt for local buffers or to promote it as the direct "
    "local-overflow source; instead, immediately move to the canary-bearing function itself, "
    "lower-address active callees, or overwritten-canary-value provenance, and revisit the higher-address "
    "frame only for saved-RIP provenance, exception-entry classification, or a newly supported non-local "
    "write mechanism."
)
