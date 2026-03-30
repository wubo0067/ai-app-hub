/*
 * Helper to dump kernel virtual address mapping/page metadata to dmesg.
 */

#ifndef MEM_VADDR_PAGE_DUMP_H
#define MEM_VADDR_PAGE_DUMP_H

#include <linux/kernel.h>
#include <linux/mm.h>
#include <linux/slab.h>
#include <linux/types.h>

/**
 * @brief 打印内核虚拟地址页面信息
 *
 * 此函数用于获取并打印与指定的内核虚拟地址相关的物理地址、页面信息等，
 * 主要用于调试内存相关问题或分析页面属性。
 *
 * @param kvaddr 内核虚拟地址指针
 * @param len 要处理的数据长度，默认为1（如果传入0）
 * @param name 对象名称，用于在日志中标识该对象，可为空
 */
static inline void dump_kvaddr_page_info(const void *kvaddr, size_t len,
                                         const char *name)
{
    // 确定对象名称，如果 name 为空则使用默认值"kvaddr"
    const char *obj = name ? name : "kvaddr";

    // 定义物理地址起始和结束变量
    phys_addr_t phys_start;
    phys_addr_t phys_end;

    // 定义指向物理页的页面结构体指针
    struct page *page;

    // 检查虚拟地址是否有效
    if (!kvaddr) {
        pr_warn("%s: kvaddr is NULL\n", obj);
        return;
    }

    // 如果长度为 0，则设置默认长度为 1
    if (!len)
        len = 1;

    // 验证虚拟地址是否为有效的线性/直接映射内核地址
    if (!virt_addr_valid((void *)kvaddr)) {
        pr_warn("%s: kvaddr=%px is not a valid linear/direct-mapped kernel "
                "address\n",
                obj, kvaddr);
        return;
    }

    // 获取对应的物理地址范围
    phys_start = virt_to_phys((void *)kvaddr);
    phys_end = phys_start + len - 1;

    // 获取对应页面结构
    page = virt_to_page((void *)kvaddr);

    // 打印虚拟地址到物理地址的映射信息
    pr_info("%s: vaddr=%px phys_start=0x%pa phys_end=0x%pa len=%zu\n", obj,
            kvaddr, &phys_start, &phys_end, len);

    // 打印页面结构的相关信息
    pr_info("%s: page=%px pfn=%lu offset=0x%zx flags=0x%lx refcount=%d "
            "mapcount=%d\n",
            obj, page, page_to_pfn(page), offset_in_page(kvaddr), page->flags,
            page_ref_count(page), page_mapcount(page));

    // 判断页面是否属于 slab 分配器管理的内存
    if (PageSlab(page)) {
        // 如果是 slab 分配的内存，则打印 slab 对象信息
        pr_info("%s: slab-backed allocation detected, dump slab object info\n",
                obj);
        kmem_dump_obj((void *)kvaddr);
    } else {
        // 如果不是 slab 分配的内存，则输出提示信息
        pr_info("%s: not a slab-backed allocation (PageSlab=0)\n", obj);
    }
}

#endif /* MEM_VADDR_PAGE_DUMP_H */
