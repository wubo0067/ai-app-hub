#include <linux/delay.h>
#include <linux/init.h>
#include <linux/interrupt.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/module.h>
#include <linux/sched.h>
#include <linux/sched/signal.h>  // Include for signal_pending

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Kernel Diagnostic Team");
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
static int soft_lockup_simulation(void *data) {
  // Try to bind to specific CPU if requested
  if (cpu_number >= 0 && cpu_number < num_online_cpus()) {
    if (set_cpus_allowed_ptr(current, cpumask_of(cpu_number))) {
      printk(
          KERN_WARNING
          "Soft Lockup Module: Failed to bind to CPU %d, running on any CPU\n",
          cpu_number);
    } else {
      printk(KERN_INFO "Soft Lockup Module: Successfully bound to CPU %d\n",
             cpu_number);
    }
  }

  printk(KERN_INFO
         "Soft Lockup Module: Starting soft lockup simulation on CPU %d\n",
         smp_processor_id());

  // Disable preemption to keep this thread running continuously
  while (!kthread_should_stop() && enable_soft_lockup) {
    // Busy-wait loop to consume CPU cycles without yielding
    // This will prevent other processes from running on this CPU
    /*
    在 preempt_disable() 状态下，CPU
    仍然会响应硬件中断（如时钟中断、网卡中断、IPI 核间中断）。
    当中断处理程序执行完毕返回时，由于抢占被禁止，调度器不会切换到其他任务，而是直接恢复执行你当前的那个死循环线程。
    */
    preempt_disable();  // Disable preemption to trigger soft lockup but allow
                        // interrupts (IPIs)

    // Infinite loop that doesn't yield CPU control
    while (enable_soft_lockup && !kthread_should_stop()) {
      // Perform some dummy computation to keep CPU busy
      volatile unsigned long counter = 0;
      while (counter++ < 1000000) {
        // Do some dummy work
        barrier();
      }

      // Check periodically if we should stop
      if (signal_pending(current)) break;
    }

    preempt_enable();  // Re-enable preemption
  }

  printk(KERN_INFO "Soft Lockup Module: Stopping soft lockup simulation\n");
  return 0;
}

static int __init soft_lockup_init(void) {
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

static void __exit soft_lockup_exit(void) {
  enable_soft_lockup = false;  // Stop the soft lockup

  if (soft_lockup_task) {
    // Wait for the thread to finish
    kthread_stop(soft_lockup_task);
    soft_lockup_task = NULL;
  }

  printk(KERN_INFO "Soft Lockup Module: Unloaded successfully\n");
}

module_init(soft_lockup_init);
module_exit(soft_lockup_exit);