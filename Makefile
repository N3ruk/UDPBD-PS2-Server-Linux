CXX ?= g++
CXXFLAGS ?= -O2 -std=c++17 -Wall -Wextra -Wpedantic -D_FILE_OFFSET_BITS=64 -D_FORTIFY_SOURCE=2 -fstack-protector-strong
LDFLAGS ?= -Wl,-z,relro,-z,now

all: udpbd-server
udpbd-server: src/udpbd-server.cpp src/virtual-disk.h
	$(CXX) $(CXXFLAGS) $< -o $@ $(LDFLAGS)
test: udpbd-server
	python3 -m unittest discover -s tests -v
.PHONY: all test
