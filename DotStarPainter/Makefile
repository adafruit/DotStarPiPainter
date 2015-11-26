# Makefile for compiling the lightpaint.so Python/C module.

all: lightpaint.so

lightpaint.so: lightpaint.o
	gcc -s -shared -Wl,-soname,liblightpaint.so -o $@ $<

.c.o:
	gcc -fPIC -O3 -fomit-frame-pointer -funroll-loops -c $<

clean:
	rm -f lightpaint.o lightpaint.so
