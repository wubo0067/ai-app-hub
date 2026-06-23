/*,,,,,?
 * @Author: CALM.WU
 * @Date: 2026-03-17 18:02:59
 * @Last Modified by:   CALM.WU
 * @Last Modified time: 2026-03-17 18:02:59
 */

#define pr_fmt(fmt) "%s:%s(): " fmt, KBUILD_MODNAME, __func__

#include <linux/delay.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/module.h>
#include <linux/rcupdate.h>
#include <linux/jiffies.h>
#include <linux/sched.h>
#include <linux/sched/signal.h>
#include <linux/completion.h>
#include <linux/mutex.h>
#include <linux/bug.h>

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Calm.Wu");
MODULE_DESCRIPTION("RCU Stall Simulator for Testing and Debugging");

// 默认 stall 时间设为 70s，通常 RCU stall 检测阈值 defaults to 60s (check
// /sys/module/rcupdate/parameters/rcu_cpu_stall_timeout)
static uint32_t stall_duration_ms = 70000;
module_param(stall_duration_ms, uint, 0644);
MODULE_PARM_DESC(stall_duration_ms,
                 "Duration to hold RCU lock in milliseconds");

static struct task_struct *task;
static DECLARE_COMPLETION(thread_done);
static DEFINE_MUTEX(thread_mutex);

/*
 * rcu_stall_thread - RCU Stall 模拟内核线程主函数
 *
 * @data: kthread_run 传入的私有数据（未使用）
 *
 * 工作原理：
 *   1. 获取 RCU 读锁（rcu_read_lock），进入 RCU 临界区
 *   2. 在持有 RCU 锁的情况下长时间休眠（默认 70 秒）
 *   3. 期间该 CPU 无法报告 quiescent state（静止状态），
 *      导致 RCU Grace Period 无法完成
 *   4. RCU 检测器会在 rcu_cpu_stall_timeout（通常 60 秒）后触发警告或 panic
 *
 * RCU Stall 触发条件：
 *   - 当前 CPU 持有 RCU 读锁的时间超过 rcu_cpu_stall_timeout
 *   - RCU 子系统无法推进 Grace Period，阻塞了等待 synchronize_rcu() 的任务
 *   - 内核 RCU 检测器在 hrtimer 中周期性检查，发现 stall 则打印警告或 panic
 *
 * 与 soft lockup 的区别：
 *   - soft lockup: preempt_disable() 禁止调度，CPU 完全无法让出
 *   - RCU stall: 仍可调度（使用 schedule_timeout_interruptible），
 *     但持有 RCU 锁导致 RCU 子系统无法推进，特定于 RCU 机制
 *
 * 返回值：固定返回 0
 */
