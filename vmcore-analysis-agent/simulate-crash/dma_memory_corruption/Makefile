obj-m += dma_memory_corruption_mod.o
ccflags-y := -g -Og -fno-inline -fno-inline-functions \
                -fno-inline-small-functions -fno-optimize-sibling-calls \
                -DDEBUG

# 添加GCC编译器兼容性选项
ccflags-y += -Wno-error=address-of-packed-member -Wno-error=restrict

all:
	make -C /lib/modules/$(shell uname -r)/build M=$(PWD) modules V=1

clean:
	make -C /lib/modules/$(shell uname -r)/build M=$(PWD) clean V=1