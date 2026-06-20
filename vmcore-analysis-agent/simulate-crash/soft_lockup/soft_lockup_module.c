/*,,,,?,
 * @Author: CALM.WU
 * @Date: 2026-03-17 18:03:16
 * @Last Modified by:   CALM.WU
 * @Last Modified time: 2026-03-17 18:03:16
 */

#define pr_fmt(fmt) "%s:%s(): " fmt, KBUILD_MODNAME, __func__

#include <linux/delay.h>
#include <linux/init.h>
#include <linux/interrupt.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/module.h>
#include <linux/sched.h>
#include <linux/sched/signal.h> // Include for signal_pending

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Calm.Wu");
MODULE_DESCRIPTION("A module to simulate soft lockup for vmcore analysis");
MODULE_VERSION("1.0");

static struct task_struct *soft_lockup_task = NULL;
static int cpu_number = -1;
module_param(cpu_number, int, S_IRUGO);
MODULE_PARM_DESC(cpu_number,
                 "CPU number to run soft lockup on (-1 for any CPU)");

static bool enable_soft_lockup = false;
module_param(enable_soft_lockup, bool, S_IRUGO | S_IWUSR);
MODULE_PARM_DESC(enable_soft_lockup,
                 "Enable soft lockup simulation (default: false)");

// Soft lockup simulation function
// CPU 运行 soft_lockup_kthread
//         │
//         ▼
//   preempt_disable()    ← preempt_count = 1
//         │
//         ▼
//   while (counter++ < 1000000) { barrier(); }
//         │
//         ├── 硬件定时器中断到来 ──→ 中断处理程序执行
//         │                              │
//         │                              ▼
//         │                        中断处理完，返回前检查：
//         │                        preempt_count > 0 ?
//         │                              │
//         │                       ┌──────┴──────┐
//         │                       │  是，不调度   │
//         │                       └──────┬──────┘
//         │                              │
//         │                              ▼
//         │                         直接恢复执行
//         │                         soft_lockup_kthread
//         │
//         ├── 网络中断到来 ──→ 同样处理
//         ├── IPI (核间中断) 到来 ──→ 同样处理
//         │
//         ▼
//   继续忙等...

// CPU N 上的 kthread: preempt_disable() + busy-wait

// 时间 →

// soft_lockup_kthread    watchdog 线程    hrtimer 中断         watch_touch_ts
//       │                    │                 │                    │
//       ├─ preempt_disable() │                 │                    │
//       │  preempt_count=1   │                 │           上次 touch 的旧值 (T0)
//       │                    │                 │
//       │  busy-wait loop    │ 想运行但被      │
//       │  (持有 CPU 不放)     │ preempt_disable│
//       │                    │ 挡住，无法调度   │
//       │                    │                 │
//       │                    │           hrtimer 触发 (T0 + 4s)
//       │                    │           检查：now - touch_ts
//       │                    │                 │
//       │                    │           T0+4s - T0 = 4s
//       │                    │           4s < 40s ✓ 正常
//       │                    │
//       │  ...继续 busy-wait...                 │
//       │                    │                 │
//       │                    │           hrtimer 触发 (T0 + 40s)
//       │                    │           检查：now - touch_ts
//       │                    │                 │
//       │                    │           T0+40s - T0 = 40s
//       │                    │           40s >= 2*threshold ⚠️
//       │                    │                 │
//       │                    │           ╔═══════════════════╗
//       │                    │           ║ BUG: soft lockup! ║
//       │                    │           ╚═══════════════════╝
//       │                    │           dump_stack()
//       │                    │           panic() → vmcore

//                     中断响应能力
//                     │
//         ┌───────────┴───────────┐
//         │                       │
//     可以响应                   无法响应
// (hrtimer 中断正常)        (中断被屏蔽/CPU 完全卡死)
//         │                       │
//         ▼                       ▼
//  watchdog_timer_fn()      perf_event NMI 回调
//  (hrtimer 中断上下文)      (NMI 上下文)
//         │                       │
// 检查：watchdog_touch_ts    检查：hrtimer_interrupts
// 超过 2*threshold?          超过 hardlockup_thresh?
//         │                       │
//     无变化 → soft lockup    无增长 → hard lockup!
//     panic("softlockup")    panic("hardlockup")

