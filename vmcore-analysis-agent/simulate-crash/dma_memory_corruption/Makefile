obj-m += dma_memory_corruption_mod.o
ccflags-y := -g -O0 -fno-inline -fno-inline-functions \
                -fno-inline-small-functions -fno-optimize-sibling-calls \
                -DDEBUG

all:
	make -C /lib/modules/$(shell uname -r)/build M=$(PWD) modules V=1

clean:
	make -C /lib/modules/$(shell uname -r)/build M=$(PWD) clean V=1
