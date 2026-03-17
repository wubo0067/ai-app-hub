/*
 * @Author: CALM.WU
 * @Date: 2026-03-17 17:47:21
 * @Last Modified by: CALM.WU
 * @Last Modified time: 2026-03-17 18:02:39
 */

#define pr_fmt(fmt) "%s:%s(): " fmt, KBUILD_MODNAME, __func__

#include <linux/delay.h>
#include <linux/device.h>
#include <linux/dma-mapping.h>
#include <linux/err.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/module.h>
#include <linux/types.h>

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Calm.Wu");
MODULE_DESCRIPTION("DMA memory corruption simulator for vmcore analysis");
MODULE_VERSION("1.0");

// 模块参数：是否启用 DMA 损坏模拟，默认为 false
static bool enable_dma_corruption;
module_param(enable_dma_corruption, bool, 0644);
MODULE_PARM_DESC(enable_dma_corruption,
                 "Enable DMA corruption crash simulation (default: false)");

// 模块参数：触发损坏前的延迟时间（毫秒）
static unsigned int start_delay_ms = 1500;
module_param(start_delay_ms, uint, 0644);
MODULE_PARM_DESC(start_delay_ms,
                 "Delay in milliseconds before triggering corruption");

// 模块参数：从 DMA 缓冲区开始写入的字节数，必须大于 64 才能覆盖回调指针
static unsigned int corruption_len = 96;
module_param(corruption_len, uint, 0644);
MODULE_PARM_DESC(corruption_len, "Bytes written from DMA buffer start (must be "
                                 "> 64 to corrupt callback pointer)");

/**
 * DMA 损坏载荷结构体
 * 包含 64 字节的缓冲区，后面紧跟着一个函数指针和标记值
 * 通过向缓冲区写入超过 64 字节的数据可以覆盖 callback 指针，从而引发错误
 */
struct dma_corrupt_payload {
  uint8_t buf[64];        // 64 字节的缓冲区，用于模拟 DMA 传输
  void (*callback)(void); // 回调函数指针，会被缓冲区溢出覆盖
  uint64_t marker;        // 标记值，用于检测损坏程度
};

// 设备结构体和线程结构体声明
static struct device *dma_dev;
static struct task_struct *corrupt_task;

/**
 * 安全回调函数
 * 正常情况下会调用此函数，但在内存损坏后可能被恶意数据覆盖
 */
static void safe_callback(void) {
  pr_info("dma_memory_corruption: safe callback should never be called after "
          "corruption\n");
}

/**
 * DMA 损坏模拟线程函数
 * 分配 DMA 内存，填充恶意数据以覆盖回调函数指针，然后调用该指针触发内核崩溃
 */
static int dma_corruption_thread(void *data) {
  struct dma_corrupt_payload *payload; // 载荷结构体指针
  dma_addr_t dma_handle;               // DMA 地址句柄
  size_t write_len;                    // 实际写入长度

  // 延迟指定时间后再开始损坏操作
  msleep(start_delay_ms);

  // 分配 DMA 一致性内存，用于模拟 DMA 传输场景
  payload =
      dma_alloc_coherent(dma_dev, sizeof(*payload), &dma_handle, GFP_KERNEL);
  if (!payload) {
    pr_err("dma_memory_corruption: dma_alloc_coherent failed\n");
    return -ENOMEM;
  }

  // 初始化载荷结构体，设置安全回调函数和标记值
  memset(payload, 0, sizeof(*payload));
  payload->callback = safe_callback;
  payload->marker = 0x1122334455667788ULL;

  // 记录分配的内存信息
  pr_info("dma_memory_corruption: payload=%px dma=%pad size=%zu\n", payload,
          &dma_handle, sizeof(*payload));

  // 计算实际写入长度，不能超过载荷大小
  write_len = min_t(size_t, corruption_len, sizeof(*payload));
  // 向缓冲区写入恶意数据 (0x41='A')，如果 write_len > 64 则会覆盖 callback 指针
  memset(payload->buf, 0x41, write_len);

  pr_info("dma_memory_corruption: wrote %zu bytes from payload->buf\n",
          write_len);
  // 检查 callback 指针是否已被破坏以及标记值是否正确
  pr_info("dma_memory_corruption: callback pointer after corruption=%px "
          "marker=0x%llx\n",
          payload->callback, payload->marker);

  // 调用被破坏的回调函数，这将导致内核崩溃
  pr_emerg("dma_memory_corruption: invoking corrupted callback to trigger "
           "kernel crash\n");
  payload->callback();

  // 释放分配的 DMA 内存
  dma_free_coherent(dma_dev, sizeof(*payload), payload, dma_handle);
  return 0;
}

/**
 * 模块初始化函数
 * 注册设备，创建内核线程来执行 DMA 损坏模拟
 */
static int __init dma_memory_corruption_init(void) {
  pr_info("dma_memory_corruption: module loaded\n");

  // 注册根设备，用于 DMA 操作
  dma_dev = root_device_register("dma_memory_corruption_dev");
  if (IS_ERR(dma_dev)) {
    pr_err("dma_memory_corruption: root_device_register failed\n");
    return PTR_ERR(dma_dev);
  }

  // 如果没有启用损坏模拟，则退出
  if (!enable_dma_corruption) {
    pr_warn("dma_memory_corruption: disabled by default, set "
            "enable_dma_corruption=1 to trigger\n");
    return 0;
  }

  // 检查损坏长度参数，确保其足够大以覆盖回调指针
  if (corruption_len <= sizeof(((struct dma_corrupt_payload *)0)->buf)) {
    pr_warn("dma_memory_corruption: corruption_len should be > 64 to corrupt "
            "callback pointer\n");
  }

  // 创建内核线程来执行损坏模拟
  corrupt_task = kthread_run(dma_corruption_thread, NULL, "dma_corrupt_thread");
  if (IS_ERR(corrupt_task)) {
    int ret = PTR_ERR(corrupt_task);

    pr_err("dma_memory_corruption: failed to start kthread: %d\n", ret);
    root_device_unregister(dma_dev);
    dma_dev = NULL;
    return ret;
  }

  return 0;
}

/**
 * 模块退出函数
 * 清理资源，停止内核线程并注销设备
 */
static void __exit dma_memory_corruption_exit(void) {
  // 停止内核线程
  if (corrupt_task && !IS_ERR(corrupt_task)) {
    kthread_stop(corrupt_task);
    corrupt_task = NULL;
  }

  // 注销设备
  if (dma_dev) {
    root_device_unregister(dma_dev);
    dma_dev = NULL;
  }

  pr_info("dma_memory_corruption: module unloaded\n");
}

module_init(dma_memory_corruption_init);
module_exit(dma_memory_corruption_exit);