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
    "gating reasoning."
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

# P0-1：无新证据空转时的强制二选一。放在 prompt_phrases（叶子模块）以便
# nodes.py 与 prompt_builder.py 共用同一份措辞——两者之间已有正向依赖，
# 任一方导入对方都会成环。
FORCED_CHOICE_CONVERGENCE_RULE = (
    "There are exactly two acceptable next moves; anything else will be refused:\n"
    "  (A) COMMIT A CONCLUSION: emit the final JSON with root_cause_class set to a concrete "
    "class (not \"unknown\"), is_conclusive=true, action=null, confidence=\"low\" is fully "
    "acceptable, and state the residual unknown plus what evidence would have resolved it in "
    "final_diagnosis.detailed_analysis. A bounded low-confidence conclusion is required over "
    "an unfinished exploration.\n"
    "  (B) DECLARE A NEW EVIDENCE TARGET: name a specific gate objective that is still open, "
    "and a different object/structure/command family (not the same address or field again) "
    "that can produce evidence you do not already have.\n"
    "Re-reading the same bytes cannot produce new evidence: whatever a probe already returned "
    "is what the vmcore contains, so choosing (A) is correct whenever (B) has no concrete answer."
)

BOUNDED_UNCERTAINTY_EXIT_RULE = (
    "When direct writer evidence is unattainable (due to missing module symbols, partial dump coverage, "
    "or asynchronous DMA/interrupt overwrite), DO NOT spin on reading the corrupted object. "
    "Proving that an object is corrupted and that the local execution frame did not corrupt it "
    "is sufficient to conclude with root_cause_class=\"pointer_corruption\" or \"memory_corruption\" "
    "(with confidence=\"low\" or \"medium\"). Explicitly recording verification gaps in detailed_analysis "
    "is considered a successful bounded convergence, NOT a diagnostic failure."
)

CORRUPTED_PAYLOAD_TERMINATION_RULE = (
    "When a kernel pointer or field has already been shown to contain corrupted data (e.g. invalid addresses, "
    "unrelated magic numbers, or driver-private pattern values), treat the memory at that location as "
    "CORRUPTED PAYLOAD, not a live protocol data structure. You are strictly forbidden from attempting to "
    "reverse-engineer protocol or hardware struct field semantics out of corruption payload: bytes inside "
    "memory already proven to be corruption payload are the payload, not live state, and their protocol "
    "semantics can never be recovered from the vmcore. Once an object is proven corrupt, terminate further "
    "read probes against it."
)

SLAB_OOB_DIRECTION_RULE = (
    "In kmalloc/slab adjacency reasoning, a standard contiguous out-of-bounds write "
    "from object A extends from lower to higher addresses. Therefore, if victim object V "
    "is at a LOWER address than suspect object S, do not claim S performed a standard "
    "OOB overflow into V unless you can prove a non-standard write primitive (for example "
    "negative index, wrong-pointer memcpy/memmove, explicit reverse copy, arbitrary write, "
    "or UAF/write-through alias) and show concrete evidence for that primitive."
)
