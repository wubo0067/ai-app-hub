#!/usr/bin/env python3
# -*- coding: utf-8 -*-

_LOCKUP_PLAYBOOK = """
## 3.2 Soft Lockup / Hard Lockup
Pattern: soft lockup or NMI watchdog hard lockup.

Analysis:
1. Use dis -l <stuck_function> 100 to identify loops or pause-based spin paths.
2. Check for missing cond_resched() or long IRQ-disabled sections.
3. For hard lockup, use bt -a to inspect all CPUs for lock contention or non-progress.
4. Use runq to inspect per-CPU queue backlog and scheduler imbalance.
""".strip()


_PROVENANCE_AND_CORRUPTION_SOURCE_GUIDE = """
## Provenance and Corruption-Source Guide

- Trace the last writer of every suspect register before classifying the overwrite mechanism.
- If crash-time register values differ from current vmcore bytes, treat the mismatch as a snapshot observation, not proof of DMA or overwrite source.
- When a faulting register was loaded from a concrete memory operand such as 0x10(%r13), you must read that exact source object before inferring corruption from a different object or argument register.
- Never replace the true source register with a more convenient argument register. If R13 produced RCX, reading RDI does not validate RCX provenance.
- When using aligned memory dumps to reason about sub-word fields, explain the field width and byte extraction explicitly before carrying the value forward.
- Validate task_struct, thread_info, kernel stack, and driver-private objects before escalating to external corruption.
- For pointer-corruption branches, explicitly rule in or rule out stack overflow, UAF, struct overwrite, and hardware error before naming DMA.
- Repeated abnormal 8-byte value patterns across nearby fields are corruption fingerprints. Compare neighboring fields and explain the pattern before escalating to DMA or generic wild-pointer language.
- If vmcore-dmesg shows recurring driver reset or discovery messages immediately before the crash, analyze that timing as a possible reinitialization or race window affecting the corrupted object.
""".strip()


_DIVIDE_OR_OPCODE_PLAYBOOK = """
## 3.9 Divide-by-Zero / Invalid Opcode
Pattern: divide error or invalid opcode.

Analysis:
1. Use dis -rl <RIP> to find div, idiv, or ud2.
2. For divide error, inspect the divisor register and confirm whether it is zero.
3. For ud2, correlate with BUG or WARN style source-side traps.
""".strip()


_BUG_WARN_PLAYBOOK = """
## BUG/WARN Playbook

- Treat BUG_ON and WARN_ON as symptom sites that still require control-flow and data-state validation.
- Read the triggering condition, correlate it with the active path, and decide whether it exposes the true root cause or downstream damage.
- If the warning is adjacent to refcount, list, or lifetime logic, still verify object state before concluding.
""".strip()


