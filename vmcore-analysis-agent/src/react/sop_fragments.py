#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from .prompt_phrases import (
    CANARY_RESIDUAL_DATA_RULE,
    CANARY_SLOT_ONLY_SCOPE_NOTE,
    CANARY_POINTER_VALUE_PARTIAL_DUMP_RULE,
    CANARY_POINTER_VALUE_RULE,
    DMA_PROMOTION_EVIDENCE_RULE,
    STACK_CAUSALITY_RED_LINE_RULE,
)

SOP_FRAGMENTS: dict[str, str] = {
    "address_search": """
## 1.6 Address Search SOP

Before executing any search, state which strategy you are using in reasoning.

Strategy 1: targeted region search.
- search -t <address> for current task stack.
- search -u <address> after set <pid> for user-space.
- search -s <start> -e <end> <address> for bounded VA ranges.

Strategy 1a: symbol-oriented raw region sweep.
- When you need to inspect a suspicious kernel memory region deeply and reinterpret raw bytes as likely kernel symbol addresses, prefer run_script with rd -SS <address> <small_count> | grep "<concrete_anchor>".
- Use this when page-fault, pointer-corruption, or object-shape analysis has already identified a bounded suspect region and you need candidate function-pointer or embedded-string anchors.
- Keep the rd window explicit and small. Do not use broad printable-character grep patterns such as grep -E '[ -~]{8,}' to mine arbitrary ASCII across a large range.
- If a wider bounded region is required, add an explicit count to rd gradually instead of switching to a large sweep.
- Treat grep hits only as candidate anchors. Validate each hit with sym, dis, struct, or neighboring rd output before concluding pointer provenance or root cause.

Strategy 2: reverse physical-address resolution.
1. Align the PA to 4 KB.
2. Run kmem -p <aligned_PA>.
3. Decide whether the page is slab, anonymous, or file-backed.

Strategy 3: PA to VA translation with mandatory validation.
1. ptov <PA>
2. vtop <VA>
3. Only if vtop succeeds and page flags are not reserved may you run rd.

ptov is arithmetic only. A returned VA does not prove the input was a valid physical address.
""".strip(),
    "per_cpu_access": """
## 1.9 Per-CPU Variable Access

On x86_64, a mov from percent-gs offset reads a per-CPU variable.

Procedure:
1. Extract the per-CPU offset from disassembly.
2. Read p/x __per_cpu_offset[<panic_cpu_id>] to get the CPU base.
3. Read the literal address base plus offset with rd.
4. Optionally identify the symbol relative to __per_cpu_start.

"Do not emit rd against percent-gs syntax, registers, or shell-like expressions."
""".strip(),
    "stack_overflow": f"""
## 3.8 Stack Overflow / Stack Corruption

Pattern: kernel stack overflow, corrupted stack end detected, or crash in random-looking code with RSP near a stack boundary.

### Stack Corruption Analysis Checklist
1. Reconstruct the canary-bearing frame from the actual prologue, saved-frame links, and raw stack bytes; never assume the bt frame address is RBP.
2. Compute the canary slot from the disassembly-derived offset such as rbp-0x18 and verify the concrete slot contents before reasoning about writers.
3. Classify each adjacent frame or stack region as one of: ordinary call frame, interrupted normal-path frame, hardware or pt_regs exception-entry state, or exception-handler frame.
4. Apply x86-64 downward-stack overflow direction only inside a proven ordinary caller/callee segment.
5. If the candidate source and corrupted canary sit on opposite sides of an exception-entry boundary, ordinary local-overflow causality is unproven until frame provenance and active-overlap arithmetic are explicitly established.
6. Before blaming a handler frame such as handle_mm_fault, evaluate competing mechanisms: active overwrite inside the exception path, stack-slot reuse from pre-fault returned frames, stale stack residue, or misidentified frame links.
7. Do not promote any function to direct suspect based only on stack-frame size, deep offset, or generic routine complexity. Require an overflow-capable object or write primitive, or concrete stack-byte provenance.
8. {CANARY_POINTER_VALUE_RULE}
9. {CANARY_POINTER_VALUE_PARTIAL_DUMP_RULE}
10. If adjacent frames imply an ordinary call edge that static code structure does not support, or splice unrelated subsystems without a proven exception bridge, treat that bt edge as unreliable until saved return addresses, frame links, or exception-entry provenance validate it.
11. If printable bytes appear near the canary or in a suspect stack slot, treat them as undecoded payload until proven otherwise. Do not call them pathname or filename evidence unless you validate contiguous string structure plus a plausible copy primitive.
12. If any checklist item remains unproven, keep the final mechanism bounded and provisional instead of naming a direct overflow source.

Analysis:
1. Distinguish process, IRQ, and exception stack overflows.
2. Treat bt as provisional when frames are context-inconsistent; first validate return addresses, stack progression, and control-flow plausibility before trusting the call chain.
3. Use bt -f only with a concrete pid or task when you need per-frame details for that task; never use bt -f with a frame number.
4. On x86_64 with a frame-pointer prologue, saved caller RBP is at [RBP] and the return address is at [RBP+8]; compute the canary slot from the disassembly-derived offset such as rbp-0x18 instead of guessing from older frames.
5. On x86-64, the stack grows downward (high -> low). In a typical contiguous local-stack-buffer overflow within function F, writes often progress toward higher addresses and may hit F's canary and caller-side data. This is not universal: wrong-pointer memcpy or memmove, struct-pointer writes, negative indices such as buf[-8], and use-after-free or other arbitrary-write primitives may write in either direction or without local stack-direction constraints. Always validate the concrete write primitive and address progression before attributing causality.
6. For any claim that caller locals overlap an active callee frame, compute caller post-prologue RSP first. Since the callee frame is allocated below the caller's call-site RSP, an alleged callee canary above that boundary is a proof error.
7. Across an exception-entry boundary, relative frame addresses alone do NOT prove that a pre-exception frame or a handler frame locally overflowed into the other. If provenance is unproven, keep local-overflow attribution provisional and evaluate alternatives such as stack-slot reuse, stale residue, or misidentified frame links.
8. Do not use sub rsp size, a large function offset, or labels such as "large frame" as standalone evidence for overflow. On their own, they are only weak complexity cues and cannot justify naming a suspect function.
9. Inspect task_struct and thread_info fields with task -R when you need stack boundaries or execution-context validation.
10. Inspect STACK_END_MAGIC and the raw stack contents with rd -x when needed.
11. For kernel-stack pages, use vtop or task-derived stack boundaries when page validation is required; do NOT use kmem -S on stack addresses -- the kernel stack is not a slab allocation and kmem -S will always return a useless "not allocated in slab subsystem" error.
12. In panic backtraces, frames prefixed with ? are stack-scan candidates rather than trusted frame-pointer links; treat them as hints only, not proven caller-callee relationships. However, ? frames from exception handlers are diagnostically significant.
13. Look for recursive call patterns, overwritten return-address regions, and frames that jump into unrelated subsystems.
14. If a bt segment implies an unexpected edge such as a VFS permission helper apparently calling an mm or vmstat helper directly, do not treat that adjacency as proof of normal execution. First decide whether it is a corrupted saved RIP, a stack-scan artifact, or an exception-nested splice.
15. When sym fails on a non-symbol kernel address found repeatedly on the stack, do NOT abandon the address. Instead run vtop <address> to validate the page, then kmem -p <PA> to check page state. The address may be a per-CPU pointer, vmalloc object, or module data address that reveals the corruption source.
""".strip(),
    "stack_protector_fast_path": f"""
## 3.8b Stack Protector Fast Path

Use this SOP only when the panic string explicitly says stack-protector or the active frame is
__stack_chk_fail.

### Phase 1: Canary Slot Closure (MANDATORY)

1. Call `resolve_stack_canary_slot <function>` as the DEFAULT and PREFERRED action.
2. Read the tool output and copy forward: return-address location, __stack_chk_fail_RBP,
   canary-bearing function RBP, canary offset, canary slot address, canary slot contents,
   and live gs:0x28 canary.
3. Only if the tool is unavailable or returns unproven, perform the manual fallback:
   a. Disassemble the canary-bearing function with `dis -rl <function>` to identify the
      standard prologue (push %rbp; mov %rsp, %rbp) and the canary store instruction
      (e.g., `mov %rax, -0x18(%rbp)` -> canary offset is -0x18).
   b. **Recommended RBP derivation -- frame-pointer chain (preferred over bt address formulas)**:
      - If `__stack_chk_fail` has a standard prologue (push %rbp; mov %rsp, %rbp), then
        [__stack_chk_fail_RBP + 8] holds the return address back to the canary-bearing function.
      - Locate that exact return-address value in the raw stack dump (e.g., via `bt -f` or
        `rd -x` around the suspected frame region). The stack address that contains this value
        equals __stack_chk_fail_RBP + 8.
      - Therefore __stack_chk_fail_RBP = (that address) - 8.
      - [__stack_chk_fail_RBP] = saved old RBP = the canary-bearing function's RBP_absolute.
      - This derivation is more reliable than computing RBP from the `bt` frame address alone.
   c. Accept RBP_absolute only if ALL of the following are independently verified:
      - Consistent with the function prologue, push instructions, and `sub $N, %rsp` layout.
      - [RBP_absolute] is a plausible saved RBP (a stack-range address for this task).
      - [RBP_absolute + 8] is a plausible saved RIP (a valid kernel text address).
      - The resulting canary slot address is consistent with the raw stack layout without
        contradiction.
   d. Compute the canary slot address as RBP_absolute + <canary_offset from prologue>.
      Read the slot with `rd -x <slot_addr> 1` and compare to the live gs:0x28 canary value.
      - High-entropy value matching gs:0x28 -> canary intact (rare; re-examine the crash path).
      - Non-random / recognizable value (task pointer, small integer, code address) -> overwritten.
      - Record both the slot address and its contents as primary forensic evidence.
   e. Do NOT derive RBP_absolute from the bt frame address by formula alone without independent
      validation of steps (c). Do NOT scan the stack for a "recognizable" value and
      reverse-justify that address as the canary slot. The slot address MUST come from RBP
      arithmetic, not from the value found at a stack location.
4. If both the tool path and the manual fallback fail to close, report the canary slot as
   unproven and STOP. Do NOT pivot to narrative-driven suspects or recognizable-value guessing.
5. {CANARY_POINTER_VALUE_RULE}
6. {CANARY_POINTER_VALUE_PARTIAL_DUMP_RULE}

**Phase 1 Required Output**:
```
PHASE 1 RESULT:
   Function: <canary_bearing_function>
   Source: <resolve_stack_canary_slot | manual fallback>
   Canary summary: <slot addr/value vs live gs:0x28 canary>
   Key chain: <return-address location, __stack_chk_fail_RBP, caller RBP>
   Status: <intact | overwritten | unproven>
   Manual notes: <only if the tool was unavailable or unproven>
```

### Phase 2: Allowed Mechanism Triage (MANDATORY)

Evaluate ONLY these mechanism families, in this order:
1. Self-frame local overflow in the canary-bearing function.
2. Active callee upward overwrite from a lower-address active callee.
3. Active exception-path overwrite during the same execution window.

For each candidate, require at least one of the following positive evidence items:
- an overflow-capable local object;
- a concrete write primitive;
- proven overlap arithmetic into the canary slot;
- verified active-call-chain membership during the canary-bearing window.

The following are NOT candidate mechanisms for canary corruption and must not be listed:
- residual stack pollution
- pre-fault stack reuse
- stale task pointer from a prior function
- generic stack smearing
- any theory derived only from a recognizable value found on the stack

{CANARY_RESIDUAL_DATA_RULE} {CANARY_SLOT_ONLY_SCOPE_NOTE}

**Phase 2 Required Output**:
```
PHASE 2 RESULT:
   Candidate mechanisms evaluated:
      1. Self-frame local overflow: <evidence for/against>
      2. Active callee upward overwrite: <evidence for/against>
      3. Active exception-path overwrite: <evidence for/against>
   Leading hypothesis: <mechanism or indeterminate>
   Evidence: <concrete items>
   Unresolved: <blocked verification>
```

### Phase 3: Conditional Provenance Check (ONLY IF NEEDED)

After Phase 1 has closed the canary slot, use `classify_saved_rip_frames_tool` for NON-CANARY frame-provenance questions if it is available. Do not use it to replace or bypass canary-slot closure.

Run a limited frame-provenance / phantom-frame check only if one of these is true:
1. the bt contains a statically impossible caller-callee edge;
2. duplicated saved RIPs remain unexplained after Phase 1 slot closure;
3. you are explaining corruption of saved RIP/RBP or another NON-CANARY slot.

If you run this phase:
- call `classify_saved_rip_frames_tool [--start-frame N] [--end-frame M]` as the PRIMARY path;
- use manual saved-RIP reading and `sym` only if the tool is unavailable or returns unproven;
- use it only to classify frame reliability;
- do not let it override verified canary-slot arithmetic;
- do not use it to invent interrupted-path blame for link_path_walk, zone_statistics,
   handle_mm_fault, or other stack-resident functions without a concrete write primitive.

### Final Output Constraint

If Phase 1 slot closure is unproven, or if none of the allowed mechanism families has positive
evidence, the final suspect code location MUST remain indeterminate.

### Action Execution Red-Line

{STACK_CAUSALITY_RED_LINE_RULE}

### Context Guardrails (Reference)

- Skip generic ghost-frame hunting, residual-stack narratives, and prior-occupant reconstruction
   until the canary slot has been closed by `resolve_stack_canary_slot` or a proven manual
   fallback.
- For __stack_chk_fail, the current bt is provisionally trustworthy for the active call path.
   Do NOT make phantom-frame detection the first mandatory task.
- Do not use stack-resident code addresses such as zone_statistics or link_path_walk as overflow-
   source evidence before the canary slot has been proven and the mechanism family has been narrowed.

### Switch-Away to Generic Stack Corruption

If diagnostic checks prove that the corrupted slot is NOT the canary slot (e.g., the true canary is intact but a saved RIP or RBP is corrupted), STOP the Stack Protector Fast Path immediately and switch to the standard `stack_overflow` / Generic Stack Corruption SOP (3.8). Do NOT force non-canary forensics into the Fast Path constraints.
""".strip(),
    "kasan_ubsan": """
## 3.11 KASAN / UBSAN Reports

Pattern: BUG: KASAN or UBSAN report.

Analysis:
1. KASAN allocation and free stacks in dmesg take priority over generic heuristics.
2. Use shadow-memory markers and access type to classify the bug.
3. UBSAN often indicates a logic bug rather than a memory-lifetime bug; keep that distinction explicit.
""".strip(),
    "dma_corruption": """
## 3.12 DMA Memory Corruption (Stray DMA Write)

Preconditions before suspecting DMA:
Treat these preconditions as the DMA-side realization of S1-S5 exclusion reasoning from the system layer. {DMA_PROMOTION_EVIDENCE_RULE}
1. Exclude use-after-free with kmem -S and poison-pattern checks. This is part of S4.
2. Exclude race or double-free style explanations. This is part of S4.
3. Confirm the corrupted memory is DMA-reachable. This is part of S5.
4. Check whether corruption correlates with I/O pressure. This is part of S5.
5. Prioritize dma_map or unmap violations if DMA API debug evidence exists. This is part of S5.

Non-indicators:
- intel_iommu=on by itself is not passthrough.
- ptov success or kmem -p emptiness alone is not proof of DMA.
- Mere module presence or generic dmesg errors are not device attribution.
- A bus-address-like value is not enough by itself. First prove whether the exact source field really contains that value.
- Do not call a value a DMA physical address until you have checked whether it fits the actual system physical-memory range and stated the current IOMMU context.

Before or alongside this DMA fragment, ensure the other S1-S4 gates are already closed: instruction-level provenance (S1), ordinary object-state validation (S2), and stack or snapshot artifact exclusion where applicable (S3), plus stronger software corruption-source exclusion (S4).

### Step 0: Stale Data Exclusion (MANDATORY before any value-match evidence)
The kmalloc/slab allocator does NOT zero memory on allocation. Any value found in an allocated
or recently-freed slab object may be residue from a previous allocation.

Before treating a suspicious value found in slab memory as proof of DMA origin:
1. **Stale residue test**: determine whether the slab object (or any adjacent object in the
   same slab page) could have been previously allocated to the suspected driver. If the
   corrupted object's cache (e.g., kmalloc-128) is routinely used by that driver for its
   internal structures, the value is a stale-data candidate, not DMA evidence.
2. **Dmesg-value match trap**: if the suspicious value matches a value that was *printed* to
   the kernel log (e.g., a log_info code, an event code, a firmware status code emitted via
   dev_info/dev_err/printk), that match is NOT corroborating evidence for DMA. The driver
   merely reported that value to dmesg; whether the device actually wrote it into system
   memory via DMA requires independent proof (see Step 2 DMA range overlap).
   - Specifically: a driver log_info(0xXXXXXXXX) call extracts the value from a firmware
     reply message, then passes it to printk. The value itself stays in the firmware reply
     DMA buffer or a driver-local stack variable -- it is NOT separately DMA-written to an
     arbitrary physical page. Finding 0xXXXXXXXX in a slab object proves only that the
     object previously held that value, not that the device performed a stray write.
3. If stale residue cannot be excluded (e.g., the driver is a heavy kmalloc-128 user),
   explicitly label the value match as "stale data candidate -- unconfirmed DMA evidence"
   and require validated DMA range overlap (Step 2) before elevating confidence.

### Step 1: Confirm IOMMU Mode
- Check vmcore-dmesg first for iommu, dmar, passthrough, translation, smmu patterns.
- Prefer p saved_command_line over log-based command-line recovery.
- Do not claim passthrough unless iommu=pt, default domain type identity, or equivalent runtime evidence is explicit.
- If IOMMU mode cannot be proven from logs, probe runtime kernel variables when available and cap DMA confidence if still unverifiable.
- On x86, if no Intel IOMMU or equivalent remapping evidence is present, you may treat DMA-address-equals-physical-address only as a conditional working assumption, not as a proven fact.
- Before labeling a value physical or DMA-backed, validate it against sys -m or kmem -i style memory-range evidence when available.

**Strong IOMMU-enabled indicators (any one is sufficient to conclude IOMMU is operational):**
- dmesg contains "DMAR: IOMMU enabled", "Intel-IOMMU: enabled", or "iommu: Default domain
  type: Translated" -- these are conclusive.
- The loaded module list contains vfio_iommu_type1 or vfio_iommu_type1_nesting -- VFIO
  requires a functional IOMMU; its presence implies translation is active for at least the
  passthrough devices. If the suspect device is NOT a VFIO-assigned device, check whether
  the non-VFIO devices share the same IOMMU group or use a separate identity/passthrough
  domain before concluding IOMMU covers the suspect device's DMA.
- For MSI or MSI-X interrupts: examine the msi_desc for the relevant IRQ. If
  msi_desc.msg.arch_addr_lo.dmar_format == 1 (VT-d remapping format), the interrupt is
  routed through the DMAR remapping unit, which is strong evidence that VT-d is operational
  on this platform. Note: interrupt remapping being active does NOT by itself prove DMA
  remapping is enabled for the suspect device -- these are separate VT-d features -- but it
  is a strong environmental signal that IOMMU is enabled system-wide.
- struct device.iommu_group != NULL for the suspect PCI device confirms it is assigned to
  an IOMMU group and subject to DMA remapping.

**If any strong IOMMU-enabled indicator is present**, DMA corruption confidence MUST be
downgraded from medium to low until device-specific DMA remapping exclusion is completed:
specifically, you must confirm whether the suspect device's IOMMU group uses a passthrough
domain or a translated domain before concluding that a stray DMA write could reach an
arbitrary physical page.

### Step 2: Device DMA Configuration
Sub-step A: inspect adjacent pages first when the target page is reserved or unreadable.
- Use ptov and vtop on neighboring pages.
- If readable, dump both hex and ASCII and look for device fingerprints such as Ethernet headers, mlx5 CQE blocks, NVMe CQE or SQE patterns, or qla2xxx IOCB signatures.

Sub-step B: extract suspect-device DMA ranges only after fingerprint work or when adjacent pages are unreadable.
- Use pci_dev.dev.driver_data to locate the runtime driver object when possible.
- Verify object and field paths against the current kernel layout before dereferencing module-private objects.
- Check whether the faulting PA lies within a validated DMA buffer range.
- Do not guess protocol-layer or firmware-message struct names for a driver-private queue object. Load module symbols with mod -s first and then inspect the actual driver-private type or field path.
- If struct -o <guessed_type> fails on a module crash path, do not keep guessing private types. Instead: (1) load module symbols with `mod -s <module>` first; (2) if symbol enumeration is then necessary, emit it as `run_script` with BOTH a concrete module target AND an immediate grep filter: `sym -l <module> | grep -i <keyword>`. `sym -l` without `| grep <pattern>` is ALWAYS forbidden — even with a module target — because it will flood the context and cause a hard failure.

Sub-step C: when driver structs are unavailable, inspect generic dma_ops and coherent_dma_mask to understand protection level.

Sub-step D: field-type disambiguation before naming the root cause.
- After identifying the corrupted object and field offset, determine the declared C type of that field from driver source, debug info, or a defensible offset-to-source correlation.
- If the field type is dma_addr_t: the observed bus address may be type-correct but used in the wrong semantic role. Classify this as field_type_misuse or missing_conversion, not generic overwrite.
- If the field type is void * or another pointer type: a low canonical physical-looking value in that field indicates write_corruption, race_condition, or reinit_path_bug.
- Do not conflate these mechanisms. Same bad value, different fix direction.

### Step 3: Corrupted Page DMA Mapping State
- Use vtop and kmem -p to understand page ownership.
- Distinguish reserved, slab, anonymous, and swap-backed pages.
- If CR2 is the candidate PA under a RIP-CR2 contradiction, treat that as a dedicated DMA-forensics path and validate it before reading memory.

### Reserved-Page Decision Gate
- PG_reserved does not imply DMA buffer.
- If all adjacent pages are reserved, cross-check against BIOS e820 reserved ranges before spending steps on driver symbol work.
- If CR2_PA falls inside a BIOS-reserved range, software pointer corruption leading to a garbage PA becomes the primary hypothesis and DMA confidence must be downgraded.

### Step 4: DMA Burst Pattern Validation
A stray DMA write typically exhibits specific spatial characteristics that distinguish it from
software corruption or stale data. Before asserting a "DMA burst" or "stray DMA write",
validate the following:

1. **Scope of corruption**: a true DMA burst usually overwrites a contiguous, cache-line-aligned
   or page-aligned region. If the suspicious values appear only in a few isolated locations
   scattered across a slab page (e.g., one 8-byte word at offset +0x18 in object A, and a
   single embedded value at +0x18 in object B), this pattern is NOT consistent with a DMA
   burst. Isolated suspicious values are more consistent with stale data or software corruption.
2. **Uniformity of overwrite**: in a DMA burst, ALL bytes in the overwritten region are
   typically replaced with device-generated data. If the surrounding fields look structurally
   valid (correct list_head self-links, plausible flags, non-garbage padding), the corruption
   is localized and inconsistent with a coarse-grained DMA write that would replace the
   entire cache line or page.
3. **Value range and structure**: DMA reply payloads from SCSI/SAS/NVMe controllers contain
   structured fields (descriptor type, SMID, VP ID, flags, physical address). If the
   "suspicious" values are a mix of small integers, partial bit patterns, and one matching
   log_info code, verify that the entire object can be decoded as a valid reply frame before
   treating the field match as a payload fingerprint.
4. **Minimum assertion threshold**: do NOT assert "DMA burst overwrote the slab page" unless
   you have: (a) at least 4 contiguous 8-byte words showing coherent device-specific data,
   OR (b) validated DMA-range overlap PLUS a decoded protocol structure. A single matching
   value in one slab object is insufficient regardless of how visually similar it appears
   to a logged driver value.

### DMA Evidence Chain Rules
- High confidence requires both payload fingerprint (validated via bit-layout decoding) and
  validated DMA-range overlap (target PA confirmed within device DMA buffer).
- Medium confidence may use one of the two, but must state what remains unproven and must
  explicitly label the result provisional.
- If neither fingerprint nor range overlap exists, DMA remains low-confidence only.
- Do not name a specific device without at least one device-side evidence item.
- If IOMMU is confirmed operational for the suspect device (translated domain), DMA
  confidence must remain low until IOMMU bypass or misconfiguration is proven.
""".strip(),
    "driver_source_correlation": """
## 3.13 Driver Source Correlation

Use this SOP when the crash path is inside a driver, struct -o cannot validate the private type, or offset-only reasoning is stalling.

### Step 1: Function-pointer anchor
- If an object dump contains a pointer inside the active module text range, resolve it with sym.
- Treat the resolved function name as a structural anchor and infer which runtime object type would legally store that callback at the observed offset.

### Step 2: Structural fingerprints
- 0xFEE0xxxx values are APIC or MSI target addresses and can fingerprint interrupt-queue objects.
- Self-referential pointers usually indicate embedded list_head nodes and provide container offsets.
- Combine these fingerprints with disassembly-derived offsets before guessing any type name.

### Step 3: Open-source cross-reference
- For drivers with upstream or historically open source, correlate the crashing function, nearby helper names, and observed offsets against the matching kernel source tree.
- Primary reference: https://elixir.bootlin.com/linux/<version>/source
- Prefer identifying the exact field name and declared type at the corrupted offset over naming the entire struct family only.

### Step 4: Field-type classification
- dma_addr_t field holding a bus address later dereferenced as a virtual pointer => field_type_misuse or missing_conversion.
- void * or struct pointer field holding a low canonical physical-looking address => write_corruption, race_condition, or reinit_path_bug.
- If source correlation cannot identify the field type, explicitly say so and keep the corruption_mechanism bounded as unknown.

### Step 5: Upstream fix correlation
- After confirming driver and function, search for known upstream fixes, CVEs, or stable backports in the same queue, reset, reinit, or reply-processing path.
- Cite only verifiable references. If you cannot verify an exact patch, report the bug pattern without inventing a commit.
""".strip(),
    "stack_frame_forensics": """
## 3.8a Stack Frame Forensics SOP

Use this SOP for generic stack-smearing / phantom-frame forensics when the main problem is
frame reliability, subsystem-inconsistent backtraces, or corruption of NON-CANARY stack data.
Do NOT use this as the first-line SOP for explicit stack-protector / __stack_chk_fail cases;
those cases must use the dedicated Stack Protector Fast Path first.

### [STOP] MANDATORY EXECUTION ORDER -- NON-NEGOTIABLE

You MUST execute Phases 1 through 5 **strictly in order**. Each phase has a GATE CHECK that
must be passed before proceeding to the next phase. Skipping phases, reordering phases, or
pursuing side investigations (such as disassembling non-canary-bearing functions) before
completing Phase 3 is FORBIDDEN.

**Phase Gate Rules**:
- Phase 1 GATE: You must have called `classify_saved_rip_frames_tool` or an equivalent manual
   fallback, then explicitly stated the FIRST phantom frame and
  the LAST trusted frame before proceeding to Phase 2. If you cannot identify phantom frames,
  state "no phantom frames detected" with evidence.
- Phase 2 GATE: You must have classified the phantom frame mechanism (smearing / exception
  splice / corrupted saved RIP) before proceeding to Phase 3.
- Phase 3 GATE: You must have classified the corrupted slot kind (corrupted_saved_rip,
   corrupted_saved_rbp, corrupted_non_canary_local, smeared_stack_region, or
   unresolved_stack_region) and produced at least one raw-stack read confirming the
   classification before proceeding to Phase 4. Do NOT derive any stack address by
   guessing from raw values without independent frame-layout proof.
- Phase 5 GATE: Before naming ANY suspect function, you must have completed the causality
  check with concrete evidence. If no mechanism has positive evidence, the conclusion MUST
  be "indeterminate -- partial dump prevents closure" and the suspect code location MUST be
  left empty or marked "unknown".

**Dead-End Detection Rule**: If you have spent 3 or more consecutive steps pursuing a single
hypothesis (e.g., disassembling a candidate function, searching for its RBP, examining its
log entries) without producing at least one of the following concrete evidence items, you MUST
STOP and re-evaluate from the last completed Phase gate:
  - An overflow-capable local object (array, struct buffer, VLA)
  - A concrete write primitive (memcpy, strcpy, copy_from_user, or explicit store instruction)
  - Proven slot-overlap arithmetic showing the write can reach the corrupted slot
  - Verified saved-RIP or saved-RBP linkage proving the function is on the active call chain

If after re-evaluation the hypothesis still lacks evidence, ABANDON it and proceed to the
next candidate mechanism in Phase 5.

### Phase 1: Frame-by-Frame Saved-RIP Validation

Goal: identify the FIRST unreliable (phantom) frame in the backtrace.

1. Call `classify_saved_rip_frames_tool [--start-frame N] [--end-frame M]` as the DEFAULT path.
   The tool deterministically reads saved RIP values from raw stack data, resolves them with
   `sym`, and reports the last trusted frame plus the first unreliable phantom-frame candidate.

2. Only if that tool is unavailable or returns unproven, perform the manual fallback:
   starting from the outermost trusted frame (e.g., system_call_fastpath -> sys_open -> ...),
   walk inward (toward lower addresses / higher frame numbers in bt) and validate each frame's
   saved RIP:
   a. Read the saved RIP at [frame_addr] from the raw stack dump (bt -f output).
   b. Use `sym <saved_RIP>` to resolve the function.
   c. Verify that the resolved function is a **statically plausible caller** of the function
      in the next-inner frame. For example, security_inode_permission is a legitimate callee
      of __inode_permission; zone_statistics is NOT.
   d. If the resolved function is from an unrelated subsystem (e.g., mm/vmstat in a VFS path),
      mark this frame as the **first suspect phantom frame**.

3. Check for **duplicate saved RIPs**: if two or more consecutive frames share the exact same
    saved RIP value, treat this as a strong unwind or exception-boundary hint, not an automatic
    proof of stack smearing.
    - In an ordinary uninterrupted call chain, duplicated consecutive return addresses are highly
       suspicious and must be explained.
    - However, before labeling the pattern as phantom-frame corruption, rule in or rule out
       exception nesting, pt_regs boundaries, unwinder residue, or stack-scan artifacts.
    - Only after that context check may you classify the first duplicated frame as phantom.

4. Record and report:
   - The last trusted frame (highest frame number with valid saved RIP and plausible caller edge).
   - The first phantom frame (frame number, address, and the anomalous saved RIP value).
    - Whether subsequent frames are truly phantom, exception-nested, or merely unwind-adjacent.
       Do not collapse these categories into generic stack smearing without supporting evidence.

**Phase 1 Required Output** (you MUST produce this before proceeding to Phase 2):
```
PHASE 1 RESULT:
   Source: <classify_saved_rip_frames_tool | manual fallback>
   Last trusted frame: #<N> <function> at <address>
   First unreliable frame: #<N> <function> at <address> (<reason>)
   Reliability summary: <duplicate RIPs / caller mismatch / exception-adjacent / none>
   Unreliable range: #<X>-#<Y> or <none>
   Manual notes: <only if the tool was unavailable or unproven>
```

### Phase 2: Phantom Frame Mechanism Classification

After identifying phantom frames, classify the mechanism:

1. **Stack smearing (most common)**: The crash frame unwinder scanned corrupted stack data and
   misidentified kernel text addresses as saved RIPs, producing phantom frames. Indicators:
   - Duplicate saved RIPs across consecutive frames.
   - Resolved function has no call-graph edge to adjacent frames.
   - The phantom frame's "function" has a tiny stack footprint (no local arrays) -- it cannot
     be an overflow source; it is merely a code-address value that happened to be on the stack.

2. **Exception splice**: A page fault, interrupt, or NMI caused exception handler frames to be
   nested on the same stack. Indicators:
   - A pt_regs or exception-entry signature in the stack data between the interrupted frame
     and handler frames.
   - The phantom "function" is actually a legitimate exception-handler callee.

3. **Corrupted saved RIP**: A single frame's return address was overwritten with a new value.
   Indicators: only one frame is anomalous and the surrounding frames are valid.

Report the classification explicitly before proceeding to Phase 3.

**Phase 2 Required Output**:
```
PHASE 2 RESULT:
  Mechanism: <stack_smearing | exception_splice | corrupted_saved_rip>
  Evidence: <list key indicators>
  Implication: frames #<X>-#<Y> are <phantom/exception-nested/corrupted>, do NOT use them
  for caller-callee or spatial reasoning.
```

### Phase 3: Corrupted Stack Slot Reconstruction

Goal: identify the corrupted slot, classify its kind, and read its raw contents.

Classify the target as one of:
  - corrupted_saved_rip      -- a return address slot containing an unexpected or bad value
  - corrupted_saved_rbp      -- a saved frame-pointer slot that was overwritten
  - corrupted_non_canary_local -- a local variable or spilled register slot that was corrupted
  - smeared_stack_region     -- multiple consecutive slots with incoherent data
  - unresolved_stack_region  -- scope of corruption not yet bounded

1. For corrupted_saved_rip or corrupted_saved_rbp:
   a. Read the raw stack around the affected frame with `rd -x <addr> <count>`.
   b. Compare the saved-RIP value against `sym` to determine whether it is a plausible
      kernel text address.
   c. Derive the frame RBP by frame-pointer chain only when the prologue independently
      supports it. Do NOT guess RBP from raw values alone.

2. For corrupted_non_canary_local or smeared_stack_region:
   a. Dump the suspect region and classify each 8-byte word (text address, data pointer,
      small integer, ASCII bytes).
   b. Note which frame's local layout (local array, spilled register, struct copy) aligns
      with the corrupted slot range.

3. For unresolved_stack_region:
   a. Dump and classify; defer precise boundary delineation to Phase 4.

4. Call `resolve_stack_canary_slot <function>` ONLY if evidence from Phases 1 or 2 has
   already confirmed this is a canary-corruption case (explicit stack-protector panic or
   __stack_chk_fail in bt). Otherwise, do NOT invoke that tool here.

5. Do NOT derive any stack address by guessing from raw values without independent
   frame-layout proof.

**Phase 3 Required Output**:
```
PHASE 3 RESULT:
  Corrupted slot kind: <corrupted_saved_rip | corrupted_saved_rbp | corrupted_non_canary_local |
                        smeared_stack_region | unresolved_stack_region>
  Address: <slot address or region bounds>
  Raw contents: <value(s) read from the slot>
  Classification evidence: <why this kind was chosen>
  Frame-layout proof: <how RBP/address was derived, or "N/A -- region not RBP-relative">
```

### Phase 4: Corruption Region Delineation

Goal: map the exact corrupted stack region.

1. Identify the corruption zone boundaries:
   a. Upper bound: the lowest trusted frame above the phantom frames (Phase 1 last trusted frame).
   b. Lower bound: the first unreliable frame address, the corrupted saved-slot address, or the
      lowest bounded address recovered in Phase 3, whichever is most restrictive.
   c. The region between these bounds contains the smeared/corrupted stack data.

2. Dump and annotate the entire corruption zone with `rd -x <lower_bound> <count>`.

3. For each 8-byte word in the zone, classify it as:
   a. A valid kernel text address (use `sym` to verify) -- candidate smeared saved-RIP or
      function-pointer residue.
   b. A valid kernel data/stack/heap address -- candidate spilled pointer or structure field.
   c. A small integer -- candidate local variable residue (e.g., fd number, flags, counter).
   d. ASCII-decodable bytes -- candidate pathname fragment (but apply the String-Evidence Gate
      from the main playbook before attributing string semantics).
   e. High-entropy / random-looking -- possible original canary fragment or uninitialized data.

### Phase 5: Overflow Source Tracing

Goal: determine WHICH function's writes produced the corrupted data.

**CRITICAL CONSTRAINT**: On x86-64, a standard local buffer overflow writes from LOW addresses
toward HIGH addresses (array index increases upward). Therefore:
- Only a function whose active frame is at a LOWER address than the corrupted region can have
  overflowed UPWARD into that region via a standard buffer overflow.
- A function at a HIGHER address (earlier caller) CANNOT overflow downward into a lower-address
  region via standard buffer overflow.

Procedure:

1. **Identify candidate source mechanisms** (evaluate ALL before final attribution):

   a. **Direct saved-RIP/RBP overwrite**: A write primitive (memcpy, strcpy, copy_from_user,
      explicit store, or struct copy) targeted the saved return address or saved frame-pointer
      slot, or an adjacent local buffer overflowed into it.
      - To investigate: identify the function whose frame contains the corrupted slot; audit its
        local buffer operations, structure copies, and inline callees for overflow-capable paths.

   b. **Active callee upward overwrite**: A function whose active frame is at a LOWER stack
      address overflowed a local buffer upward into the corrupted region at a higher address.
      - To investigate: for each active frame below the corrupted slot, check for overflow-capable
        local buffers (char arrays, struct copies, memcpy targets) and verify that the write
        can arithmetically reach the corrupted slot address.

   c. **Exception-path splice or overwrite**: A page fault, interrupt, or NMI nested an exception
      handler on the same stack, and a write within that handler path corrupted the slot.
      - To investigate: look for pt_regs or exception-entry signatures in the stack region between
        the interrupted frame and the corrupted slot.

   d. **Stack reuse or stale residue (non-canary slots)**: For saved-RBP, saved-RIP, or
      non-canary local slots, a prior function's return left residue that the current frame's
      bt interpretation misidentifies as a corrupt write. This mechanism is NOT applicable if
      the slot is a confirmed canary slot.
      - To investigate: check whether the suspicious value is consistent with a prior call-chain
        occupant's local layout or return value.

   e. **Frame reconstruction error**: The bt or unwind logic misidentified the slot as corrupted
      because the frame layout was miscomputed (e.g., non-standard prologue, tail-call, or
      inlined leaf callee).
      - To investigate: verify the actual prologue with `dis -rl <function>` and check whether
        the alleged corrupted address is actually within the function's declared frame.

2. **Match corruption data to source**:
   a. If zone_statistics return addresses appear in the corrupted zone, this strongly suggests
      that zone_statistics was legitimately called during the VFS path (as part of page
      allocation), its return address was left as stale residue, and the crash frame unwinder
      later misidentified it as a saved RIP.
   b. If the canary contains a value like 0x2 (fd number from sys_open's return), this points
      to VFS-path local variable residue from a previously-returned helper.
   c. Cross-reference recognized values with the active syscall path to identify the prior
      stack occupant.

3. **Produce bounded conclusion**:
   a. If one mechanism has positive evidence (matching residue data, identified prior occupant,
      or proven overflow-capable buffer), name it as the leading hypothesis.
   b. If multiple mechanisms remain plausible, list them ranked by evidence strength.
   c. If the partial dump prevents definitive closure, state which verification steps are blocked
      and keep the conclusion provisional.
   d. NEVER name a final overflow source based solely on frame size, function complexity, or
      "stack-heavy" reputation without concrete write-path evidence.

4. **Action execution red-line after mathematical elimination**:
   a. {STACK_CAUSALITY_RED_LINE_RULE}

**Phase 5 Required Output**:
```
PHASE 5 RESULT:
  Candidate mechanisms evaluated:
    1. Direct saved-RIP/RBP overwrite: <evidence for/against>
    2. Active callee upward overwrite: <evidence for/against>
    3. Exception-path splice or overwrite: <evidence for/against>
    4. Stack reuse or stale residue (non-canary slots): <evidence for/against>
    5. Frame reconstruction error: <evidence for/against>
  Leading hypothesis: <mechanism> (confidence: <high/medium/low>)
  Evidence: <concrete items>
  Unresolved: <what the partial dump prevents from verifying>
  Suspect code location: <function or "indeterminate"> (ONLY if concrete evidence exists)
```

**REMINDER**: Stack reuse or stale residue (mechanism d) IS a valid candidate for corruption of
saved RBP, saved RIP, or non-canary local slots in generic stack forensics. If at any point
evidence confirms this is a canary-slot corruption case, switch to the Stack Protector Fast Path.

### Switch-Back to Stack Protector Fast Path

If at any point during Phases 1-5 the evidence shifts to indicate a canary-slot corruption
(e.g., the corrupted slot is confirmed as the gs:0x28 canary, or bt reveals __stack_chk_fail
in the active call chain), STOP the generic SOP immediately and switch to the dedicated Stack
Protector Fast Path (SOP 3.8b) from the beginning. Do NOT run both SOPs simultaneously.

**FINAL OUTPUT CONSTRAINT**: If your Phase 5 leading hypothesis has confidence "low" or
"indeterminate", you MUST set the final "suspect_code_location" to "indeterminate -- insufficient
evidence" rather than naming a specific function. Naming a function without concrete evidence
is a critical analysis error that misleads the customer.
""".strip(),
    "advanced_techniques": """
## PART 5: ADVANCED TECHNIQUES

### 5.1 Reconstructing Local Variables
- Use bt -f, dis -rl, and ABI knowledge to reconstruct only what is defensible.

### 5.2 Compiler Optimizations
- Treat inlining, tail calls, and aggressive register allocation as sources of backtrace incompleteness.

### 5.3 Multi-CPU Correlation
- Use bt -a only for hard lockup.
- Use foreach UN bt and bt -c <cpu> for deadlock, race, or corruption analysis.

### 5.4 KASLR Considerations
- Let crash resolve KASLR when symbols match, and avoid fixed-offset assumptions.

### 5.5 Error Recovery and Fallbacks
- Treat invalid-address, seek-error, and incomplete-dump conditions as evidence, not as reasons to keep retrying the same read.

### 5.6 Backtrace Reliability Assessment
- Validate return addresses, stack progression, and control-flow plausibility before trusting bt as a root-cause source.

### 5.7 Tracing Garbage Values
- Use bounded search, reverse page ownership, and neighborhood inspection to identify the writer of suspicious values.

### 5.8 DMA Corruption Forensics
- Refer to the DMA workflow for full device-side attribution rules.
""".strip(),
}
