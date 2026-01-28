#include <linux/delay.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/module.h>
#include <linux/rcupdate.h>

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Demo");
MODULE_DESCRIPTION("RCU Stall Simulator");

// 默认 stall 时间设为 70s，通常 RCU stall 检测阈值 defaults to 60s (check
// /sys/module/rcupdate/parameters/rcu_cpu_stall_timeout)
static int stall_duration_ms = 70000;
module_param(stall_duration_ms, int, 0644);
MODULE_PARM_DESC(stall_duration_ms,
                 "Duration to hold RCU lock in milliseconds");

static struct task_struct *task;

static int rcu_stall_thread(void *data)
{
    pid_t pid = task_pid_nr(current);
    pid_t tgid = task_tgid_nr(current);
    pr_info(
        "rcu_stall_mod: Starting stall simulation for %d ms, Thread PID: %d, TGID: %d, running on CPU: %d\n",
        stall_duration_ms, pid, tgid, smp_processor_id());
    /*
   * 模拟 RCU stall:
   * 获取 RCU read lock，然后长时间忙等待。
   * 这会阻止当前 CPU 报告 RCU quiescent state (静止状态)，
   * 从而导致 RCU Grace Period 无法结束。
   */
    rcu_read_lock();

    // 使用 mdelay 进行忙等待，它会持续占用 CPU 时间而不让出处理器，这通常也会触发 softlockup 警告。
    // RCU 检测器会在 rcu_cpu_stall_timeout 秒后触发警告或 panic。
    mdelay(stall_duration_ms);

    rcu_read_unlock();

    pr_info("rcu_stall_mod: RCU lock released, stall simulation finished.\n");
    return 0;
}

static int __init rcu_stall_init(void)
{
    pr_info("rcu_stall_mod: module loaded\n");
    // 创建内核线程来执行 stall 操作，避免阻塞 insmod 进程（虽然 insmod
    // 本身也会进入内核态）
    task = kthread_run(rcu_stall_thread, NULL, "rcu_stall_thr");
    if (IS_ERR(task)) {
        pr_err("rcu_stall_mod: Failed to create thread\n");
        return PTR_ERR(task);
    }
    pr_info("rcu_stall_mod init on cpu:%d\n", smp_processor_id());
    return 0;
}

static void __exit rcu_stall_exit(void)
{
    pr_info("rcu_stall_mod: module unloaded\n");
}

module_init(rcu_stall_init);
module_exit(rcu_stall_exit);
