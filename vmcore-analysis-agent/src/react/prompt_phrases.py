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

DMA_MINIMUM_EVIDENCE_GATE_RULE = (
    "Treat DMA corruption as a gated hypothesis. To elevate DMA from possible to likely or confirmed, "
    "you MUST satisfy at least TWO independent device-side evidence families from this set: DMA-address "
    "or physical-page overlap, IOMMU fault or remapping evidence, validated descriptor bit-layout decode, "
    "PCI or device ownership tied to the corrupted object, MSI or IRQ vector ownership tied to the device, "
    "or sg/dma mapping overlap. If fewer than two families are satisfied, DMA may remain only a possible "
    "corruption hypothesis and must not be emitted as the final root cause."
)

ADJACENT_SLAB_VALUE_COINCIDENCE_RULE = (
    "A driver-specific value found only in an adjacent slab slot or elsewhere on the same slab page does NOT "
    "exclude software mechanisms such as OOB, UAF-with-reuse, or stale residue. It proves only that the driver "
    "or a related object may have allocated somewhere on that slab page; it is not by itself DMA evidence and it "
    "does not negate software-side adjacency reasoning."
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

SLAB_OOB_DIRECTION_RULE = (
    "In kmalloc/slab adjacency reasoning, a standard contiguous out-of-bounds write "
    "from object A extends from lower to higher addresses. Therefore, if victim object V "
    "is at a LOWER address than suspect object S, do not claim S performed a standard "
    "OOB overflow into V unless you can prove a non-standard write primitive (for example "
    "negative index, wrong-pointer memcpy/memmove, explicit reverse copy, arbitrary write, "
    "or UAF/write-through alias) and show concrete evidence for that primitive."
)
