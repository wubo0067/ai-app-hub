/*
 * hard_lockup.c - simulate hard lockup and (optionally) force panic
 *
 * RHEL 9.x (5.14) uses proc_ops.
 *
 * Behavior:
 * - Create /proc/hard_lockup_trigger
 * - Write "1" to trigger:
 *    - start a panic helper kthread on a different CPU (optional)
 *    - trigger a hard lockup on a victim CPU (irq off + infinite loop)
 *
 * Why panic helper:
 * - Some environments don't have working hard-lockup watchdog (NMI
 * watchdog/perf NMI), so the system may freeze forever without panic/vmcore.
 */

#include <linux/cpumask.h>
#include <linux/delay.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/module.h>
#include <linux/proc_fs.h>
#include <linux/smp.h>
#include <linux/string.h>
#include <linux/uaccess.h>
#include <linux/version.h>

#define PROC_NAME "hard_lockup_trigger"

static struct proc_dir_entry *proc_entry;
static struct task_struct *panic_task;

/* Module parameters */
static int victim_cpu = -1; /* -1 = auto-pick */
module_param(victim_cpu, int, 0644);

static int panic_after = 20; /* seconds, only used when force_panic=1 */
module_param(panic_after, int, 0644);

static bool force_panic = true; /* insurance: panic from another CPU */
module_param(force_panic, bool, 0644);

static int pick_online_cpu_not(int exclude_cpu) {
  int cpu;

  for_each_online_cpu(cpu) {
    if (cpu != exclude_cpu) return cpu;
  }
  return -1;
}

/* Runs on the victim CPU: disable irq + spin forever */
static void trigger_hard_lockup_on_cpu(void *info) {
  pr_warn("hard_lockup: victim CPU=%d entering hard lockup (irq off, spin)\n",
          smp_processor_id());

  preempt_disable();
  local_irq_disable();

  while (1) cpu_relax();

  /* never reached */
  local_irq_enable();
  preempt_enable();
}

/* Runs on a helper CPU: waits then panic() to guarantee vmcore */
static int panic_helper_thread(void *data) {
  int seconds = *(int *)data;

  pr_warn(
      "hard_lockup: panic helper started on CPU=%d, will panic in %d seconds\n",
      smp_processor_id(), seconds);

  ssleep(seconds);

  pr_emerg("hard_lockup: forcing panic (watchdog may be unavailable)\n");
  panic("hard_lockup: forced panic after simulated hard lockup");

  return 0;
}

static void trigger_lockup_with_insurance(void) {
  int caller_cpu = smp_processor_id();
  int victim = victim_cpu;
  int helper;
  int ret;

  if (victim < 0 || !cpu_online(victim)) {
    /* Prefer not to lock up current CPU so proc write can return quickly */
    victim = pick_online_cpu_not(caller_cpu);
    if (victim < 0) victim = caller_cpu; /* single CPU system fallback */
  }

  helper = pick_online_cpu_not(victim);

  if (force_panic && helper >= 0) {
    /* Start helper thread bound to helper CPU */
    if (panic_task) {
      pr_warn("hard_lockup: panic helper already exists, skipping create\n");
    } else {
      /* pass panic_after by address; safe because module stays loaded until
       * crash */
      panic_task = kthread_create(panic_helper_thread, &panic_after,
                                  "hard_lockup_panic");
      if (IS_ERR(panic_task)) {
        pr_err("hard_lockup: failed to create panic helper kthread\n");
        panic_task = NULL;
      } else {
        kthread_bind(panic_task, helper);
        wake_up_process(panic_task);
      }
    }
  } else if (force_panic && helper < 0) {
    pr_warn(
        "hard_lockup: only one online CPU; cannot run panic helper on another "
        "CPU\n");
  }

  /* Trigger lockup on victim CPU (async) */
  ret = smp_call_function_single(victim, trigger_hard_lockup_on_cpu, NULL, 0);
  if (ret) {
    pr_err(
        "hard_lockup: smp_call_function_single(%d) failed: %d; falling back to "
        "local trigger\n",
        victim, ret);
    if (victim == caller_cpu) trigger_hard_lockup_on_cpu(NULL);
  }
}

/* /proc write handler */
static ssize_t proc_write(struct file *file, const char __user *buffer,
                          size_t count, loff_t *pos) {
  char input[32];

  if (count == 0) return 0;
  if (count > sizeof(input) - 1) return -EINVAL;
  if (copy_from_user(input, buffer, count)) return -EFAULT;

  input[count] = '\0';

  /*
   * Commands:
   * - "1" (default): lockup + (optional) force panic
   * - "watchdog": lockup only (requires working hard-lockup watchdog to panic)
   */
  if (!strncmp(input, "watchdog", 8)) {
    pr_warn("hard_lockup: mode=watchdog-only\n");
    force_panic = false;
    trigger_lockup_with_insurance();
    return count;
  }

  if (input[0] == '1') {
    trigger_lockup_with_insurance();
    return count;
  }

  return -EINVAL;
}

/* /proc read handler */
static ssize_t proc_read(struct file *file, char __user *buffer, size_t count,
                         loff_t *pos) {
  char msg[256];
  int len;

  len = scnprintf(
      msg, sizeof(msg),
      "Write '1' to trigger hard lockup.\n"
      "Write 'watchdog' to trigger hard lockup without forced panic.\n"
      "Params: victim_cpu=%d panic_after=%d force_panic=%d\n",
      victim_cpu, panic_after, force_panic);

  return simple_read_from_buffer(buffer, count, pos, msg, len);
}

static const struct proc_ops proc_fops = {
    .proc_read = proc_read,
    .proc_write = proc_write,
};

static int __init hard_lockup_init(void) {
  pr_info("hard_lockup: module loaded\n");

  proc_entry = proc_create(PROC_NAME, 0666, NULL, &proc_fops);
  if (!proc_entry) {
    pr_err("hard_lockup: failed to create /proc/%s\n", PROC_NAME);
    return -ENOMEM;
  }

  pr_info("hard_lockup: /proc/%s created\n", PROC_NAME);
  return 0;
}

static void __exit hard_lockup_exit(void) {
  if (proc_entry) proc_remove(proc_entry);

  /* Usually unreachable because we crash; still keep it clean. */
  if (panic_task) {
    kthread_stop(panic_task);
    panic_task = NULL;
  }

  pr_info("hard_lockup: module unloaded\n");
}

module_init(hard_lockup_init);
module_exit(hard_lockup_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Simulate hard lockup; optionally force panic for vmcore");
MODULE_VERSION("1.1");