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
10. If printable bytes appear near the canary or in a suspect stack slot, treat them as undecoded payload until proven otherwise. Do not call them pathname or filename evidence unless you validate contiguous string structure plus a plausible copy primitive.
11. If any checklist item remains unproven, keep the final mechanism bounded and provisional instead of naming a direct overflow source.

Analysis:
1. Distinguish process, IRQ, and exception stack overflows.
2. Treat bt as provisional when frames are context-inconsistent; first validate return addresses, stack progression, and control-flow plausibility before trusting the call chain.
3. Use bt -f only with a concrete pid or task when you need per-frame details for that task; never use bt -f with a frame number.
4. On x86_64 with a frame-pointer prologue, saved caller RBP is at [RBP] and the return address is at [RBP+8]; compute the canary slot from the disassembly-derived offset such as rbp-0x18 instead of guessing from older frames.
5. On x86-64, the stack grows downward (high → low). In a typical contiguous local-stack-buffer overflow within function F, writes often progress toward higher addresses and may hit F's canary and caller-side data. This is not universal: wrong-pointer memcpy or memmove, struct-pointer writes, negative indices such as buf[-8], and use-after-free or other arbitrary-write primitives may write in either direction or without local stack-direction constraints. Always validate the concrete write primitive and address progression before attributing causality.
6. For any claim that caller locals overlap an active callee frame, compute caller post-prologue RSP first. Since the callee frame is allocated below the caller's call-site RSP, an alleged callee canary above that boundary is a proof error.
7. Across an exception-entry boundary, relative frame addresses alone do NOT prove that a pre-exception frame or a handler frame locally overflowed into the other. If provenance is unproven, keep local-overflow attribution provisional and evaluate alternatives such as stack-slot reuse, stale residue, or misidentified frame links.
8. Do not use sub rsp size, a large function offset, or labels such as "large frame" as standalone evidence for overflow. On their own, they are only weak complexity cues and cannot justify naming a suspect function.
9. Inspect task_struct and thread_info fields with task -R when you need stack boundaries or execution-context validation.
10. Inspect STACK_END_MAGIC and the raw stack contents with rd -x when needed.
11. For kernel-stack pages, use vtop or task-derived stack boundaries when page validation is required; do NOT use kmem -S on stack addresses — the kernel stack is not a slab allocation and kmem -S will always return a useless "not allocated in slab subsystem" error.
12. In panic backtraces, frames prefixed with ? are stack-scan candidates rather than trusted frame-pointer links; treat them as hints only, not proven caller-callee relationships. However, ? frames from exception handlers are diagnostically significant.
13. Look for recursive call patterns, overwritten return-address regions, and frames that jump into unrelated subsystems.
14. If a bt segment implies an unexpected edge such as a VFS permission helper apparently calling an mm or vmstat helper directly, do not treat that adjacency as proof of normal execution. First decide whether it is a corrupted saved RIP, a stack-scan artifact, or an exception-nested splice.
15. When sym fails on a non-symbol kernel address found repeatedly on the stack, do NOT abandon the address. Instead run vtop <address> to validate the page, then kmem -p <PA> to check page state. The address may be a per-CPU pointer, vmalloc object, or module data address that reveals the corruption source.
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
    "stack_frame_forensics": """
## 3.8a Stack Frame Forensics SOP

Use this SOP when stack-protector fires (__stack_chk_fail) or when the backtrace contains
subsystem-inconsistent frames (e.g., an mm/vmstat helper appearing inside a VFS/security
call chain). This SOP provides the positive step-by-step procedure for frame-level validation,
phantom frame detection, and overflow source tracing that complements the defensive rules in
the stack_corruption playbook.

### ⛔ MANDATORY EXECUTION ORDER — NON-NEGOTIABLE

You MUST execute Phases 1 through 5 **strictly in order**. Each phase has a GATE CHECK that
must be passed before proceeding to the next phase. Skipping phases, reordering phases, or
pursuing side investigations (such as disassembling non-canary-bearing functions) before
completing Phase 3 is FORBIDDEN.

**Phase Gate Rules**:
- Phase 1 GATE: You must have identified and explicitly stated the FIRST phantom frame and
  the LAST trusted frame before proceeding to Phase 2. If you cannot identify phantom frames,
  state "no phantom frames detected" with evidence.
- Phase 2 GATE: You must have classified the phantom frame mechanism (smearing / exception
  splice / corrupted saved RIP) before proceeding to Phase 3.
- Phase 3 GATE: You must have computed RBP_absolute using the prologue-counting method
  (step 4a-4d below) and verified the canary slot contents before proceeding to Phase 4.
  Do NOT compute RBP by guessing from raw stack values.
- Phase 5 GATE: Before naming ANY suspect function, you must have completed the causality
  check with concrete evidence. If no mechanism has positive evidence, the conclusion MUST
  be "indeterminate — partial dump prevents closure" and the suspect code location MUST be
  left empty or marked "unknown".

**Dead-End Detection Rule**: If you have spent 3 or more consecutive steps pursuing a single
hypothesis (e.g., disassembling a candidate function, searching for its RBP, examining its
log entries) without producing at least one of the following concrete evidence items, you MUST
STOP and re-evaluate from the last completed Phase gate:
  - An overflow-capable local object (array, struct buffer, VLA)
  - A concrete write primitive (memcpy, strcpy, copy_from_user, or explicit store instruction)
  - Proven slot-overlap arithmetic showing the write can reach the canary slot
  - Verified saved-RIP or saved-RBP linkage proving the function is on the active call chain

If after re-evaluation the hypothesis still lacks evidence, ABANDON it and proceed to the
next candidate mechanism in Phase 5.

### Phase 1: Frame-by-Frame Saved-RIP Validation

Goal: identify the FIRST unreliable (phantom) frame in the backtrace.

1. Starting from the outermost trusted frame (e.g., system_call_fastpath → sys_open → ...),
   walk inward (toward lower addresses / higher frame numbers in bt) and validate each frame's
   saved RIP:
   a. Read the saved RIP at [frame_addr] from the raw stack dump (bt -f output).
   b. Use `sym <saved_RIP>` to resolve the function.
   c. Verify that the resolved function is a **statically plausible caller** of the function
      in the next-inner frame. For example, security_inode_permission is a legitimate callee
      of __inode_permission; zone_statistics is NOT.
   d. If the resolved function is from an unrelated subsystem (e.g., mm/vmstat in a VFS path),
      mark this frame as the **first suspect phantom frame**.

2. Check for **duplicate saved RIPs**: if two or more consecutive frames share the exact same
   saved RIP value, this is a definitive stack smearing signal. In any normal call chain, two
   consecutive frames cannot have identical return addresses. The first frame with the duplicated
   saved RIP is the first phantom frame.

3. Record and report:
   - The last trusted frame (highest frame number with valid saved RIP and plausible caller edge).
   - The first phantom frame (frame number, address, and the anomalous saved RIP value).
   - All subsequent frames between the first phantom frame and the canary-bearing frame are
     also unreliable and should be treated as smeared stack data.

**Phase 1 Required Output** (you MUST produce this before proceeding to Phase 2):
```
PHASE 1 RESULT:
  Last trusted frame: #<N> <function> at <address> (saved RIP <value> → <resolved_sym> ✓)
  First phantom frame: #<N> <function> at <address> (saved RIP <value> → <resolved_sym> ✗ reason: <why implausible>)
  Smearing signal: <duplicate saved RIPs? / subsystem mismatch? / other>
  Frames #<X> through #<Y> are UNRELIABLE.
```

### Phase 2: Phantom Frame Mechanism Classification

After identifying phantom frames, classify the mechanism:

1. **Stack smearing (most common)**: The crash frame unwinder scanned corrupted stack data and
   misidentified kernel text addresses as saved RIPs, producing phantom frames. Indicators:
   - Duplicate saved RIPs across consecutive frames.
   - Resolved function has no call-graph edge to adjacent frames.
   - The phantom frame's "function" has a tiny stack footprint (no local arrays) — it cannot
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

### Phase 3: Canary Slot Reconstruction

Goal: compute the exact canary slot address from disassembly, not from bt frame addresses.

1. Disassemble the canary-bearing function with `dis -rl <function>`.

2. Identify the prologue sequence and compute RBP:
   a. The standard x86-64 frame-pointer prologue is: push %rbp; mov %rsp, %rbp.
   b. After `mov %rsp, %rbp`, RBP equals the address where old RBP was saved.
   c. Subsequent pushes (push %r12, push %rbx, etc.) and `sub $N, %rsp` extend the frame
      below RBP.

3. Identify the canary store instruction (e.g., `mov %rax, -0x18(%rbp)`) and compute:
   canary_slot_addr = RBP_absolute - offset

4. To compute RBP_absolute from the bt frame address:
   a. The bt frame address for the canary-bearing function is the RSP at the point where it
      called __stack_chk_fail (or the deepest callee).
   b. Count the total frame size below RBP: each push after `mov %rsp, %rbp` uses 8 bytes,
      plus the `sub $N, %rsp` value, plus 8 bytes for the __stack_chk_fail return address.
   c. RBP_absolute = bt_frame_address + total_size_below_rbp
   d. Verify by checking that the value at [RBP_absolute] in the raw stack dump looks like
      a valid saved RBP (a stack address within the task's stack range).

5. Read the canary slot contents and evaluate:
   a. If the value is a valid gs:0x28 canary (high-entropy random), canary is intact — the
      __stack_chk_fail was triggered by something else (rare).
   b. If the value is a non-random recognizable pattern (e.g., a kernel code address, a small
      integer like 0x2, a task_struct pointer, or a stack address), the canary was overwritten.
   c. Record both the slot address and its contents as primary forensic evidence.

**Phase 3 Required Output** (you MUST show the RBP computation steps):
```
PHASE 3 RESULT:
  Function: <canary_bearing_function>
  Prologue pushes after mov %rsp,%rbp: <list registers> = <N*8> bytes
  sub $<M>, %rsp
  __stack_chk_fail return address: 8 bytes
  Total below RBP: <N*8 + M + 8> bytes
  bt frame address (RSP at call): <address>
  RBP_absolute = <bt_frame_addr> + <total> = <computed_value>
  Verification: [RBP_absolute] = <value from rd> (valid stack addr? <yes/no>)
  Canary offset from disassembly: rbp-<offset>
  Canary slot address: <RBP_absolute - offset>
  Canary slot contents: <value> (expected canary from gs:0x28: <value>)
  Canary status: <intact | overwritten with <description>>
```

### Phase 4: Corruption Region Delineation

Goal: map the exact corrupted stack region.

1. Identify the corruption zone boundaries:
   a. Upper bound: the lowest trusted frame above the phantom frames (e.g., security_inode_permission).
   b. Lower bound: the canary-bearing function's frame top.
   c. The region between these bounds contains the smeared/corrupted stack data.

2. Dump and annotate the entire corruption zone with `rd -x <lower_bound> <count>`.

3. For each 8-byte word in the zone, classify it as:
   a. A valid kernel text address (use `sym` to verify) — candidate smeared saved-RIP or
      function-pointer residue.
   b. A valid kernel data/stack/heap address — candidate spilled pointer or structure field.
   c. A small integer — candidate local variable residue (e.g., fd number, flags, counter).
   d. ASCII-decodable bytes — candidate pathname fragment (but apply the String-Evidence Gate
      from the main playbook before attributing string semantics).
   e. High-entropy / random-looking — possible original canary fragment or uninitialized data.

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

   a. **Pre-fault residual-stack pollution**: The most common mechanism in VFS-path stack
      corruption cases.
      - During the active syscall path (e.g., path_openat → do_last → link_path_walk →
        walk_component → lookup_slow → ... → page allocator → zone_statistics), deep callees
        push frames into low-address stack regions.
      - When those deep callees return, their frame data persists as stale residue.
      - If a later exception (page fault, interrupt) causes new handler frames to be allocated
        in that same low-address region, the canary slot of the handler function may coincide
        with an address previously written by a returned callee.
      - To investigate: identify which earlier (now-returned) functions had frames overlapping
        the canary slot address. Disassemble candidate prior-occupant functions to check whether
        any write `current`, function pointers, or structure data to local variables at offsets
        that would land on the canary slot.

   b. **Active callee upward overflow**: A function at a LOWER address than the canary slot
      overflowed a local buffer upward, corrupting the canary at a higher address.
      - To investigate: check which active frames sit below the canary slot. For each, disassemble
        and look for overflow-capable local buffers (char arrays, struct copies, memcpy targets).

   c. **Exception-path active overwrite**: A function in the exception handler chain wrote past
      its own local bounds into the canary slot.
      - To investigate: disassemble exception-handler functions that were active at crash time.

   d. **Stack reuse from struct copy or memcpy**: A structure copy or memcpy operation copied
      data onto the stack beyond the destination object boundary.

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

**Phase 5 Required Output**:
```
PHASE 5 RESULT:
  Candidate mechanisms evaluated:
    1. Pre-fault residual-stack pollution: <evidence for/against>
    2. Active callee upward overflow: <evidence for/against>
    3. Exception-path active overwrite: <evidence for/against>
    4. Stack reuse from struct copy/memcpy: <evidence for/against>
  Leading hypothesis: <mechanism> (confidence: <high/medium/low>)
  Evidence: <concrete items>
  Unresolved: <what the partial dump prevents from verifying>
  Suspect code location: <function or "indeterminate"> (ONLY if concrete evidence exists)
```

**FINAL OUTPUT CONSTRAINT**: If your Phase 5 leading hypothesis has confidence "low" or
"indeterminate", you MUST set the final "可疑代码位置" to "indeterminate — insufficient
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
