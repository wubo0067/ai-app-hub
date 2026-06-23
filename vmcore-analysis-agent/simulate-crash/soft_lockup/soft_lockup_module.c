/*,,,,?,,,
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

static atomic_t enable_soft_lockup = ATOMIC_INIT(0);
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

/*
 * soft_lockup_simulation - 内核线程主函数，用于模拟 soft lockup 场景
 *
 * @data: kthread_run 创建线程时传入的私有数据，本场景未使用（传 NULL）
 *
 * 工作原理：
 *   1. 将当前线程绑定到指定 CPU（若 cpu_number 有效），确保 lockup 发生在目标核上
 *   2. 主循环中检查 enable_soft_lockup 开关：
 *      - 关闭时调用 schedule() 主动让出 CPU，线程处于可调度状态，不会触发 lockup
 *      - 开启时进入"禁抢占 + 忙等"阶段，模拟 soft lockup
 *   3. soft lockup 模拟阶段：
 *      - preempt_disable() 使 preempt_count > 0，禁止内核抢占
 *      - 内层 while 循环持续忙等（barrier() 防止编译器优化掉空循环）
 *      - 此时硬件中断仍可响应，但调度器无法抢占该 CPU
 *      - watchdog 机制（hrtimer 中断）检测到该 CPU 上任务长时间未调度，
 *        超过 2 * watchdog_thresh（默认 40s）即判定为 soft lockup 并 panic
 *   4. 当 enable_soft_lockup 被关闭或收到 kthread_stop() 请求时退出忙等，
 *      preempt_enable() 恢复抢占，线程正常结束
 *
 * 返回值：固定返回 0
 */
static int soft_lockup_simulation(void *data)
{
    /* 若指定了有效的 CPU 编号，则将当前线程绑定到该 CPU，
     * 使 soft lockup 精确发生在目标核上，便于后续 vmcore 分析定位 */
    if (cpu_number >= 0 && cpu_number < num_online_cpus()) {
        if (set_cpus_allowed_ptr(current, cpumask_of(cpu_number)))
            pr_warn("Failed to bind to CPU %d\n", cpu_number);
        else
            pr_info("Bound to CPU %d\n", cpu_number);
    }

    /* 打印线程实际运行的 CPU，注意 smp_processor_id() 必须在禁止迁移后调用才有意义 */
    pr_info("Starting on CPU %d\n", smp_processor_id());

    /* 主循环：直到收到 kthread_stop() 信号才退出 */
    while (!kthread_should_stop()) {
        /* 开关未开启时，主动让出 CPU 进入睡眠，避免空转浪费资源 */
        if (!atomic_read(&enable_soft_lockup)) {
            schedule();
            continue;
        }

        /* === soft lockup 模拟阶段开始 ===
         * preempt_disable() 使 preempt_count 加 1，禁止抢占。
         * 此后即使有更高优先级任务就绪，调度器也无法抢占当前 CPU，
         * 这是触发 soft lockup 的关键条件 */
        preempt_disable();

        /* 在禁抢占状态下持续忙等，直到开关关闭或要求线程停止 */
        while (atomic_read(&enable_soft_lockup) && !kthread_should_stop()) {
            unsigned long counter = 0;
            /* 内层忙等循环：barrier() 编译屏障，阻止编译器将循环优化掉，
             * 保证 CPU 真实执行空转，占用 CPU 时间片 */
            while (counter++ < 1000000)
                barrier();
        }

        /* 恢复抢占，preempt_count 减 1，调度器重新获得对该 CPU 的控制权 */
        preempt_enable();
    }

    pr_info("Stopping\n");
    return 0;
}

static int __init soft_lockup_init(void)
{
    pr_info("Initializing soft lockup simulation module\n");

    if (!enable_soft_lockup) {
        pr_warn("Soft lockup disabled by default. Set enable_soft_lockup=1 to activate.\n");
        return 0;
    }

    // Create a kernel thread for soft lockup simulation
    soft_lockup_task =
            kthread_run(soft_lockup_simulation, NULL, "soft_lockup_kthread");
    if (IS_ERR(soft_lockup_task)) {
        pr_err("Failed to create kernel thread\n");
        return PTR_ERR(soft_lockup_task);
    }

    pr_info("Loaded successfully. Soft lockup simulation started.\n");
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

    pr_info("Unloaded successfully\n");
}

module_init(soft_lockup_init);
module_exit(soft_lockup_exit);