PLAYBOOKS: dict[str, str] = {
    "null_deref": """
## 3.1 NULL Pointer Dereference
Pattern: unable to handle kernel NULL pointer dereference.

Analysis:
1. Distinguish direct NULL from small-offset member access through NULL.
2. Reject the null_deref path immediately if the fault address is a large non-zero value or a non-canonical / invalid address in kernel context.
3. Check which register was NULL in bt output.
4. Use sym <RIP> and dis -rl <RIP> to identify the faulting instruction.
5. If offset is non-zero, use struct -o <type> to find the member at that offset.
6. Trace where the NULL pointer originated: return value, argument propagation, or struct-member chain.
7. Use task -R fields to judge whether the crash is in a driver path or kernel-core path.
""".strip(),
    "soft_lockup": _LOCKUP_PLAYBOOK,
    "hard_lockup": _LOCKUP_PLAYBOOK,
    "rcu_stall": """
## 3.3 RCU Stall
Pattern: rcu_sched self-detected stall or related RCU stall report.

Analysis:
1. Identify stall type first: rcu_sched, rcu_bh, or rcu_tasks.
2. Inspect stalled-task backtrace for long-held rcu_read_lock sections.
3. Check whether offline or online CPU operations delayed the grace period.
4. If CONFIG_RCU_NOCB_CPU is present, consider callback backlog accumulation.
""".strip(),
    "use_after_free": """
## 3.4 Use-After-Free / Memory Corruption
Pattern: paging request at a non-NULL address or a KASAN-style report.

Analysis:
1. Run kmem -S <address> only when the candidate address is expected to be a slab object or heap allocation; if the address may belong to a kernel stack, text, or non-slab page, use vtop or kmem -p on the translated page instead.
2. Recognize poison values such as 0x6b6b..., 0x5a5a..., and 0xdead....
3. Distinguish UAF, heap OOB write, and double-free style symptoms.
4. When KASAN is present, prioritize allocation and free stacks from dmesg.
5. Inspect slab state, shadow markers, and bad-page metadata before escalating.

"""
    + _PROVENANCE_AND_CORRUPTION_SOURCE_GUIDE,
    "pointer_corruption": """
## Pointer Corruption Playbook

- Treat pointer corruption as a provenance-first workflow, not a device-attribution workflow.
- Before considering DMA or hardware, close register provenance, object lifetime, and local corruption exclusion in that order.
- A snapshot mismatch between crash-time registers and current vmcore memory is diagnostic context only; it does not prove overwrite mechanism.
- If a driver or third-party module is on the crash path, validate the full driver object shape before escalating to external corruption.
- If the bad value came from a concrete load like mov 0x10(%r13), %rcx, the next validation target is the r13-based object, not a different argument register.
- If nearby fields repeat a suspicious high-bit prefix or bus-address-like pattern, preserve that as a corruption fingerprint and test it explicitly instead of hand-waving it as "looks physical".
- If a guessed struct name fails on a module-private object, stop guessing protocol-layer types and switch to module-symbol loading plus raw-object validation.

## 3.4 Use-After-Free / Memory Corruption
Pattern: paging request at a non-NULL address or a KASAN-style report.

Analysis:
1. Run kmem -S <address> only when the candidate address is expected to be a slab object or heap allocation; if the address may belong to a kernel stack, text, or non-slab page, use vtop or kmem -p on the translated page instead.
2. Recognize poison values such as 0x6b6b..., 0x5a5a..., and 0xdead....
3. Distinguish UAF, heap OOB write, and double-free style symptoms.
4. When KASAN is present, prioritize allocation and free stacks from dmesg.
5. Inspect slab state, shadow markers, and bad-page metadata before escalating.

"""
    + _PROVENANCE_AND_CORRUPTION_SOURCE_GUIDE,
    "hung_task": """
## 3.5 Deadlock / Hung Task
Pattern: task blocked for more than 120 seconds.

Analysis:
1. Distinguish true deadlock, lock starvation, and I/O hang first.
2. Use foreach UN bt to inspect all D-state tasks.
3. Inspect lock ownership with struct mutex and wait-chain backtraces.
4. If lockdep output exists in dmesg, prioritize it.
""".strip(),
    "atomic_sleep": """
## 3.6 Scheduling While Atomic
Pattern: BUG: scheduling while atomic.

Analysis:
1. Use task -R preempt_count to confirm atomic-context state.
2. Inspect bt for the sleeping function called under atomic constraints.
3. Distinguish sleep in hardirq context from sleep while holding a spinlock.
4. Common culprits include mutex_lock, GFP_KERNEL allocation, msleep, wait_event, and schedule_timeout in the wrong context.
""".strip(),
    "mce": """
## 3.7 Hardware Errors (MCE/EDAC)
Pattern: Machine Check Exception, Hardware Error, EDAC, PCIe Bus Error.

Analysis:
1. Inspect dmesg for Hardware Error and Machine Check Exception records.
2. Interpret MCE bank context with vendor-aware caution.
3. Distinguish correctable from uncorrectable EDAC events.
4. Check for AER, PCIe, DMAR, IOMMU, firmware, and BIOS errors that masquerade as generic corruption.
""".strip(),
    "divide_error": _DIVIDE_OR_OPCODE_PLAYBOOK,
    "invalid_opcode": _DIVIDE_OR_OPCODE_PLAYBOOK,
    "oom_panic": """
## 3.10 OOM Killer
Pattern: Out of memory, oom-kill constraint reports, or panic_on_oom path.

Analysis:
1. Distinguish global OOM from cgroup OOM.
2. Use dmesg memory stats and kmem -i to confirm memory pressure.
3. Inspect victim process footprint and dominant allocator growth.
4. For cgroup OOM, inspect the limit and fail-count context before blaming a kernel leak.
""".strip(),
    "general_protection_fault": """
## General Protection Fault Playbook

- Use this playbook only when the evidence actually points to x86 #13 or an equivalent protection-domain fault.
- Do not enter this playbook for BUG: unable to handle kernel paging request with Oops: 0000; that remains a page-fault investigation and should normally route to pointer_corruption or null_deref depending on the address shape.
- Start with canonical-address validation and provenance of the bad register or operand.
- Treat a non-canonical or partially plausible pointer as evidence of corruption, not evidence of a specific writer.
- Prefer software wild pointer, UAF, OOB, or race explanations before DMA or hardware unless device-side evidence is already present.
- A large non-zero kernel fault address is not a NULL dereference equivalent. Keep crash_type wording aligned with the actual address class.
- If a bad register came from a module-private object, load module symbols first and inspect the exact producer object before speculating about DMA or stale register state.
- If recurring device events in dmesg occur immediately before the fault, fold that timing into the live hypotheses instead of treating those logs as unrelated background noise.

## 3.9 Divide-by-Zero / Invalid Opcode
Pattern: divide error or invalid opcode.

Analysis:
1. Use dis -rl <RIP> to find div, idiv, or ud2.
2. For divide error, inspect the divisor register and confirm whether it is zero.
3. For ud2, correlate with BUG or WARN style source-side traps.
""".strip(),
    "bug_on": _BUG_WARN_PLAYBOOK,
    "warn_on": _BUG_WARN_PLAYBOOK,
    "stack_corruption": """
## Stack Corruption / Stack Protector Failure
Pattern: stack-protector: Kernel stack is corrupted in: <function>.

### ⛔ FIRST ACTION RULE

When this playbook is triggered, your VERY FIRST analysis actions (before ANY disassembly of
non-canary functions, before ANY hypothesis about overflow sources) MUST be:
1. Disassemble the canary-bearing function (from the panic string) to identify the canary slot.
2. Execute Phase 1 of the Stack Frame Forensics SOP (3.8a): frame-by-frame saved-RIP validation.
3. Produce the Phase 1 Required Output identifying the first phantom frame.

You are FORBIDDEN from disassembling handle_mm_fault, __do_page_fault, or any other non-canary
function until Phase 1-3 of the SOP are complete with their required outputs.

### Mandatory Stack Corruption Analysis Checklist

Before naming a local overflow source, complete this checklist in order:
1. Reconstruct the canary-bearing frame from the real prologue and stack contents; do not equate a bt frame address with RBP.
2. Prove the canary slot address from the disassembly-derived offset.
3. Classify nearby frames as ordinary caller/callee frames versus interrupted-frame, pt_regs or exception-entry state, and exception-handler frames.
4. Apply stack-growth direction only within a proven ordinary call segment; do not carry ordinary overflow causality across an exception boundary.
5. If the suspected source and corrupted slot are separated by an exception-entry boundary, keep local-overflow attribution provisional until frame provenance and active-overlap arithmetic are proven.
6. Evaluate at least these alternative mechanisms before final blame: active overwrite inside the exception path, stack-slot reuse from pre-fault returned frames, stale stack residue, and frame reconstruction error.

### CRITICAL: Stack Growth Direction and Causality Constraint (x86-64)

On x86-64, the kernel stack grows from HIGH addresses toward LOW addresses.
- A frame at a LOWER address was pushed LATER (called more recently).
- A frame at a HIGHER address was pushed EARLIER (called first).
- A local buffer overflow writes UPWARD (toward HIGHER addresses), so it can only corrupt
  its own canary, saved RBP, return address, and frames of EARLIER callers (higher addresses).
- A local buffer overflow CANNOT corrupt frames at LOWER addresses, because those frames
  belong to functions called LATER and did not exist when the overflow occurred.

**Mandatory causality check before attributing corruption source**:
Given canary_frame_addr (address of the frame whose canary is corrupted) and
suspect_frame_addr (address of the suspected overflow source):
- If suspect_frame_addr > canary_frame_addr (suspect frame is at a higher address, i.e.,
  an earlier/outer caller): the suspect's local overflow writes upward and CANNOT reach the
  canary at a lower address. This attribution is PHYSICALLY IMPOSSIBLE. Reject it immediately.
- If suspect_frame_addr < canary_frame_addr (suspect frame is at a lower address, i.e.,
  a later/inner callee): the suspect's local overflow writes upward and CAN reach the canary
  at a higher address. This attribution is physically plausible.
- If suspect_frame_addr == canary_frame_addr: the function corrupted its own canary.

Example of INVALID reasoning:
  "link_path_walk (frame at 0x17c08) overflowed and corrupted the canary of
   search_module_extables (frame at 0x17a10)"
  → WRONG: 0x17c08 > 0x17a10, so link_path_walk's frame is at a higher address (earlier caller).
  Its overflow writes toward even higher addresses and cannot reach 0x17a20.

### CRITICAL: Exception-Path Stack Is Not an Ordinary Call Nest

Do NOT apply ordinary caller/callee stack-overflow arithmetic across an exception boundary
until you have proved the exact provenance of each frame.

For page fault, interrupt, NMI, and similar exception paths, the stack often contains:
- an interrupted ordinary-function frame (for example, zone_statistics),
- hardware-pushed exception state and/or pt_regs,
- then exception-handler frames (for example, handle_mm_fault, search_module_extables).

These are NOT equivalent to a simple uninterrupted nesting like:
caller -> callee -> callee.

Mandatory rule:
- Relative address ordering alone does NOT prove that an interrupted pre-exception frame can
   locally overflow into a post-exception handler frame, or vice versa.
- Before using stack-direction arithmetic for causality, first classify each frame as one of:
   interrupted normal-path frame, exception-entry state/pt_regs region, or exception-handler frame.
- If the candidate source and the corrupted canary are on opposite sides of an exception-entry
   boundary, you MUST mark ordinary local-overflow causality as unproven and avoid direct blame
   based only on "lower address means later call".

Example of INVALID reasoning:
   "zone_statistics faulted, then handle_mm_fault and search_module_extables ran later at lower
    addresses, therefore handle_mm_fault is the likely local overflow source because only later
    frames can overwrite the canary"
   → INSUFFICIENT: this treats the page-fault path as an ordinary contiguous call nest and ignores
   the interrupted-frame / exception-entry / handler segmentation. The frame provenance must be
   established first.

### Recognizing Exception/Interrupt Nested Frames

When a page fault, interrupt, or exception occurs during a function's execution, the
exception handler pushes NEW frames on the SAME kernel stack, extending it toward LOWER
addresses. These nested frames appear in the backtrace BELOW the interrupted function.

Key indicators of nested exception frames:
- Frames prefixed with ? that belong to mm subsystem (handle_mm_fault, __do_page_fault)
  or exception handling appearing below VFS/filesystem frames.
- A function like search_module_extables appearing below inode_permission/link_path_walk
  — this means the page fault handler called search_module_extables DURING link_path_walk's
  execution, not that link_path_walk called it directly.

When analyzing nested exception frames:
- The corruption source must be sought WITHIN the exception handler call chain itself
  (the frames between the interrupted function and the canary-checking function), not in
  the interrupted function's callers.
- Do NOT upgrade an exception-handler frame to "likely overflow source" merely because it sits
   at a lower address than the canary frame or than the interrupted frame. First prove that the
   overwrite mechanism is an active local overwrite rather than reused stack residue, saved-state
   confusion, or a misidentified frame link.
- Treat deep offsets inside exception-path functions only as control-flow location evidence.
   A non-trivial stack allocation such as sub rsp, 0x90, a large offset such as +0xbfd/0xfb0,
   or generic function complexity does NOT by itself make handle_mm_fault or any similar frame
   a likely overflow source.
- Before naming a function as suspect, require at least one of: an overflow-capable local object,
   a concrete copy or write primitive, overlap arithmetic that survives exception-boundary review,
   or stack-byte provenance linking that frame's writes to the corrupted slot.

Analysis:
1. Identify the faulting function from the panic string and disassemble it with dis -rl <function>.
2. Locate the stack canary check point (__stack_chk_fail call site) and determine the canary slot
   from the disassembly (e.g., rbp-0x18 or similar). Compute the canary's absolute stack address.
3. Read the task's kernel stack with rd <stack_base> <size> to examine the raw stack content;
   look for overwritten canary or return address.
4. Before using any rbp-relative address arithmetic, prove which stack word is the actual saved
   caller RBP and which stack word is the saved RIP. Do NOT assume the bt frame address itself
   is RBP.
5. **Perform the mandatory causality check**: list all frames with their addresses, identify
   which frames are at lower addresses (later calls) vs higher addresses (earlier calls) relative
   to the corrupted canary slot. Only frames at LOWER addresses (later calls whose overflow
   writes upward toward the canary) are physically capable of causing the corruption WITHIN the
   same proven stack segment. Do not apply this rule across an unproven exception-entry boundary.
6. Frames prefixed with ? in the backtrace are stack-scan candidates, not reliable frame-pointer
   links; do not treat them as proven callers. However, ? frames from exception handlers
   (e.g., handle_mm_fault, __do_page_fault) are significant because they indicate nested
   execution on the same stack.
7. Compare the backtrace against the disassembly call chain to identify which frames are
   plausible and which are residual from prior calls or exception handler nesting. Explicitly
   annotate where the normal execution path was interrupted and where exception-handler frames begin.
8. For each candidate source function (only those passing the causality check in step 5),
   check for an overflow-capable write mechanism such as a local array, structure copy,
   memcpy-like primitive, negative-index store, alloca/VLA use, or proven direct write into
   the corrupted region. Stack size alone is not a candidate-selection criterion.
9. Distinguish stack buffer overflow (local array overwrite) from external corruption
   (another CPU or DMA corrupting the stack page).
10. If the corrupted function is unrelated to the apparent call chain (e.g., zone_statistics
   calling search_module_extables), suspect stack smearing from earlier activity or exception
   handler nesting. Check whether an exception handler (page fault, interrupt) inserted
   intermediate frames.
11. Use vtop on the kernel stack address to verify the stack page is not shared or aliased.
    **NEVER use kmem -S on kernel stack addresses** — the stack is not a slab allocation;
    kmem -S will always return "address is not allocated in slab subsystem" with zero
    diagnostic value. Use vtop <stack_addr> to verify page ownership instead.

### Active-Frame Overlap Proof

If you claim that a caller's active local object overlaps an active callee canary or local slot,
you must prove the overlap with canonical stack-layout arithmetic:

12. Derive the caller's post-prologue RSP from its prologue exactly: pushes plus local-allocation.
13. Derive the callee's entry stack position from the call site: call pushes the return address at
    caller_rsp-8, and the callee prologue then moves further downward from there.
14. Compute the callee canary/local slot from the callee's own prologue and verify that it lies
    below the caller's call-site RSP as required by x86-64 stack discipline.
15. If an alleged callee canary address lies inside a caller-local range but above the caller's
    call-site RSP, your frame arithmetic is inconsistent. Reject the active-overlap theory and
    re-evaluate whether the address actually belongs to the caller frame, stale residual data, or
    a misidentified saved-frame link.

### Canary Overwrite Value Analysis

When the corrupted canary slot contains a recognizable value (not random garbage), that value
is a critical forensic clue. Pursue it aggressively:

16. If the canary was overwritten with a task_struct pointer (e.g., current task's address from
    bt output), this means the overflow code was likely accessing `current` or `current->field`
    and writing beyond bounds. Next steps:
    a. Run `task <task_addr>` to expand the task_struct and look for fields whose addresses or
       values match the stack corruption pattern.
    b. Identify which kernel functions store `current` (the task pointer) on the stack as a local
       variable. Search for code paths where `current->xxx` access could produce an OOB write
       into adjacent stack slots.
    c. Cross-reference with the call chain: which functions in the active backtrace access
       task_struct fields that could spill onto the stack at the corrupted offset?
    d. Treat a `current`-valued canary as a spill-location clue, not as proof by itself. Inspect
       the disassembly for explicit stack-store instructions that save `current` or a
       current-derived pointer into a local slot to free registers, including both rbp-relative
       and rsp-relative forms (for example, `mov %rax, -0x40(%rbp)` or `mov %rax, 0x20(%rsp)`).
    e. Only escalate this theory after proving slot adjacency: identify the exact saved slot for
       `current`, identify a neighboring overflow-capable local object or write primitive in the
       same frame, and show that the overwrite distance could reach that slot. Merely accessing
       `current` or spilling it somewhere in the frame is insufficient.

17. If the canary was overwritten with a kernel text address, use `sym <value>` and `dis -rl <value>`
    to identify the function. This may reveal a function pointer copy, callback table, or residual
    stack payload, but it is NOT proof that the named function was the overflow source.
    a. First classify the address as a written value: saved return site, copied function pointer,
       callback table entry, or stale residual stack content.
    b. Only after establishing how that code pointer could have been materialized on the stack may
       you use it in writer-provenance reasoning.
    c. Reject any jump from "handle_mm_fault+0xbfd is on the stack" to "handle_mm_fault caused
       the overflow" unless a concrete write path, local object, and slot-overlap proof are shown.

18. If the canary was overwritten with a non-symbol kernel address that appears multiple times
    on the stack, treat it as a corruption fingerprint. Do NOT give up after `sym` fails:
    a. Run `vtop <address>` to verify whether the page exists, is mapped, and its ownership.
    b. Run `kmem -p <translated_PA>` to check the physical page state (slab, anonymous, reserved).
    c. If the page belongs to a specific subsystem or memory pool, that narrows the corruption
       source significantly.
    d. Check whether the value could be a per-CPU pointer, module data address, or vmalloc region
       address by comparing against known address ranges.

### Stack Reuse and Prior-Frame Pollution Mechanism

On a kernel stack, when a function returns, its frame data remains in memory as stale residue
until another function call overwrites it. This creates a "ghost frame" effect:

19. **Identify the stack-reuse timeline**: When an exception (page fault, interrupt) occurs during
    a function's execution, the exception handler pushes new frames onto the same stack. These new
    frames may overlap with stack space previously used by deeper calls that have already returned
    from the normal (pre-exception) call chain.

    Example timeline:
    a. sys_open → do_filp_open → path_openat → link_path_walk calls deep helper functions
       (e.g., walk_component → lookup_slow → various inode ops) that push frames to low addresses.
    b. Those deep helpers return — stack pointer moves back up, but their written data persists
       as stale residue in the low-address region.
    c. Execution continues in the VFS permission or path-walk region, for example
       inode_permission → __inode_permission → security_inode_permission and then into the
       relevant LSM hook path. If a later corrupted bt appears to place an unrelated helper such as
       zone_statistics directly adjacent to security_inode_permission, do NOT treat that edge as
       proven ordinary control flow; first decide whether it is an exception splice, stack-scan
       artifact, or corrupted saved return path.
    d. The page fault handler pushes new frames (handle_mm_fault → search_module_extables)
       into the SAME low-address region that was previously used by the returned deep helpers.
    e. search_module_extables's canary slot now occupies an address that was previously written
       by one of those returned helpers.

20. **Investigate the prior occupants**: To find the corruption source under this mechanism:
    a. Calculate which earlier (now-returned) functions had frames overlapping the canary slot
       address. Use the stack base from `task -R stack` and the frame addresses from `bt` to
       map the used stack range.
    b. Disassemble candidate prior-occupant functions to check whether any of them write
       `current` (task pointer), function pointers, or structure data to local variables at
       offsets that would land on the canary slot.
    c. If a prior-occupant function had an off-by-one or boundary error that wrote past its
       frame into adjacent stack space, the residue would persist and be discoverable when
       the canary slot is reused.

20a. **Analyze the active call chain before exception-path blame**:
    a. If the panic task is still on a coherent syscall path such as sys_open -> do_filp_open ->
       path_openat -> do_last -> link_path_walk -> inode_permission, inspect those active frames
       first with `dis -rl <func>` before promoting an exception handler as the source.
    b. Prioritize functions on the live non-exception path that perform pathname handling,
       structure copies, or substantial local-stack bookkeeping.
    c. A final recommendation that jumps from a stack-resident exception-path address directly to
       fault.c or handle_mm_fault, without first auditing the active VFS/open-path frames, is
       incomplete and must remain non-conclusive.

21. **Residual data pattern matching**: Compare the corrupted canary value and surrounding
    corrupted bytes against known kernel data patterns:
    a. task_struct pointer at canary slot → prior function stored `current` and OOB-wrote it
    b. Repeated non-symbol addresses → structure copy or memcpy overflow from a known object
    c. Contiguous validated string object → only then consider string-copy style overflow; an
       isolated ASCII-decodable 8-byte word is only a weak clue and cannot establish pathname,
       filename, or userspace-string provenance by itself

### Mandatory String-Evidence Gate

If stack bytes look printable, do NOT jump from "ASCII-decodable" to "pathname" or
"filename fragment". Before using a string hypothesis in final diagnosis, verify all of the
following:

22. **String object validation**:
    a. There is a contiguous multi-byte region, not just one 8-byte machine word.
    b. The region has either a NULL terminator, an explicit length field, or a surrounding
       object layout that proves string semantics.
    c. The neighboring bytes are consistent with a real copied string rather than unrelated
       printable residue from pointers, flags, or packed values.

23. **Copy-path validation**:
    a. Identify a plausible code path that could have copied that exact string class onto the
       stack, such as copy_from_user, strncpy, strscpy, getname, or pathname handling.
    b. If no such copy primitive or pathname-handling path is visible in the active or prior
       call chain, keep the string hypothesis low confidence.
    c. If the evidence is only "these 8 bytes decode as ASCII", explicitly reject pathname
       attribution as unproven.

### Mandatory Mechanism Closure For Meaningful Canary Values

If the canary slot contains a meaningful kernel pointer such as current task_struct, a stable
object pointer, or a repeated non-random value, you are NOT allowed to stop at naming that value.
Before final diagnosis, you must explicitly work through this closure checklist:

24. **Pre-fault interrupted-path reconstruction**:
    a. Reconstruct the normal call chain that was executing before the page fault or exception
       handler began.
    b. Identify which deeper functions in that interrupted path had already returned before the
       exception path reused the low-address stack region.
    c. Prefer candidate functions that could plausibly materialize `current`, `current->field`,
       validated string objects, or copied structure fields on the stack.

25. **Mechanism triage**: evaluate all three mechanism families, not just one:
    a. exception-path local overwrite: a function in the exception/page-fault path wrote past its
       own local bounds into the canary slot;
    b. pre-fault residual-stack pollution: an earlier, already-returned function left stale data
       that was later reused by search_module_extables or another exception-path frame;
    c. current-pointer spill/copy overflow: some function stored `current` or `current->xxx` in a
       local stack slot and then copied or wrote beyond that slot.

26. **Conclusive-output gate**:
    a. Do not set is_conclusive=true unless at least one mechanism family above has positive
       supporting evidence from stack bytes, frame layout, disassembly, or call-chain timing.
    b. For the remaining mechanism families, explicitly state whether they are weakened by
       address-direction causality, weakened by stack-layout evidence, or still open because the
       partial dump prevents closure.
    c. A statement such as "canary overwritten with task_struct pointer, therefore likely kernel
       bug in handle_mm_fault" is insufficient and must be treated as non-conclusive.""".strip(),
}
