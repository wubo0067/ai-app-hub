#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from .prompt_phrases import (
    CANARY_POINTER_VALUE_PARTIAL_DUMP_RULE,
    CANARY_POINTER_VALUE_RULE,
    STACK_CAUSALITY_RED_LINE_RULE,
)

STACK_CORRUPTION_OVERLAY = f"""
## Stack-Corruption Overlay

### Stack-Specific Forbidden Reasoning Patterns

- When `resolve_stack_canary_slot` is available, use it before any manual canary-slot or frame-pointer-chain arithmetic.
- When `classify_saved_rip_frames_tool` is available, use it before manual phantom-frame or saved-RIP classification in generic frame-reliability work. In explicit stack-protector cases, first close the canary slot with `resolve_stack_canary_slot`; only then use `classify_saved_rip_frames_tool` for NON-CANARY provenance checks or unresolved saved-RIP reliability questions.
- When the panic string explicitly says stack-protector failure in function F, the default hypothesis is corruption of F's own frame during F's execution. Do not name an unrelated interrupted-path function unless you can prove a concrete write primitive or proven cross-frame overlap into F's canary slot.
- Do not blame an exception-path frame such as handle_mm_fault for canary corruption, or blame an interrupted pre-fault frame for a handler-frame canary, when the only support is relative stack position or ordinary downward-stack reasoning across a page-fault, interrupt, NMI, or similar exception-entry boundary. Such claims are invalid until frame provenance, exception-entry layout, and active overlap of the relevant stack regions are explicitly proven.
- Do not promote a function to suspect overflow source merely because it has a non-trivial stack frame, a large in-function offset, or deep execution within a complex routine. Evidence such as sub rsp, 0x90, a +0xbfd offset, or generic "large frame" language is not overflow proof. Require object-level write evidence such as an overflow-capable local object, a concrete copy primitive, or stack-byte provenance tying the write mechanism to the corrupted slot.
- Do not treat the mere presence of a kernel text address or return-site address on the stack as proof that the named function caused the overwrite. A stack-resident code pointer is first evidence about the value that was written or copied, not about the writer. Distinguish payload provenance from writer provenance before naming any overflow source.
- Do not infer pathname, filename, or generic string-buffer overflow from a single ASCII-decodable machine word or a short raw-byte fragment on the kernel stack. Eight decodable bytes without contiguous string context, termination or length evidence, and a plausible copy path are not string provenance.
- Do not promote rd -SS output, ASCII side-by-side dumps, or embedded printable bytes from search hints to root-cause evidence unless you have validated a real string object shape such as contiguous bytes, a terminator or explicit length, and a code path that could have copied that exact string onto the stack.
- Do not attribute canary corruption (__stack_chk_fail) to "residual stack data", "stale data from prior function calls", or "pre-fault stack pollution". The stack protector prologue unconditionally writes the canary value at function entry, overwriting any prior data. Only writes occurring DURING the function's execution (after prologue, before epilogue) can corrupt the canary.
- Do not identify a canary slot address by scanning the stack for a "recognizable" value (such as a task pointer or known object) and reverse-justifying that address as the canary slot. Use `resolve_stack_canary_slot` first; if manual fallback is required, derive the slot from verified RBP arithmetic using the disassembly prologue, not from the value found at an arbitrary address.

### Stack-Corruption Convergence Criteria

When a syscall-path backtrace remains coherent up to the interrupted site, do not automatically pivot to that interrupted non-exception chain in every stack-protector case. First explain why the canary-bearing function's own frame is not the primary suspect. Only after that gate is satisfied should you inspect the interrupted path, for example sys_open -> do_filp_open -> path_openat -> do_last -> link_path_walk -> inode_permission, for local objects, copy primitives, or proven overlap into the canary slot. Do not jump directly to unrelated interrupted-path functions merely because they are active on the stack.

In stack-corruption cases specifically, before naming a suspect function as the overflow source, you MUST verify stack-address causality: on x86-64, a local buffer overflow writes toward higher addresses. Therefore only a function whose frame is at a LOWER address than the corrupted canary could have overflowed upward into that canary. A function whose frame is at a HIGHER address (an earlier caller) cannot overflow downward into a canary that was placed later at a lower address. If the backtrace contains exception-handler frames (page fault, interrupt) nested below the interrupted function, that handler chain is only the primary context for classifying frame provenance; it is not the default overflow source.

If you claim that one active frame's local object overlaps another active frame's canary or locals, you must prove it with standard stack-layout arithmetic, not just two rbp-relative ranges. At minimum, derive:
- caller RBP,
- caller post-prologue RSP after pushes and local allocation,
- callee entry RSP at the call site,
- and the callee canary/local slot from the callee prologue.
If those numbers are not mutually consistent, the overlap claim is unproven and must not be used as final diagnosis.

In stack-corruption cases where the overwritten canary contains a meaningful kernel value rather than random noise, root cause is NOT complete until value provenance has been explored as a mechanism question, not just noted as a fact. For example, if the canary contains the current task pointer or another recognizable object pointer, you must do all of the following before setting is_conclusive to true:
- analyze whether the canary-bearing function's own code (or its inlined/unprotected leaf callees) could have written that value beyond bounds — this is the DEFAULT and most common mechanism,
- analyze whether the exception-path call chain itself could have written that value beyond bounds,
- analyze whether a function storing current or current->field on the stack could have copied or spilled it into the canary slot,
- {CANARY_POINTER_VALUE_RULE}
- {CANARY_POINTER_VALUE_PARTIAL_DUMP_RULE}
- ⛔ do NOT analyze "pre-fault residual-stack pollution" as a canary corruption mechanism — the prologue unconditionally overwrites any prior data at the canary slot,
- and explicitly state which of these mechanisms is supported, which are weakened, and which remain open due to dump limits.

Do not stop at "canary overwritten with task_struct pointer". That is only an intermediate clue. Final diagnosis must explain the most plausible write mechanism or explicitly bound the remaining mechanism set.

Action Execution Red-Line: {STACK_CAUSALITY_RED_LINE_RULE}
""".strip()


