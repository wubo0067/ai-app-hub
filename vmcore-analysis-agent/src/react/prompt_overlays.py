#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from .prompt_phrases import (
    ADJACENT_SLAB_VALUE_COINCIDENCE_RULE,
    CANARY_RESIDUAL_DATA_RULE,
    CANARY_SLOT_ONLY_SCOPE_NOTE,
    CANARY_POINTER_VALUE_PARTIAL_DUMP_RULE,
    CANARY_POINTER_VALUE_RULE,
    DMA_MINIMUM_EVIDENCE_GATE_RULE,
    DMA_PROMOTION_EVIDENCE_RULE,
    SLAB_OOB_DIRECTION_RULE,
    STACK_CAUSALITY_RED_LINE_RULE,
)

STACK_CORRUPTION_OVERLAY = f"""
## Stack-Corruption Overlay

### Stack-Specific Forbidden Reasoning Patterns

- When `resolve_stack_canary_slot` is available, use it before any manual canary-slot or frame-pointer-chain arithmetic.
- When `classify_saved_rip_frames_tool` is available, use it before manual phantom-frame or saved-RIP classification in generic frame-reliability work. In explicit stack-protector cases, first close the canary slot with `resolve_stack_canary_slot`; only then use `classify_saved_rip_frames_tool` for NON-CANARY provenance checks or unresolved saved-RIP reliability questions.
- These overlay guardrails primarily help close S1, S3, and S4 in the system-layer S1-S5 exclusion reasoning. They do not by themselves satisfy S5. {DMA_PROMOTION_EVIDENCE_RULE}
- When the panic string explicitly says stack-protector failure in function F, the default hypothesis is corruption of F's own frame during F's execution. Do not name an unrelated interrupted-path function unless you can prove a concrete write primitive or proven cross-frame overlap into F's canary slot.
- Do not blame an exception-path frame such as handle_mm_fault for canary corruption, or blame an interrupted pre-fault frame for a handler-frame canary, when the only support is relative stack position or ordinary downward-stack reasoning across a page-fault, interrupt, NMI, or similar exception-entry boundary. Such claims are invalid until frame provenance, exception-entry layout, and active overlap of the relevant stack regions are explicitly proven.
- Do not promote a function to suspect overflow source merely because it has a non-trivial stack frame, a large in-function offset, or deep execution within a complex routine. Evidence such as sub rsp, 0x90, a +0xbfd offset, or generic "large frame" language is not overflow proof. Require object-level write evidence such as an overflow-capable local object, a concrete copy primitive, or stack-byte provenance tying the write mechanism to the corrupted slot.
- Do not treat the mere presence of a kernel text address or return-site address on the stack as proof that the named function caused the overwrite. A stack-resident code pointer is first evidence about the value that was written or copied, not about the writer. Distinguish payload provenance from writer provenance before naming any overflow source.
- Do not infer pathname, filename, or generic string-buffer overflow from a single ASCII-decodable machine word or a short raw-byte fragment on the kernel stack. Eight decodable bytes without contiguous string context, termination or length evidence, and a plausible copy path are not string provenance.
- Do not promote rd -SS output, ASCII side-by-side dumps, or embedded printable bytes from search hints to root-cause evidence unless you have validated a real string object shape such as contiguous bytes, a terminator or explicit length, and a code path that could have copied that exact string onto the stack.
- {CANARY_RESIDUAL_DATA_RULE} {CANARY_SLOT_ONLY_SCOPE_NOTE}
- Do not identify a canary slot address by scanning the stack for a "recognizable" value (such as a task pointer or known object) and reverse-justifying that address as the canary slot. Use `resolve_stack_canary_slot` first; if manual fallback is required, derive the slot from verified RBP arithmetic using the disassembly prologue, not from the value found at an arbitrary address.

### Stack-Corruption Convergence Criteria

When a syscall-path backtrace remains coherent up to the interrupted site, do not automatically pivot to that interrupted non-exception chain in every stack-protector case. First explain why the canary-bearing function's own frame is not the primary suspect. Only after that gate is satisfied should you inspect the interrupted path, for example sys_open -> do_filp_open -> path_openat -> do_last -> link_path_walk -> inode_permission, for local objects, copy primitives, or proven overlap into the canary slot. Do not jump directly to unrelated interrupted-path functions merely because they are active on the stack.

In stack-corruption cases specifically, before naming a suspect function as the overflow source, you MUST verify stack-address causality: in a typical contiguous local-stack-buffer overflow, writes go toward higher addresses. However, pointer-based or negative-index writes may proceed in either direction; validate the concrete write primitive before applying directional causality. Therefore, only a function whose frame is at a LOWER address than the corrupted canary is a default candidate for a standard upward local-buffer overflow. A function whose frame is at a HIGHER address (an earlier caller) should not be ruled out solely by stack direction if a non-standard write primitive such as negative indexing, wrong-pointer memcpy or memmove, struct-pointer writes, or arbitrary-write/UAF behavior is evidenced. If the backtrace contains exception-handler frames (page fault, interrupt) nested below the interrupted function, that handler chain is only the primary context for classifying frame provenance; it is not the default overflow source.

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
- ⛔ {CANARY_RESIDUAL_DATA_RULE} {CANARY_SLOT_ONLY_SCOPE_NOTE}
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

### Step G: Protocol-Level Value Claims Require Bit Layout Verification
- If a corrupted value is described as "resembling", "matching", or "consistent with" a hardware protocol structure (e.g., mpt3sas reply descriptor, NVMe command frame, SCSI CDB, descriptor ring entry), that description is a hypothesis, NOT evidence-quality correlation, until the bit layout is explicitly verified.
- Required verification before elevating a resemblance claim to evidence:
  - Map the raw 64-bit (or N-bit) value to the named structure's documented bit field layout, field by field.
  - Confirm that each decoded sub-field (type, flags, payload, address) individually holds a value consistent with valid protocol state for the claimed structure type.
  - Prove that the physical page or DMA buffer containing the value is actually mapped to the device's DMA ring, reply queue, or command queue — not merely that the address range "could be" a DMA target.
- If module debuginfo is unavailable and struct access for the candidate type fails, any structural resemblance claims MUST be explicitly labeled "unverified hypothesis" in the analysis. Do NOT carry forward a failed struct attempt as partial confirmation of the structural claim.
- Confidence labels such as "high confidence" or "root cause confirmed" are incompatible with an unverified protocol-structure resemblance claim. When bit layout verification cannot be completed, the DMA or hardware attribution must remain explicitly provisional, and is_conclusive must not be set to true on that basis alone.

### Step H: Dmesg Log Value vs DMA Payload (CRITICAL Distinction)

A value that appears in kernel log output (emitted via log_info, dev_info, dev_err, pr_err,
printk, or any equivalent) and also appears at a location in slab memory does NOT constitute
evidence that the device DMA-wrote that value to the slab location. These are two independent
facts that share a value coincidence. The causal chain is as follows:
- In a driver such as mpt3sas: firmware sends a reply message → driver reads the reply from
  its DMA reply ring → driver extracts event codes or status codes from the reply fields →
  driver passes those codes to printk/log_info for display in dmesg.
- The log_info code (e.g., 0x30030109) lives in the firmware reply buffer (a driver-managed
  DMA allocation). It does NOT get separately DMA-written to an arbitrary slab object just
  because the driver logged it.
- Stale residue hypothesis: if the slab cache (e.g., kmalloc-128) is regularly used by the
  same driver for internal allocations, then the matching value in a current slab object is
  more parsimoniously explained by: (a) the object was previously allocated to the driver,
  (b) the driver stored that event code in a field, (c) the object was freed without zeroing,
  and (d) the current allocation received that slab page with the stale value intact.

**Mandatory actions when a log-value / slab-value match is found:**
1. State explicitly that this is a "dmesg log value coincidence" and NOT independent DMA evidence.
2. Examine whether the slab cache involved is used by the suspected driver for internal objects.
3. Check whether the value could be stale data from a prior driver allocation of that slab slot.
4. Require DMA range overlap proof (physical address of the slab page vs device DMA buffer
   physical addresses) as a SEPARATE and INDEPENDENT evidence item before elevating DMA confidence.
5. Never use "the value 0xXXXXXXXX was logged by driver Y AND appears in slab memory" as a
   standalone argument that driver Y performed a stray DMA write to that slab location.
6. {ADJACENT_SLAB_VALUE_COINCIDENCE_RULE}

### Step I: Slab OOB Directionality (CRITICAL Distinction)

- {SLAB_OOB_DIRECTION_RULE}
- If your claim requires a reverse-direction overwrite (higher-address object corrupting a lower-address neighbor),
  you MUST name the exact non-standard primitive and provide evidence from disassembly, call path, or object writes.
- Address coincidence plus adjacent-slot layout is insufficient to prove reverse-direction overwrite.

### Step J: DMA Promotion Gate (CRITICAL Distinction)

- {DMA_MINIMUM_EVIDENCE_GATE_RULE}
- Required validation examples include: `vtop <corrupted_addr>` for page translation, explicit comparison against driver DMA mappings such as reply ring or sg buffers, `log -m | grep -Ei "iommu|dma|fault|remap"` when relevant, and ownership closure for the IRQ/MSI or PCI device path.
- If range overlap cannot be proven in a partial dump, keep DMA as a bounded alternative hypothesis unless another independent device-side evidence family is still positively established.
- A sentence such as "software OOB cannot explain this value on the slab page" is forbidden unless the software mechanisms were actually tested and eliminated with object-local evidence rather than value coincidence.

For third-party or driver-private object corruption, root cause is not complete until one of the following is true:
- the corrupted field's declared type is identified, or
- you explicitly state why field-type classification is not possible from available symbols, source, or dump coverage.
""".strip()
