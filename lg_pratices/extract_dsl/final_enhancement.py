import os
import json
from typing import List, Dict, Any


def remove_duplicates(matrix: List[Dict]) -> List[Dict]:
    """移除重复的诊断分支"""
    seen_triggers = set()
    unique_matrix = []

    for branch in matrix:
        trigger = branch.get("trigger", "")
        if trigger not in seen_triggers:
            seen_triggers.add(trigger)
            unique_matrix.append(branch)
        else:
            print(f"移除重复: {trigger[:50]}...")

    return unique_matrix


def enhance_with_new_scenarios():
    """基于新增文件增强知识库"""

    # 读取现有的目标文件
    target_file = "dsl/diagnostic_knowledge_library.json"

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            existing_kb = json.load(f)

        print(f"原始知识库:")
        print(f"总结: {existing_kb.get('summary', '')[:100]}...")
        print(f"初始命令数: {len(existing_kb.get('init_cmds', []))}")
        print(f"诊断分支数: {len(existing_kb.get('matrix', []))}")

        # 移除重复项
        original_matrix = existing_kb.get("matrix", [])
        unique_matrix = remove_duplicates(original_matrix)

        print(f"去重后分支数: {len(unique_matrix)}")

        # 新增的诊断分支（基于新增文件的分析）
        new_branches = [
            {
                "trigger": "WARNING: CPU: {N} PID: {PID} at include/linux/kref.h:52 fl2000_device_probe+0x304/0x3e0 [fl2000]",
                "action": "px ((struct scsi_cmnd *){addr})->device",
                "arg_hints": "addr: scsi_cmnd address from context",
                "why": "Examine SCSI device associated with timed-out command",
                "expect": "SCSI device pointer",
                "is_end": False,
            },
            {
                "trigger": "SCSI command older than its timeout",
                "action": "scsishow --check",
                "arg_hints": None,
                "why": "Check for SCSI command issues",
                "expect": "WARNING about EH or stalled queue",
                "is_end": False,
            },
            {
                "trigger": "Spinlock val.counter = {value} (not typical pattern)",
                "action": "eval -b {value}",
                "arg_hints": "value: spinlock counter value from context",
                "why": "Analyze spinlock value bitwise for unusual patterns",
                "expect": "Binary representation showing unusual bit pattern",
                "is_end": False,
            },
            {
                "trigger": "rcu_sched detected stalls on CPUs/tasks: {N}",
                "action": "bt -c {N}",
                "arg_hints": "N: CPU number from stall message",
                "why": "Get backtrace for stalled CPU to identify blocking task",
                "expect": "vsapiapp stuck at kvm_lock_spinning and ioctlMod",
                "is_end": False,
            },
            {
                "trigger": "Tasks stuck in TASK_UNINTERRUPTIBLE state for >120 seconds",
                "action": "ps -m|grep UN",
                "arg_hints": None,
                "why": "List top UNINTERRUPTIBLE state tasks to identify hung processes",
                "expect": "Multiple tasks in UN state for >2 minutes",
                "is_end": False,
            },
            {
                "trigger": "vsapiapp stuck at kvm_lock_spinning",
                "action": "p klock_waiting:{cpu}",
                "arg_hints": "cpu: CPU where vsapiapp is running",
                "why": "Get klock_waiting structure for specific CPU to check lock status",
                "expect": "lock address and want value showing contention",
                "is_end": False,
            },
            {
                "trigger": "kthreadd stuck in schedule",
                "action": "runq -c {cpu}",
                "arg_hints": "cpu: CPU where kthreadd should run",
                "why": "Check runqueue for CPU to see what's blocking kthreadd",
                "expect": "vsapiapp currently running on CPU, preventing kthreadd from executing",
                "is_end": False,
            },
            {
                "trigger": "Tasks waiting for page writeback completion",
                "action": "kmem {page_addr}",
                "arg_hints": "page_addr: from wait_bit_queue key flags",
                "why": "Examine memory page for writeback status to identify I/O stalls",
                "expect": "Page has writeback flag set (PG_writeback = 13)",
                "is_end": False,
            },
            {
                "trigger": "sys_close replaced with closeHook",
                "action": "sys -c | head",
                "arg_hints": None,
                "why": "Check system call table for modifications by third-party modules",
                "expect": "sys_close replaced with closeHook at syscall 3 (splxmod module)",
                "is_end": False,
            },
            {
                "trigger": "kworker waiting at wait_for_completion in kthread_create_on_node",
                "action": "p kthreadd_task",
                "arg_hints": None,
                "why": "Get kthreadd_task pointer to check kthreadd status",
                "expect": "kthreadd_task at specific address, showing kthreadd is blocked",
                "is_end": False,
            },
            {
                "trigger": "Host lock spinlock contention in SCSI subsystem",
                "action": "px ((struct Scsi_Host *){addr})->host_lock",
                "arg_hints": "addr: Scsi_Host address from context",
                "why": "Examine host lock spinlock for SCSI host contention",
                "expect": "host_lock address showing spinlock location",
                "is_end": False,
            },
            {
                "trigger": "Completion wait lock spinlock with unusual value",
                "action": "px ((struct us_data *)((struct scsi_cmnd *){addr})->device->host->hostdata)->notify->wait.lock",
                "arg_hints": "addr: scsi_cmnd address from context",
                "why": "Check completion wait lock spinlock value for unusual patterns",
                "expect": "Spinlock val.counter with non-standard value (e.g., 0x218928)",
                "is_end": False,
            },
            {
                "trigger": "MCS spinlock structure analysis needed",
                "action": "mcs_spinlock {addr}",
                "arg_hints": "addr: MCS spinlock node address from backtrace",
                "why": "Examine MCS spinlock structure for queue-based locking issues",
                "expect": "struct mcs_spinlock with next, locked, and count values",
                "is_end": False,
            },
        ]

        # 合并所有分支
        all_branches = unique_matrix + new_branches

        # 创建最终知识库
        final_kb = {
            "summary": "Comprehensive diagnostic matrix for Linux kernel lockup scenarios covering hard/soft lockups, timer discrepancies, spinlock deadlocks, race conditions, memory pressure, hardware errors, third-party module issues (fl2000, splxmod), page writeback stalls, kvm_lock_spinning, SCSI timeouts, and MCS spinlock contention.",
            "init_cmds": existing_kb.get("init_cmds", []),
            "matrix": all_branches,
        }

        # 保存最终版本
        output_file = "dsl/final_diagnostic_knowledge_library.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(final_kb, f, indent=2, ensure_ascii=False)

        print(f"\n增强完成!")
        print(f"原始分支数: {len(original_matrix)}")
        print(f"去重后分支数: {len(unique_matrix)}")
        print(f"新增分支数: {len(new_branches)}")
        print(f"总分支数: {len(all_branches)}")
        print(f"保存到: {output_file}")

        # 显示新增场景
        print(f"\n新增场景覆盖:")
        print("1. fl2000第三方模块数据竞争")
        print("2. SCSI命令超时和主机锁竞争")
        print("3. page writeback完成阻塞")
        print("4. vsapiapp阻塞kthreadd的kvm_lock_spinning")
        print("5. splxmod模块系统调用替换")
        print("6. MCS自旋锁队列分析")

        return final_kb

    except Exception as e:
        print(f"增强失败: {e}")
        import traceback

        traceback.print_exc()
        return None


if __name__ == "__main__":
    enhance_with_new_scenarios()