static int soft_lockup_simulation(void *data)
{
    // Try to bind to specific CPU if requested
    if (cpu_number >= 0 && cpu_number < num_online_cpus()) {
        if (set_cpus_allowed_ptr(current, cpumask_of(cpu_number))) {
            printk(KERN_WARNING
                   "Soft Lockup Module: Failed to bind to CPU %d, running on any CPU\n",
                   cpu_number);
        } else {
            printk(KERN_INFO
                   "Soft Lockup Module: Successfully bound to CPU %d\n",
                   cpu_number);
        }
    }

    printk(KERN_INFO
           "Soft Lockup Module: Starting soft lockup simulation on CPU %d\n",
           smp_processor_id());

    // Disable preemption to keep this thread running continuously
    while (!kthread_should_stop() && enable_soft_lockup) {
        /*
         * Busy-wait loop to consume CPU cycles without yielding.
         * This will prevent other processes from running on this CPU.
         *
         * Note: In preempt_disable() state, the CPU still responds to hardware
         * interrupts (e.g., timer interrupts, network interrupts, IPIs).
         * When interrupt handlers finish execution, since preemption is disabled,
         * the scheduler will NOT switch to other tasks but directly resume executing
         * the current busy-wait thread.
         */
        preempt_disable(); // Disable preemption to trigger soft lockup but allow interrupts (IPIs)

        // Infinite loop that doesn't yield CPU control
        while (enable_soft_lockup && !kthread_should_stop()) {
            // Perform some dummy computation to keep CPU busy
            volatile unsigned long counter = 0;
            while (counter++ < 1000000) {
                /*
                 * Using barrier() instead of cpu_relax():
                 * - cpu_relax() would reduce CPU load as it allows the CPU to save energy
                 *   or optimize execution during busy-wait. This contradicts our goal of
                 *   simulating soft lockup (keeping CPU continuously busy).
                 *
                 * - barrier() prevents compiler optimization without generating actual
                 *   CPU instructions. It forces the compiler to keep this loop and consume
                 *   CPU cycles. Without it, the compiler might optimize away the entire loop,
                 *   making it impossible to simulate soft lockup.
                 */
                barrier();
            }

            // Check periodically if we should stop
            if (signal_pending(current))
                break;
        }

        preempt_enable(); // Re-enable preemption
    }

    printk(KERN_INFO "Soft Lockup Module: Stopping soft lockup simulation\n");
    return 0;
}

static int __init soft_lockup_init(void)
{
    printk(KERN_INFO
           "Soft Lockup Module: Initializing soft lockup simulation module\n");

    if (!enable_soft_lockup) {
        printk(KERN_WARNING
               "Soft Lockup Module: Soft lockup disabled by default. Set "
               "enable_soft_lockup=1 to activate.\n");
        return 0;
    }

    // Create a kernel thread for soft lockup simulation
    soft_lockup_task =
            kthread_run(soft_lockup_simulation, NULL, "soft_lockup_kthread");
    if (IS_ERR(soft_lockup_task)) {
        printk(KERN_ERR "Soft Lockup Module: Failed to create kernel thread\n");
        return PTR_ERR(soft_lockup_task);
    }

    printk(KERN_INFO
           "Soft Lockup Module: Loaded successfully. Soft lockup simulation "
           "started.\n");
    return 0;
}

static void __exit soft_lockup_exit(void)
{
    enable_soft_lockup = false; // Stop the soft lockup

    if (soft_lockup_task) {
        // Wait for the thread to finish
        kthread_stop(soft_lockup_task);
        soft_lockup_task = NULL;
    }

    printk(KERN_INFO "Soft Lockup Module: Unloaded successfully\n");
}

module_init(soft_lockup_init);
module_exit(soft_lockup_exit);