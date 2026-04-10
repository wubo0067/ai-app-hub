#!/usr/bin/env python3
# -*- coding: utf-8 -*-

SOP_FRAGMENTS: dict[str, str] = {
    "address_search": """
## 1.6 Address Search SOP

Before executing any search, state which strategy you are using in reasoning.

Strategy 1: targeted region search.
- search -t <address> for current task stack.
- search -u <address> after set <pid> for user-space.
- search -s <start> -e <end> <address> for bounded VA ranges.

Strategy 1a: symbol-oriented raw region sweep.
- When you need to inspect a suspicious kernel memory region deeply and reinterpret raw bytes as likely kernel symbol addresses, prefer run_script with rd -SS <address> | grep "<pattern>".
- Use this when page-fault, pointer-corruption, or object-shape analysis has already identified a bounded suspect region and you need candidate function-pointer or embedded-string anchors.
- If a wider bounded region is required, add an explicit count to rd instead of switching to an unbounded search.
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

Do not emit rd against percent-gs syntax, registers, or shell-like expressions.
""".strip(),
    "stack_overflow": """
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
8. If the overwritten canary value matches current or a task_struct-derived pointer, treat it as a spill-location clue only. Find the exact disassembly stack-store that spilled current or the derived pointer, then prove that an adjacent overflow-capable local object or concrete write primitive in the same frame could reach that saved slot.
9. If adjacent frames imply an ordinary call edge that static code structure does not support, or splice unrelated subsystems without a proven exception bridge, treat that bt edge as unreliable until saved return addresses, frame links, or exception-entry provenance validate it.
10. If any checklist item remains unproven, keep the final mechanism bounded and provisional instead of naming a direct overflow source.

Analysis:
1. Distinguish process, IRQ, and exception stack overflows.
2. Treat bt as provisional when frames are context-inconsistent; first validate return addresses, stack progression, and control-flow plausibility before trusting the call chain.
3. Use bt -f only with a concrete pid or task when you need per-frame details for that task; never use bt -f with a frame number.
4. On x86_64 with a frame-pointer prologue, saved caller RBP is at [RBP] and the return address is at [RBP+8]; compute the canary slot from the disassembly-derived offset such as rbp-0x18 instead of guessing from older frames.
5. On x86-64, the stack grows downward (high → low). A buffer overflow in function F writes UPWARD and can only corrupt F's own canary and frames of F's callers (at higher addresses). It CANNOT corrupt frames pushed after F (at lower addresses). Always verify overflow direction vs victim frame address before attributing a corruption source.
6. Do not equate a bt frame address with RBP. When proving a canary address or overlap claim, reconstruct frame layout from the actual prologue, saved-frame links, and current stack contents.
7. For any claim that caller locals overlap an active callee frame, compute caller post-prologue RSP first. Since the callee frame is allocated below the caller's call-site RSP, an alleged callee canary above that boundary is a proof error.
8. When an exception (page fault, interrupt) fires during a function's execution, the exception handler pushes new frames at even lower addresses on the same stack. Identify these nested exception frames (often prefixed with ?), but do NOT treat the resulting layout as an ordinary uninterrupted call nest. Distinguish interrupted normal-path frames, hardware/pt_regs entry state, and exception-handler frames before applying overflow-direction causality.
9. Across an exception-entry boundary, relative frame addresses alone do NOT prove that a pre-exception frame or a handler frame locally overflowed into the other. If provenance is unproven, keep local-overflow attribution provisional and evaluate alternatives such as stack-slot reuse, stale residue, or misidentified frame links.
10. Do not use sub rsp size, a large function offset, or labels such as "large frame" as standalone evidence for overflow. On their own, they are only weak complexity cues and cannot justify naming a suspect function.
11. Inspect task_struct and thread_info fields with task -R when you need stack boundaries or execution-context validation.
12. Inspect STACK_END_MAGIC and the raw stack contents with rd -x when needed.
13. For kernel-stack pages, use vtop or task-derived stack boundaries when page validation is required; do NOT use kmem -S on stack addresses — the kernel stack is not a slab allocation and kmem -S will always return a useless "not allocated in slab subsystem" error.
14. In panic backtraces, frames prefixed with ? are stack-scan candidates rather than trusted frame-pointer links; treat them as hints only, not proven caller-callee relationships. However, ? frames from exception handlers are diagnostically significant.
15. Look for recursive call patterns, overwritten return-address regions, and frames that jump into unrelated subsystems.
16. If a bt segment implies an unexpected edge such as a VFS permission helper apparently calling an mm or vmstat helper directly, do not treat that adjacency as proof of normal execution. First decide whether it is a corrupted saved RIP, a stack-scan artifact, or an exception-nested splice.
17. When sym fails on a non-symbol kernel address found repeatedly on the stack, do NOT abandon the address. Instead run vtop <address> to validate the page, then kmem -p <PA> to check page state. The address may be a per-CPU pointer, vmalloc object, or module data address that reveals the corruption source.
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
1. Exclude use-after-free with kmem -S and poison-pattern checks.
2. Exclude race or double-free style explanations.
3. Confirm the corrupted memory is DMA-reachable.
4. Check whether corruption correlates with I/O pressure.
5. Prioritize dma_map or unmap violations if DMA API debug evidence exists.

Non-indicators:
- intel_iommu=on by itself is not passthrough.
- ptov success or kmem -p emptiness alone is not proof of DMA.
- Mere module presence or generic dmesg errors are not device attribution.
- A bus-address-like value is not enough by itself. First prove whether the exact source field really contains that value.
- Do not call a value a DMA physical address until you have checked whether it fits the actual system physical-memory range and stated the current IOMMU context.

### Step 1: Confirm IOMMU Mode
- Check vmcore-dmesg first for iommu, dmar, passthrough, translation, smmu patterns.
- Prefer p saved_command_line over log-based command-line recovery.
- Do not claim passthrough unless iommu=pt, default domain type identity, or equivalent runtime evidence is explicit.
- If IOMMU mode cannot be proven from logs, probe runtime kernel variables when available and cap DMA confidence if still unverifiable.
- On x86, if no Intel IOMMU or equivalent remapping evidence is present, you may treat DMA-address-equals-physical-address only as a conditional working assumption, not as a proven fact.
- Before labeling a value physical or DMA-backed, validate it against sys -m or kmem -i style memory-range evidence when available.

### Step 2: Device DMA Configuration
Sub-step A: inspect adjacent pages first when the target page is reserved or unreadable.
- Use ptov and vtop on neighboring pages.
- If readable, dump both hex and ASCII and look for device fingerprints such as Ethernet headers, mlx5 CQE blocks, NVMe CQE or SQE patterns, or qla2xxx IOCB signatures.

Sub-step B: extract suspect-device DMA ranges only after fingerprint work or when adjacent pages are unreadable.
- Use pci_dev.dev.driver_data to locate the runtime driver object when possible.
- Verify object and field paths against the current kernel layout before dereferencing module-private objects.
- Check whether the faulting PA lies within a validated DMA buffer range.
- Do not guess protocol-layer or firmware-message struct names for a driver-private queue object. Load module symbols with mod -s first and then inspect the actual driver-private type or field path.
- If struct -o <guessed_type> fails on a module crash path, the next step should be run_script with mod -s plus symbol enumeration such as sym -l <module> | grep -i <keyword>, not another guessed private type.

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

### DMA Evidence Chain Rules
- High confidence requires both payload fingerprint and validated DMA-range overlap.
- Medium confidence may use one of the two, but must state what remains unproven.
- If neither fingerprint nor range overlap exists, DMA remains low-confidence only.
- Do not name a specific device without at least one device-side evidence item.
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
