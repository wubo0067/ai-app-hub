/*
 * @Author: CALM.WU
 * @Date: 2026-03-17 17:47:21
 * @Last Modified by: CALM.WU
 * @Last Modified time: 2026-03-17 18:21:08

 * Advanced DMA corruption simulator
 * - separate DMA buffer and victim object
 * - cross-object overwrite
 * - optional struct page corruption
 */

#define pr_fmt(fmt) "%s:%s(): " fmt, KBUILD_MODNAME, __func__

#include <linux/delay.h>
#include <linux/device.h>
#include <linux/dma-mapping.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/types.h>
#include <linux/io.h>

#include "mem_vaddr_page_dump.h"

MODULE_LICENSE("GPL");
MODULE_VERSION("3.0");

/* ================= 参数 ================= */

static bool enable_dma_corruption;
module_param(enable_dma_corruption, bool, 0644);

static bool corrupt_page_struct = false; // ⭐ 新增
module_param(corrupt_page_struct, bool, 0644);

/* ================= victim ================= */

struct victim_obj {
    char data[32];
    void (*fn)(void);
    u64 magic;
};

static void victim_safe_fn(void)
{
    pr_info("victim safe fn (should NOT run)\n");
}

static unsigned int corruption_len =
        PAGE_SIZE + sizeof(struct victim_obj); // 默认越界写入，覆盖 victim 对象
module_param(corruption_len, uint, 0644);

/* ================= 全局 ================= */

static struct device *dma_dev;
static struct task_struct *corrupt_task;

/* ================= DMA 写 ================= */

static void simulate_dma_write(dma_addr_t dma_handle, size_t len)
{
    // 在没有 IOMMU 的系统中，这个地址通常直接对应物理内存地址。
    // CPU 在模拟硬件设备执行 DMA 写操作时，通常会使用 dma_handle 这个 DMA 地址来访问内存，但 CPU 不能直接使用物理地址或总线
    // 地址，所以这里将物理地址转换为内核可以直接访问的虚拟地址
    void *alias = phys_to_virt(dma_handle);

    pr_info("DMA write: dma_handle=%pad alias=%px len=%zu\n", &dma_handle,
            alias, len);

    memset(alias, 0x41, len);
}

/* ================= 主逻辑 ================= */