static int rcu_stall_thread(void *data)
{
    pid_t pid, tgid;
    uint64_t time_out_jiffies;

    /* 1. 打印线程 PID、TGID 和运行 CPU 信息 */
    pid = task_pid_nr(current);
    tgid = task_tgid_nr(current);
    pr_info("rcu_stall_mod: Starting stall simulation for %u ms, Thread PID: %d, "
            "TGID: %d, running on CPU: %d\n",
            stall_duration_ms, pid, tgid, smp_processor_id());

    /* 进入 RCU 临界区：
     * rcu_read_lock() 标记当前 CPU 进入 RCU 读侧临界区，
     * 此时该 CPU 不能报告 quiescent state，
     * RCU 子系统必须等待所有 CPU 退出临界区才能完成 Grace Period */
    rcu_read_lock();

    /* 将毫秒转换为 jiffies（内核时钟滴答数），作为倒计时计数器
     * 注意：msecs_to_jiffies() 返回 unsigned long 类型 */
    time_out_jiffies = msecs_to_jiffies(stall_duration_ms);

    /* 在持有 RCU 锁的情况下循环休眠，模拟长时间占用 RCU 临界区：
     * - 每次休眠 1 jiffy（通常 1-10ms），允许调度其他任务，不触发 soft lockup
     * - 但始终持有 RCU 锁，导致 RCU 子系统无法推进 Grace Period
     * - 循环持续到超时或收到停止信号 */
    while (time_out_jiffies && !kthread_should_stop()) {
        /* schedule_timeout_interruptible(1) 使线程进入可中断睡眠状态 1 jiffy，
         * 期间可被信号唤醒，避免忙等浪费 CPU，但 RCU 锁仍然持有
         * schedule_timeout_interruptible(1)
         * │
         * ▼
         * set_current_state(TASK_INTERRUPTIBLE)   ← 标记自己为"可中断睡眠"
         * │
         * ▼
         * schedule()                             ← 让出 CPU，调度器运行其他 task
         * │
         * │   ┌──────────────────────────────────────┐
         * │   │  线程进入睡眠，CPU 资源释放给其他任务  │
         * │   └──────────────────────────────────────┘
         * │
         * ├── 途径 1：超时唤醒（1 jiffy 后）
         * │   内核定时器到期 → 自动唤醒
         * │   返回值 = 0（剩余 jiffies）
         * │
         * ├── 途径 2：信号唤醒
         * │   收到信号（SIGINT/SIGTERM 等）→ 立即唤醒
         * │   返回值 = 剩余 jiffies
         * │
         * └── 途径 3：被 wake_up() 唤醒
         *     其他代码显式唤醒 → 立即唤醒
         *     返回值 = 剩余 jiffies
         */
        schedule_timeout_interruptible(1);
        time_out_jiffies--;

        /* 检查是否有待处理的信号（如 SIGINT、SIGTERM），
         * 允许用户提前中断模拟，避免等待完整的 stall_duration_ms */
        if (signal_pending(current)) {
            /* flush_signals() 清除当前进程的待处理信号，
             * 防止信号传播影响后续清理流程 */
            flush_signals(current);
            pr_info("rcu_stall_mod: Received signal, exiting early\n");
            break;
        }
    }

    /* ===== 备选方案：使用 mdelay 进行忙等待 =====
     * 取消注释下面的 mdelay 调用，可以切换到忙等模式：
     * - mdelay(stall_duration_ms) 会持续占用 CPU 不让出处理器
     * - 同时触发 RCU stall 和 soft lockup 两种警告
     * - 适合测试同时发生的复合故障场景
     *
     * 效果对比：
     *   当前方案（schedule_timeout）: 仅触发 RCU stall，CPU 可调度
     *   mdelay 方案：同时触发 RCU stall + soft lockup，CPU 完全占用
     */
    // mdelay(stall_duration_ms);

    /* 退出 RCU 临界区，释放 RCU 读锁，
     * 该 CPU 可以报告 quiescent state，RCU 子系统恢复正常推进 */
    rcu_read_unlock();

    pr_info("rcu_stall_mod: RCU lock released, stall simulation finished.\n");

    /* 通知等待者（rcu_stall_exit）线程已完成工作，
     * 配合 wait_for_completion() 实现优雅退出 */
    complete(&thread_done);
    return 0;
}

/*
 * rcu_stall_init - 模块初始化函数，在 insmod 时调用
 *
 * 工作流程：
 *   1. 校验模块参数 stall_duration_ms 的有效性（1-300000 ms）
 *   2. 使用互斥锁保护，检查是否已有线程运行（防止重复加载）
 *   3. 创建名为 "rcu_stall_thr" 的内核线程执行 stall 模拟
 *   4. 线程创建后立即开始运行，init 函数返回
 *
 * 设计考虑：
 *   - 使用独立线程而非在 init 中直接模拟，避免阻塞 insmod 进程
 *   - 互斥锁 thread_mutex 保护全局 task 指针，防止并发冲突
 *   - 参数校验确保 stall 时间在合理范围，避免过短（无效）或过长（系统挂死）
 *
 * 返回值：
 *   0: 成功创建模拟线程
 *   -EINVAL: stall_duration_ms 参数无效
 *   -EBUSY: 已有线程正在运行
 *   PTR_ERR(task): kthread_run 创建失败的错误码
 */