DRIVER_OBJECT_OVERLAY = """
## Driver-Private Object Overlay

### Driver Source Correlation (when driver symbols are unavailable)

When struct -o fails for a third-party or out-of-tree module, reconstruct the runtime object layout with the following inference chain before naming the corruption mechanism.

### Step A: Function Pointer Anchoring
- If an extended object dump such as rd -x <addr> 32 contains a value inside the module text range [mod_base, mod_base + mod_size), treat it as a candidate function pointer.
- Run sym <value> to resolve the function name.
- Use that resolved function as a structural anchor: prefer the object type whose source layout places that callback or ISR field at the observed offset.
- Example: if a pointer at offset 0x60 resolves to _base_interrupt in mpt3sas, treat that as a strong cue for a reply-queue descriptor style object rather than a generic queue guess.

### Step B: APIC or MSI Address Recognition
- Values matching 0xFEE0xxxx are Local APIC MSI target addresses.
- When such a value appears at a stable offset, use it as a structural fingerprint for hardware-interrupt queue objects rather than dismissing it as random corruption.

### Step C: Embedded list_head Self-Reference
- If *(addr+N) == addr+N or adjacent pointers self-reference the same embedded node, identify that region as a list_head and use it for container-of style reasoning.
- Record the embedded-node offset explicitly; it is evidence about the enclosing struct identity.

### Step D: Open Source Cross-Reference
- For in-tree or historically open drivers such as mpt3sas, megaraid, mlx5, qla2xxx, and bnx2x, correlate the crashing function and observed offsets against the upstream kernel source when crash debug info cannot name the private type.
- Preferred reference is https://elixir.bootlin.com/linux/<version>/source, or a version-appropriate downstream kernel tree when available.
- Report the inferred field name and declared C type at the corrupted offset. The field type must drive the corruption-mechanism classification.

### Step E: Field Type Classification
- If the field type is dma_addr_t and the observed value is a bus or physical address later used as a virtual pointer, classify this as field-type misuse or missing address conversion.
- If the field type is a pointer type such as void * or struct X *, and it contains a low canonical physical-looking value, classify this as write corruption, incorrect assignment, or a reinit-path bug.
- Do not conflate these mechanisms. Same bad address, different fix vector.

### Step F: Upstream Fix Correlation
- Once the driver and function are known, look for known upstream fixes, stable backports, or CVEs touching the same queue, reset, or reinitialization path.
- If you cannot verify an exact patch, state the bounded pattern only. Do not invent commit IDs.

For third-party or driver-private object corruption, root cause is not complete until one of the following is true:
- the corrupted field's declared type is identified, or
- you explicitly state why field-type classification is not possible from available symbols, source, or dump coverage.
""".strip()
