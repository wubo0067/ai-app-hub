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

Use this guide as part of the S1-S5 exclusion reasoning required before promoting DMA or hardware. In this file, S1 mainly covers instruction-level provenance closure, S2 covers ordinary object-state validation, S3 covers snapshot or unwind artifact exclusion, and S4 covers stronger software corruption sources.

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
- Before considering DMA or hardware, explicitly close S1-S5 exclusion reasoning. At minimum in this playbook: close register provenance first (S1), object lifetime and ordinary object-state validation next (S2), stack or snapshot artifact exclusion when relevant (S3), local corruption exclusion after that (S4), and only then assess whether any affirmative device-side evidence exists (S5).
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
    "invalid_address_access": """
## 3.11 Invalid Address Access (Canonical but Garbage-Valued Fault Address)
Pattern: unable to handle kernel paging request at <address> where the fault address is
non-zero, non-trivial, and does not match any expected kernel object layout; the address
appears random, offset-shifted, or clearly outside any kernel-mapped region, yet is
architecturally canonical.

Triage — classify the address BEFORE proceeding:
- If the address is TRULY NON-CANONICAL (bits 63:48 are not all 0 or all 1 on x86-64):
  this is a General Protection Fault (#GP, x86 vector 13). Route to general_protection_fault.
- If the address is near zero (< 0x1000 or a small struct-member offset from zero):
  route to null_deref.
- If the address is in the vmalloc/vmap range but the PTE is absent: route to page_not_present.
- Otherwise (canonical, non-null, non-vmalloc, PTE-absent, garbage-looking): use this playbook.

Analysis:
1. Confirm the address is canonical: verify that bits 63:48 sign-extend bit 47.
   If not canonical, stop — this is a #GP; switch to general_protection_fault playbook.
2. Use sym <RIP> and dis -rl <RIP> to identify the faulting instruction and the register
   holding the bad address.
3. Identify the producer: which register carries the bad value, and where was it last
   loaded? Trace back through the register chain — was this a memory load, the result of
   arithmetic, a function return value, or a struct-member dereference?
4. Evaluate likely root causes in order:
   a. Pointer overflow / underflow: iterating a pointer past the end of an allocation, or
      subtracting past the start. The bad address will be a small or large delta from a
      valid kernel symbol or slab address.
   b. Struct member offset error: wrong struct type cast, incorrect field offset constant,
      or API mismatch producing an address that is valid-looking except for a small shift.
   c. Bit flip (hardware): a single-bit change in a valid kernel pointer produces an address
      in an unmapped region. Look for corroborating MCE or EDAC evidence.
   d. Stale/wild pointer from UAF: the object holding the pointer was freed and its memory
      reclaimed; the stored pointer now refers to reclaimed or remapped address space.
5. Run vtop <fault_addr> to confirm the page is absent.  If vtop succeeds and a valid
   page is found, the access may be a protection fault — re-evaluate the Oops error code.
6. If the bad address has a recognizable bit pattern (e.g., a valid address shifted by one
   byte, a bus address stored in a kernel virtual pointer slot, or a value matching a known
   struct field), treat that pattern as a corruption fingerprint and trace it aggressively.
7. For unaligned-access symptoms on non-x86 architectures: verify whether the fault is an
   alignment exception. On x86, unaligned data accesses are handled in hardware; if an
   alignment-related path is reported, the address itself is more likely wrong, not merely
   misaligned.
""".strip(),
    "write_protection_violation": """
## 3.12 Write Protection Violation (Read-Only Page Fault)
Pattern: BUG: unable to handle kernel paging request at <address>; Oops error code with
bit 0 (protection fault, page IS present) and bit 1 (write access) both set — e.g.,
Oops: 0003; or "kernel BUG at ... write to read-only memory"; or dmesg explicitly states
"Write protect fault" or "kernel attempted to write to read-only page".

Oops error code quick decode (relevant bits):
  bit 0 = 1 → protection fault (page present, access denied)
  bit 1 = 1 → write fault
  bit 4 = 1 → instruction-fetch fault (SMEP) → use smap_smep_violation instead

Analysis:
1. Confirm the error code: bits 0 and 1 both set, bit 4 clear.
   If bit 4 is also set, this is a SMEP violation — route to smap_smep_violation.
2. Identify the fault address. Use sym <fault_addr> to classify the write target:
   a. Kernel .text section → kernel code segment is being modified. Highest-priority
      security concern; treat as potential exploit (inline hook, code patch, ret2dir).
   b. .rodata or __ro_after_init data → attempt to modify data sealed after init;
      likely a driver lifecycle bug or post-init write to a configuration constant.
   c. Module .text or .rodata → faulty module self-modifying its own code or constants.
   d. Kernel page table or credential struct pages → memory corruption reaching
      write-protected structural pages; may indicate heap OOB or DMA overwrite.
3. Use dis -rl <RIP> to locate the exact store instruction that triggered the fault.
4. Trace the write source: identify the object being written and the value being stored.
   Determine whether this is a direct store, a memcpy, a copy_from_user overrun, or an
   indirect write through a corrupted function pointer or struct field.
5. Assess the write mechanism:
   a. Direct store to a known kernel text or RO symbol from a driver path → driver bug
      directly writing a constant or code address (e.g., self-patching, miscounted offset).
   b. memcpy or bulk copy overrunning into a read-only region adjacent to a writable one →
      OOB write; investigate source buffer bounds and copy length.
   c. Attempt to modify __ro_after_init config data long after init completed →
      driver lifecycle bug; check whether the init vs. runtime paths are correctly split.
   d. Corrupted pointer directing a store to a protected region → route the pointer
      provenance investigation to pointer_corruption or use_after_free as appropriate.
6. Cross-check dmesg for preceding events: module loads, setuid/setcap syscalls, mprotect
   calls on kernel memory, or prior BUG/WARN lines indicating pre-existing corruption.
7. If exploit activity is suspected (write targeting function pointer tables, system call
   table, IDT, or credential structures), record all register values and the full backtrace
   as forensic evidence before drawing conclusions.
""".strip(),
    "page_not_present": """
## 3.13 Page Not Present (Missing Mapping — VMalloc / VMap / Module Space)
Pattern: unable to handle kernel paging request at <address>; Oops error code 0x0000
(neither protection fault nor write fault — the page is simply absent); fault address is
in the vmalloc region, the module address range, a driver vmap area, or another
dynamically-mapped kernel virtual range — not the slab/kmalloc heap and not near NULL.

Key distinction from use_after_free (slab):
  use_after_free  → physical page is typically still present; kmem -S finds a freed slab
                    object; poison markers (0x6b6b...) are visible in the slab data.
  page_not_present → vtop <fault_addr> FAILS or returns an invalid/absent PTE;
                    the physical page backing that virtual address does not exist.

Analysis:
1. Run vtop <fault_addr> immediately to confirm the PTE is absent or invalid.
   If vtop succeeds and the page IS present, re-evaluate: this may be a use_after_free
   with slab reuse, or a protection fault with wrong error code interpretation.
2. Identify the address range of the fault address:
   a. vmalloc range (typically 0xffffc90000000000–0xffffe8ffffffffff on x86-64):
      likely vmalloc UAF — the region was vfree'd while a stale pointer remained live.
   b. Module text/data range (0xffffffffc0000000–0xffffffffff000000):
      module was unloaded (or partially initialized) while still referenced; check whether
      module_put or module unload raced with in-flight callers.
   c. Driver vmap or ioremap region: driver unmapped its own I/O or buffer window while a
      pending interrupt, DMA completion, or work-queue entry still held a reference.
3. Check dmesg for vfree, free_vm_area, vunmap, iounmap, module_put, or __free_pages
   activity immediately before the crash. Correlate freed addresses with the fault address.
4. Inspect the call stack for the access path: is the stale reference being dereferenced
   from an interrupt handler, a work-queue callback, a timer, or an RCU callback that
   outlived the allocation?
5. For vmalloc UAF: identify (a) the vmalloc/vmap call that allocated the region, and
   (b) the vfree/vunmap call that freed it. Determine which reference — pointer stored in
   a struct, argument passed to a callback, cached in a per-CPU variable — was not cleared
   before the free.
6. For module-range faults: use mod -s <module> to inspect module state. Verify whether
   the module is in MODULE_STATE_UNFORMED or MODULE_STATE_GOING at crash time.
7. Distinguish from vmalloc_fault (normal TLB sync): the kernel vmalloc_fault handler
   propagates page-table entries from init_mm for legitimate vmalloc mappings. If
   vmalloc_fault appears in the call stack and the crash still occurred, the mapping truly
   no longer exists — this confirms the vmalloc region was freed before the access.
8. If the fault is in a DMA-mapped region, consider whether the DMA mapping was torn down
   (dma_unmap_*) while device-side DMA was still in flight. Check IOMMU fault logs.
""".strip(),
    "smap_smep_violation": """
## 3.14 SMAP / SMEP Violation (Privilege Boundary Fault)
Pattern: unable to handle kernel paging request at <user-space address>; fault address is
in user-space range (typically < 0x00007fffffffffff); Oops error code with bit 2 (user-mode
page accessed from CPL=0) set, and/or bit 4 (instruction-fetch fault) set.

Triage — distinguish SMEP from SMAP before proceeding:
  SMEP violation: kernel attempted to EXECUTE a user-space page.
    Oops error code bit 4 (instruction-fetch) is set (e.g., 0x0011, 0x0015).
    RIP is at a valid kernel address; the faulting instruction is an indirect call/jmp
    that resolved to a user-space target.
  SMAP violation: kernel accessed (read/write) a user-space DATA page without stac/clac.
    Oops error code bit 2 set, bit 4 clear (e.g., 0x0004, 0x0005, 0x0006, 0x0007).
    The fault address is a user-space data address; RIP is inside kernel code.

Analysis:
1. Confirm the fault address is in user-space range (below the user-kernel split,
   typically < 0x00007fffffffffff on x86-64). If the fault address is in kernel range,
   this is not a SMAP/SMEP violation — re-evaluate using a different playbook.
2. Decode the Oops error code to classify the violation (SMEP vs. SMAP) as above.
3. Use sym <RIP> and dis -rl <RIP> to identify the exact instruction that faulted.
   - SMEP: locate the indirect call or jmp in kernel code whose target resolved to the
     user-space address. Inspect the function pointer register and its source object.
   - SMAP: locate the memory load or store accessing the user-space address. Verify whether
     it falls inside a copy_from_user / copy_to_user / get_user / put_user guard. These
     wrappers temporarily re-enable user-space access via stac/clac; a plain dereference
     outside them triggers SMAP.

4. For SMEP violations:
   a. This is a strong indicator of kernel exploit activity (ret2usr, JOP/ROP pivoting to
      user-space shellcode, function-pointer overwrite).
   b. Identify which kernel struct or callback table contains the corrupted function pointer
      that resolved to the user address. Trace the pointer provenance using the register
      chain and the object at which the indirect call/jmp was issued.
   c. If the function pointer was corrupted by a prior memory error, route the corruption
      source investigation to pointer_corruption or use_after_free as appropriate.
   d. If no corruption source is found and the pointer appears to have been deliberately
      set to a user-space address, treat as an active exploit attempt and document all
      register values and the full backtrace.

5. For SMAP violations:
   a. Check whether the kernel code path has a legitimate reason to access user-space
      memory at that point. If yes, verify whether copy_from_user / get_user is missing
      and a __user-annotated pointer is being dereferenced directly instead.
   b. Common legitimate-bug pattern: an ioctl handler, read/write callback, or mmap fault
      handler receives a user-supplied pointer and dereferences it without the proper guard.
      Inspect the call chain for such patterns in the active frames.
   c. If no direct user-pointer dereference is found in the active call chain, consider
      whether a kernel object was corrupted (UAF, OOB write) to contain a user-space
      address that the kernel then inadvertently dereferenced.
   d. Check for missing access_ok() validation upstream: a user address that was never
      range-checked may pass into a kernel subsystem that expects it to have been
      validated earlier in the call chain.

6. Assess security context:
   a. SMEP violations in production kernels are nearly always exploit-related. Treat as
      highest-priority and preserve all forensic evidence.
   b. SMAP violations may be legitimate driver bugs (missing copy_from_user wrapper) or
      exploit attempts (corrupted data structures directing the kernel toward user memory).
      Pursue both angles until one is definitively ruled out.
7. Check dmesg for preceding suspicious events: unusual setuid / setreuid / setcap syscalls,
   mmap calls by the crash task mapping executable user pages, or prior BUG/WARN/GPF lines.
""".strip(),
    "stack_protector_canary": """
## Stack Protector / Canary Failure
Pattern: stack-protector: Kernel stack is corrupted in: <function>, or active frame is __stack_chk_fail.

### Mandatory Fast Path

Your first actions MUST be:
1. Call `resolve_stack_canary_slot <function>` as the PRIMARY path to derive the
   __stack_chk_fail frame-pointer chain, canary-bearing RBP, canary slot address, and live
   gs:0x28 comparison.
2. Only if that tool is unavailable or returns unproven, fall back to manual disassembly and
   frame-pointer arithmetic.
3. After the canary slot has been closed, audit only the allowed mechanism families listed below.

Do NOT begin with phantom-frame hunting, prior-occupant reconstruction, or interrupted-path
story building. Those are conditional fallback investigations, not the primary path.

### ONLY Allowed Mechanism Families

You MUST keep the candidate set restricted to:
1. Self-frame local overflow in the canary-bearing function.
2. Active callee upward overwrite from a lower-address active callee.
3. Active exception-path overwrite by code executing during the same canary-bearing window.

### Conditional Provenance Fallback

Only after the canary slot has been closed by `resolve_stack_canary_slot` (or a proven manual
fallback) may you perform limited frame
provenance checks, and only for one of these reasons:
1. bt contains a statically impossible caller-callee edge.
2. duplicated saved RIPs remain unexplained after canary-slot closure.
3. you are now explaining corruption of a non-canary slot rather than the canary itself.

When one of those conditions is met, call `classify_saved_rip_frames_tool` before attempting
manual phantom-frame or saved-RIP classification.

If none of the allowed mechanism families has positive evidence, the conclusion MUST remain
indeterminate and no specific function may be named as the overflow source.

---

### Appendix: Guardrails and Invariants (Reference)

**⛔ COMPILER-LEVEL CANARY INVARIANT**: The stack protector prologue unconditionally writes
the canary at function entry (`mov %gs:0x28,%rax; mov %rax,<slot>(%rbp)`). Residual stack
data, pre-fault stack reuse, and stale task pointers CANNOT explain __stack_chk_fail. Only
a write DURING the function's execution (after prologue, before epilogue) can corrupt the canary.

**Forbidden canary mechanisms**: residual stack pollution, stale data from prior function
calls, prior-frame reuse, generic stack smearing, a mere recognizable value found on the stack.

**Context pruning**: IGNORE generic residual-stack, prior-occupant, ghost-frame, or
stack-smearing narratives unless you have already proved that a NON-CANARY target (saved RIP,
saved RBP, or other local slot) was corrupted. For the canary slot, the ONLY admissible time
window is AFTER the prologue store and BEFORE the epilogue check.

**Blame guardrails**: Do not blame link_path_walk, zone_statistics, handle_mm_fault, or any interrupted-path frame merely because its address appears on the stack. Do not identify the canary slot by scanning for a recognizable value and reverse-justifying the address. Do not use stack direction across an exception boundary unless frame provenance and active overlap have been explicitly proven.
""".strip(),
    "stack_corruption": """
## Stack Corruption (Generic — Non-Canary)
Pattern: Kernel stack overflow, stack smashing detected, or frame-pointer corruption where the
panic did NOT contain stack-protector or __stack_chk_fail.

### ⛔ FIRST ACTION RULE

Your VERY FIRST analysis actions (before any hypothesis about overflow sources) MUST be:
1. Execute Phase 1 of the Stack Frame Forensics SOP (3.8a): frame-by-frame saved-RIP and
   saved-RBP validation.
2. Produce the Phase 1 Required Output identifying the first phantom frame.
3. Then proceed to Phase 2 (overflow window reconstruction) and Phase 3 (blame triage).

You are FORBIDDEN from naming a specific overflow source or disassembling suspected callers
until Phase 1–3 of the SOP are complete with their required outputs.

### Mandatory Stack Corruption Analysis Checklist

Before naming a local overflow source, complete this checklist in order:
1. Validate the frame-pointer chain: confirm each saved RBP is a stack-range address and each
   saved RIP is a valid kernel text address.
2. Identify the first phantom frame (corrupted saved RIP or saved RBP): this is the primary
   forensic evidence for where corruption entered the call chain.
3. Classify nearby frames as ordinary caller/callee frames versus interrupted-frame, pt_regs or exception-entry state, and exception-handler frames.
4. Apply stack-growth direction only within a proven ordinary call segment; do not carry ordinary overflow causality across an exception boundary.
5. If the suspected source and corrupted slot are separated by an exception-entry boundary, keep local-overflow attribution provisional until frame provenance and active-overlap arithmetic are proven.
6. Evaluate at least these alternative mechanisms before final blame: self-frame local overflow
   (canary-bearing function's own code or unprotected leaf callees), active overwrite inside the
   exception path, stack-slot reuse from pre-fault returned frames (valid for saved RBP/RIP/locals
   but NOT for the canary slot — see CANARY INVARIANT above), and frame reconstruction error.

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

**Self-frame priority rule**:
- If suspect_frame_addr == canary_frame_addr, prioritize self-frame overflow, inline expansion,
   or unprotected leaf-callee overwrite before investigating any other frame.
- Do not pivot to interrupted-path or unrelated caller frames until you can explain why the
   canary-bearing frame itself is not the primary suspect.

Example of INVALID reasoning:
  "link_path_walk (frame at 0x17c08) overflowed and corrupted the canary of
   search_module_extables (frame at 0x17a10)"
  → WRONG: 0x17c08 > 0x17a10, so link_path_walk's frame is at a higher address (earlier caller).
  Its overflow writes toward even higher addresses and cannot reach 0x17a20.

### ⛔ MANDATORY PRE-CONCLUSION GATE (stack_protector_canary)

You MUST execute this gate explicitly and in writing BEFORE stating any final diagnosis or
naming any overflow source. Failure to complete this gate makes the conclusion invalid.

**Gate Checklist** — fill in ALL items:

```
Canary-bearing function    : <name>
Canary frame address       : 0x<addr>          ← from bt output
Canary slot address        : 0x<addr>          ← RBP - <offset> from disassembly

For each candidate function you intend to blame:
  Candidate                : <name>
  Candidate frame address  : 0x<addr>          ← from bt output
  Comparison               : candidate (0x<X>) vs canary_slot (0x<Y>)
  candidate > canary_slot? : YES → ❌ EXCLUDED (PHYSICALLY IMPOSSIBLE, reject immediately)
                             NO  → ✅ Plausible, continue investigation
```

**Gate Rule**: If candidate frame address > canary frame address → the candidate is an earlier
(outer) caller. Its local overflow writes toward higher addresses. It CANNOT reach the canary at
a lower address. You MUST mark this candidate as EXCLUDED and MUST NOT name it as the overflow
source in the final diagnosis.

**If all candidates are excluded**: the conclusion MUST state the corruption source as
indeterminate. Name only the remaining allowed mechanism families (self-frame or callee) as
the investigation direction. Do NOT default to the most recently disassembled function.

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
- Treat the canary-bearing function's own frame as the primary investigation target first.
- The exception-handler call chain is additional provenance context, not a default cross-frame
   overflow source.
- Do not pivot to unrelated interrupted-path callers unless you can prove a concrete write
   primitive or proven overlap into the canary-bearing function's slot.
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

⛔ **CANARY INVARIANT REMINDER**: The following discussion of residual stack data applies to
corruption of saved RBP, saved RIP, and non-canary local variables. It does NOT apply to canary
corruption. The stack protector prologue unconditionally writes the canary at function entry,
overwriting any residual data. For __stack_chk_fail cases, the canary was corrupted DURING the
function's execution, not before it.

On a kernel stack, when a function returns, its frame data remains in memory as stale residue
until another function call overwrites it. This creates a "ghost frame" effect that can corrupt
non-canary frame data:

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
    e. search_module_extables's saved RBP, saved RIP, or non-canary local variables may now
       occupy addresses that were previously written by those returned helpers. However, the
       CANARY SLOT is immune to this effect because the prologue unconditionally overwrites it.
       Residual data can explain corrupted frame links or spurious unwinder output, but it
       CANNOT explain __stack_chk_fail.

20. **Investigate the prior occupants**: To find non-canary corruption under this mechanism:
    a. Calculate which earlier (now-returned) functions had frames overlapping the affected
       address. Use the stack base from `task -R stack` and the frame addresses from `bt` to
       map the used stack range.
    b. Disassemble candidate prior-occupant functions to check whether any of them write
       `current` (task pointer), function pointers, or structure data to local variables at
       offsets that would land on the corrupted slot.
    c. If a prior-occupant function had an off-by-one or boundary error that wrote past its
       frame into adjacent stack space, the residue would persist and be discoverable when
       the slot is reused.
    d. ⛔ Do NOT use this mechanism to explain canary corruption. The canary prologue
       overwrites any residual data. For __stack_chk_fail cases, focus on self-frame overflow
       (mechanism 25a) instead.

20a. **Analyze the active call chain before exception-path blame**:
    a. If the panic task is still on a coherent syscall path such as sys_open -> do_filp_open ->
       path_openat -> do_last -> link_path_walk -> inode_permission, inspect those active frames
       first with `dis -rl <func>` before promoting an exception handler as the source.
    b. Prioritize functions on the live non-exception path that perform pathname handling,
       structure copies, or substantial local-stack bookkeeping.
    c. A final recommendation that jumps from a stack-resident exception-path address directly to
       fault.c or handle_mm_fault, without first auditing the active VFS/open-path frames, is
       incomplete and must remain non-conclusive.

21. **Residual data pattern matching**: Compare corrupted stack data and surrounding bytes
    against known kernel data patterns. For NON-CANARY slots (saved RBP, saved RIP, locals),
    residual data from prior functions is a valid corruption source. For the CANARY SLOT,
    residual data is irrelevant — the prologue overwrites it; only in-execution writes matter.
    a. task_struct pointer at canary slot → the canary-bearing function (or its active callee)
       accessed `current` and an OOB write during execution spilled it into the canary slot;
       NOT residual data from a prior function
    b. task_struct pointer at non-canary slot → prior function may have stored `current` as
       residual, or active function spilled it
    c. Repeated non-symbol addresses → structure copy or memcpy overflow from a known object
    d. Contiguous validated string object → only then consider string-copy style overflow; an
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

25. **Mechanism triage**: evaluate the following mechanism families, not just one:
    a. self-frame local overflow: the canary-bearing function itself (or an inlined/unprotected
       leaf callee) performed an out-of-bounds write that reached the canary slot. THIS IS THE
       DEFAULT AND MOST COMMON MECHANISM and must be evaluated FIRST;
    b. exception-path local overwrite: a function in the exception/page-fault path wrote past its
       own local bounds into the canary slot;
    c. current-pointer spill/copy overflow: some function stored `current` or `current->xxx` in a
       local stack slot and then copied or wrote beyond that slot;
    d. ⛔ Do NOT list "pre-fault residual-stack pollution" as a canary corruption mechanism. The
       prologue unconditionally writes the canary, overwriting any residual data. Residual data
       can only corrupt saved RBP, saved RIP, or non-canary locals.

26. **Conclusive-output gate**:
    a. Do not set is_conclusive=true unless at least one mechanism family above has positive
       supporting evidence from stack bytes, frame layout, disassembly, or call-chain timing.
    b. For the remaining mechanism families, explicitly state whether they are weakened by
       address-direction causality, weakened by stack-layout evidence, or still open because the
       partial dump prevents closure.
    c. A statement such as "canary overwritten with task_struct pointer, therefore likely kernel
       bug in handle_mm_fault" is insufficient and must be treated as non-conclusive.""".strip(),
}