static int __init rcu_stall_init(void)
{
    pr_info("rcu_stall_mod: module loaded\n");

    /* 参数合法性检查：
     * - 最小值 1ms：低于此值无法触发 RCU stall 检测
     * - 最大值 300000ms (5 分钟)：避免系统长时间不可用 */
    if (stall_duration_ms == 0 || stall_duration_ms > 300000) {
        pr_warn("rcu_stall_mod: Invalid stall_duration_ms (%u ms), must be between 1 and 300000 ms\n",
                stall_duration_ms);
        return -EINVAL;
    }

    /* 获取互斥锁，保护对全局变量 task 的并发访问，
     * 防止多次 insmod 导致的竞态条件 */
    mutex_lock(&thread_mutex);

    /* 检查是否已有有效的线程在运行：
     * task != NULL: 线程指针已被赋值
     * !IS_ERR(task): 不是 ERR_PTR 编码的错误值 */
    if (task && !IS_ERR(task)) {
        pr_warn("rcu_stall_mod: Thread already running\n");
        mutex_unlock(&thread_mutex);
        return -EBUSY;
    }

    /* 重新初始化 completion 结构体，确保模块重载时状态正确
     * 这对于支持多次加载/卸载模块至关重要 */
    reinit_completion(&thread_done);

    /* 创建并启动内核线程执行 stall 模拟：
     * - rcu_stall_thread: 线程入口函数
     * - NULL: 不传递私有数据
     * - "rcu_stall_thr": 线程名称，可在 ps/top 中看到
     * kthread_run 创建后线程立即处于可运行状态，由调度器安排执行 */
    task = kthread_run(rcu_stall_thread, NULL, "rcu_stall_thr");

    /* 检查线程创建是否失败：
     * kthread_run 失败时返回 ERR_PTR 编码的负数错误码，
     * 使用 IS_ERR 判断，PTR_ERR 提取具体错误码（如 -ENOMEM） */
    if (IS_ERR(task)) {
        pr_err("Failed to create thread, error %ld\n", PTR_ERR(task));
        mutex_unlock(&thread_mutex);
        return PTR_ERR(task);
    }

    /* 打印模块初始化完成信息，记录当前执行的 CPU 编号，
     * 注意：线程可能在其他 CPU 上运行，此处仅为 init 函数自身的 CPU */
    pr_info("rcu_stall_mod init on cpu:%d\n", smp_processor_id());
    mutex_unlock(&thread_mutex);
    return 0;
}

/*
 * rcu_stall_exit - 模块退出函数，在 rmmod 时调用
 *
 * 工作流程：
 *   1. 检查全局 task 指针是否有效（非 NULL 且非 ERR_PTR）
 *   2. 调用 wait_for_completion() 等待 rcu_stall_thread 自然完成
 *      - 阻塞直到线程调用 complete(&thread_done)
 *      - 确保 RCU 锁已释放，避免强制停止导致死锁
 *   3. 调用 kthread_stop() 设置停止标志并等待线程退出
 *   4. 清理资源，模块安全卸载
 *
 * 设计考虑：
 *   - 优雅退出：先等待模拟完成再停止线程，避免 RCU 临界区中被强制杀死
 *   - 双重保险：wait_for_completion 确保工作完成，kthread_stop 确保线程回收
 *   - 空指针检查：防止在 init 失败的情况下 exit 访问无效 task
 *
 * 返回值：无（void）
 */
static void __exit rcu_stall_exit(void)
{
    struct task_struct *tsk;

    /* 使用互斥锁保护，避免与 init 函数并发访问 task 指针
     * 虽然实际场景极少发生，但符合严格的线程安全规范 */
    mutex_lock(&thread_mutex);
    tsk = task;
    mutex_unlock(&thread_mutex);

    /* 检查线程指针有效性，确保有线程需要清理 */
    if (tsk && !IS_ERR(tsk)) {
        pr_info("Waiting for thread to finish...\n");

        /* 阻塞等待 completion 事件，直到 rcu_stall_thread 调用 complete()：
         * - 确保 RCU 锁已释放（rcu_read_unlock 在 complete 之前调用）
         * - 避免在 RCU 临界区中强制停止线程导致系统死锁
         * - 等待期间进程状态为 TASK_UNINTERRUPTIBLE */
        wait_for_completion(&thread_done);

        /* 停止内核线程：
         * - 设置 kthread_should_stop() 标志为 true
         * - 唤醒线程（如果在休眠）
         * - 等待线程返回（阻塞直到线程函数 return）
         * - 回收线程资源（task_struct）*/
        kthread_stop(tsk);

        /* 清理全局线程指针，确保模块重载时状态正确
         * 必须在互斥锁保护下进行，防止竞态条件 */
        mutex_lock(&thread_mutex);
        task = NULL;
        mutex_unlock(&thread_mutex);

        pr_info("Thread stopped, module unloaded\n");
    } else {
        /* task 为 NULL 或 ERR_PTR，说明线程未成功创建或已被清理，
         * 无需执行停止操作，直接返回 */
        pr_warn("No valid thread to stop\n");
    }
}

// 注册模块初始化和退出函数
module_init(rcu_stall_init);
module_exit(rcu_stall_exit);
