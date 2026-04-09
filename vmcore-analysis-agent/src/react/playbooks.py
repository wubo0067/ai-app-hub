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
- Pay special attention to ? handle_mm_fault entries with large offsets (e.g., +0xbfd/0xfb0),
  as these indicate deep execution within a complex function that has substantial local
  state and is a prime candidate for the overflow source.

Analysis:
1. Identify the faulting function from the panic string and disassemble it with dis -rl <function>.
2. Locate the stack canary check point (__stack_chk_fail call site) and determine the canary slot
   from the disassembly (e.g., rbp-0x18 or similar). Compute the canary's absolute stack address.
3. Read the task's kernel stack with rd <stack_base> <size> to examine the raw stack content;
   look for overwritten canary or return address.
4. **Perform the mandatory causality check**: list all frames with their addresses, identify
   which frames are at lower addresses (later calls) vs higher addresses (earlier calls) relative
   to the corrupted canary slot. Only frames at LOWER addresses (later calls whose overflow
   writes upward toward the canary) are physically capable of causing the corruption.
5. Frames prefixed with ? in the backtrace are stack-scan candidates, not reliable frame-pointer
   links; do not treat them as proven callers. However, ? frames from exception handlers
   (e.g., handle_mm_fault, __do_page_fault) are significant because they indicate nested
   execution on the same stack.
6. Compare the backtrace against the disassembly call chain to identify which frames are
   plausible and which are residual from prior calls or exception handler nesting.
7. For each candidate source function (only those passing the causality check in step 4),
   check for local array variables that could overflow; inspect their sizes relative to stack
   frame layout and the distance to the corrupted canary slot.
8. Distinguish stack buffer overflow (local array overwrite) from external corruption
   (another CPU or DMA corrupting the stack page).
9. If the corrupted function is unrelated to the apparent call chain (e.g., zone_statistics
   calling search_module_extables), suspect stack smearing from earlier activity or exception
   handler nesting. Check whether an exception handler (page fault, interrupt) inserted
   intermediate frames.
10. Use vtop on the kernel stack address to verify the stack page is not shared or aliased.
""".strip(),
}