static int dma_corruption_thread(void *data)
{
    void *region;
    // 前半段是 DMA source
    // 后半段是 victim
    // DMA 写越界后跨到第二页，破坏 victim
    // 被 DMA overflow 波及的对象
    struct victim_obj *victim;
    // 模拟 DMA 写入源
    void *dma_area;
    dma_addr_t dma_handle;
    struct page *victim_page;
    size_t total_size = PAGE_SIZE * 2;

    msleep(1000);

    /*
     * ⭐ 分配连续区域：
     * [ DMA buffer | victim ]
     */
    region = kmalloc(total_size, GFP_KERNEL);
    if (!region)
        return -ENOMEM;

    memset(region, 0, total_size);

    dma_area = region;
    // victim 在第 2 个 page 中，明确构造“跨 page / 跨区域 overwrite”
    // “设备往某个 DMA buffer 写数据，结果写穿了边界，破坏了后面的别的对象”
    victim = (struct victim_obj *)(region + PAGE_SIZE);

    /* 初始化 victim */
    victim->fn = victim_safe_fn;
    victim->magic = 0xdeadbeefcafebabeULL;

    pr_info("region=%px dma_area=%px victim=%px\n", region, dma_area, victim);

    dump_kvaddr_page_info(victim, sizeof(*victim), "victim");

    /*
     * ⭐ 为 DMA buffer 建立 mapping
     * 是 linux 内核中用于流式 DMA 映射的核心 API 之一
     * 功能：为一块单个、物理连续的内存缓冲区创建 DMA 映射。这个缓冲区通常是之前通过 kmalloc 或类似方式分配的。
     *      这种映射是临时的，用于一次或短期的 I/O 操作。操作完成后，需要通过 dma_unmap_single 来解除映射
     * 使用场景：非常适用于所谓的“流式”数据传输，其特点是数据单向或双向流动一次。
     *          网络设备驱动：发送或接收一个网络数据包。
     *          块设备驱动：向磁盘写入或从磁盘读取一个数据块。
     *          USB 设备驱动：传输一个 USB 请求块 (URB)。
     * 返回值：成功时返回一个 dma_addr_t 类型的 DMA 地址，这个地址可以被设备用来访问缓冲区。失败时返回一个错误码，通常是一个负数。
     *       dma 地址不一定等于物理地址，因为中间可能有 IOMMU 或总线地址转换
     *       无 IOMMU，简单直通：DMA address ≈ physical address
     *       有 IOMMU，地址转换：device DMA address -> IOMMU translation -> physical address
     */
    dma_handle =
            dma_map_single(dma_dev, dma_area, PAGE_SIZE, DMA_BIDIRECTIONAL);

    if (dma_mapping_error(dma_dev, dma_handle)) {
        pr_err("dma_map_single failed\n");
        kfree(region);
        return -EIO;
    }

    /*
     * ⭐ DMA overflow：从 DMA buffer 写，溢出到 victim
     */
    simulate_dma_write(dma_handle, corruption_len);

    pr_info("after corruption: victim->fn=%px magic=0x%llx\n", victim->fn,
            victim->magic);

    /* ================= 模式 A：函数指针 crash ================= */

    if (!corrupt_page_struct) {
        pr_emerg("trigger via corrupted function pointer\n");
        victim->fn(); // crash
    }

    /* ================= 模式 B：page struct 污染 ================= */
    // 调用 virt_to_page 宏，将 victim 对象的内核虚拟地址转换为管理这块内存的 struct page 描述地址
    // victim 是一个内核虚拟地址，该虚拟地址由内存管理子系统分配，并映射到某个物理内存页上。
    // 内核为每一个物理内存都维护一个名为 struct page 的元数据结构，来追踪这个页的状态
    victim_page = virt_to_page(victim);

    pr_emerg("corrupting struct page at %px\n", victim_page);

    /*
     * ⭐ 直接模拟 DMA 覆盖 page struct
     * 这是极其危险的操作。它直接用 0x41 覆盖了整个 struct page 结构。
     * 这个结构是内核内存管理的心脏，包含了指向伙伴系统（Buddy System）中其他空闲页的链表指针、页的引用计数、标志位等关键信息。
     */
    memset(victim_page, 0x41, sizeof(struct page));

    /*
     * 触发 page 相关路径
     * 当调用 __free_pages 尝试释放这个页时，内核会去操作已被破坏的 struct page。
     * 它会读取到无效的链表指针（比如 0x41414141...），并尝试将它们链接到空闲链表中。
     * 这会立刻破坏内核的内存管理数据结构，极大概率在尝试访问这些无效指针时触发缺页异常或一般保护性异常（General Protection Fault），
     * 导致内核崩溃。这种类型的崩溃通常非常难以调试，因为根本原因（struct page 被破坏）和崩溃点（在内存分配/释放路径中）可能相距甚远
     */
    pr_emerg("triggering page free to detect corruption\n");

    __free_pages(victim_page, 0); // 高概率 crash

    /*
     * 即使在触发 crash 之前，也应该有清理代码。
     * 这展示了完整的 DMA mapping 生命周期。
     */
    dma_unmap_single(dma_dev, dma_handle, PAGE_SIZE, DMA_BIDIRECTIONAL);
    kfree(region);
    return 0;
}

/* ================= init/exit ================= */

static int __init dma_memory_corruption_init(void)
{
    // 在路径/sys/devices 下创建设备文件 dma_memory_corruption_dev
    // 用来模拟一个可以咨询 DMA 操作的硬件设备。以便后续进行内存的破坏仿真
    dma_dev = root_device_register("dma_memory_corruption_dev");
    if (IS_ERR(dma_dev))
        return PTR_ERR(dma_dev);

    // 为创建的模拟设备设置 DMA 寻址能力，它告诉内核，这个设备能够对 64 位的物理地址进行 DMA 操作
    // 一个设备的 DMA 掩码定义了该设备能够访问的物理内存地址范围。
    // 这行代码是在初始化一个用于模拟 DMA 内存破坏的虚拟设备，并将其配置为能够访问系统中的所有 64 位物理地址，
    // 为后续模拟 DMA 写操作（包括越界写入）提供了必要的前提条件。
    if (dma_set_mask_and_coherent(dma_dev, DMA_BIT_MASK(64))) {
        pr_err("dma_set_mask_and_coherent failed\n");
        root_device_unregister(dma_dev);
        return -EIO;
    }

    if (!enable_dma_corruption)
        return 0;

    corrupt_task = kthread_run(dma_corruption_thread, NULL, "dma_corrupt");

    return PTR_ERR_OR_ZERO(corrupt_task);
}

static void __exit dma_memory_corruption_exit(void)
{
    if (corrupt_task)
        kthread_stop(corrupt_task);

    if (dma_dev)
        root_device_unregister(dma_dev);
}

module_init(dma_memory_corruption_init);
module_exit(dma_memory_corruption_exit);