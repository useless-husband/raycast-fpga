# raycast-fpga — run every target from the repository root.
#
# Every path below is relative on purpose: the checkout may live in a
# directory whose name contains spaces or non-ASCII characters, which GNU make
# (and Verilator's generated makefiles) cannot handle in absolute paths.  For
# the same reason the Verilator model is compiled with one direct compiler
# call instead of Verilator's own makefile.

SHELL := /bin/bash
VENV ?= .venv
PYTHON ?= $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
VERILATOR ?= verilator
YOSYS ?= yosys
CXX ?= c++
FFMPEG ?= ffmpeg

RTL := $(sort $(wildcard rtl/*.sv))
BOARD := rtl/board/tmds_serializer.sv rtl/board/top_level.sv
MEM := rtl/mem/map.hex rtl/mem/tex.hex rtl/mem/palette.hex rtl/mem/trig.hex rtl/mem/map_start.svh

VROOT := $(shell $(VERILATOR) --getenv VERILATOR_ROOT 2>/dev/null)
SDL_CONFIG := $(shell command -v sdl2-config 2>/dev/null)
ifneq ($(SDL_CONFIG),)
SDL_FLAGS := -DWITH_SDL $(shell sdl2-config --cflags)
SDL_LIBS := $(shell sdl2-config --libs)
endif

# Two simulation builds of the same RTL: real 720p timing, and a 320x180
# variant (with enough blanking for the ray engine) for fast tests and demos.
VPARAMS_720p :=
W_720p := 1280
H_720p := 720
VPARAMS_small := -GACTIVE_H=320 -GH_FP=16 -GH_SYNC=32 -GH_BP=48 \
                 -GACTIVE_V=180 -GV_FP=3 -GV_SYNC=5 -GV_BP=32 -GDEBOUNCE_CYCLES=64
W_small := 320
H_small := 180

VDEFS := -DVERILATOR=1 -DVM_COVERAGE=0 -DVM_SC=0 -DVM_TIMING=0 -DVM_TRACE=0 -DVM_TRACE_FST=0 \
         -DVM_TRACE_VCD=0 -DVM_TRACE_SAIF=0 -DVM_VPI=0
VFLAGS := --cc -O3 --x-assign fast --x-initial fast --noassert -Irtl -Irtl/mem \
          --top-module raycast_system

.PHONY: all assets lint unit system test synth play play-small video docs-media bench venv check-python clean

all: test

.SECONDARY:

# ---------------------------------------------------------------- assets
assets:
	python3 tools/gen_assets.py --map maps/level1.txt --out rtl/mem

# ------------------------------------------------------------------ lint
lint:
	$(VERILATOR) --lint-only -Wall -Irtl -Irtl/mem --top-module raycast_system $(RTL)
	$(VERILATOR) --lint-only -Wall -Irtl -Irtl/mem --top-module top_level \
	  synth/xilinx_stubs.sv $(RTL) $(BOARD)
	@echo "lint: verilator -Wall clean"

# ----------------------------------------------------------------- tests
check-python:
	@$(PYTHON) -c "import cocotb, pytest" 2>/dev/null || { \
	  echo "cocotb/pytest not found for $(PYTHON)."; \
	  echo "Run 'make venv' once (creates $(VENV)), or pass PYTHON=/path/to/python."; exit 1; }

unit: check-python
	$(PYTHON) -m pytest -q tests/model tests/unit

build/vsim_%/Vraycast_system.h: $(RTL) $(MEM)
	rm -rf build/vsim_$*
	$(VERILATOR) $(VFLAGS) -Mdir build/vsim_$* $(VPARAMS_$*) $(RTL)

build/vsim_%/vsim: build/vsim_%/Vraycast_system.h sim/sim_main.cpp
	$(CXX) -std=c++17 -O2 -w $(VDEFS) -DSIM_W=$(W_$*) -DSIM_H=$(H_$*) $(SDL_FLAGS) \
	  -Ibuild/vsim_$* -I$(VROOT)/include -I$(VROOT)/include/vltstd \
	  build/vsim_$*/*.cpp $(VROOT)/include/verilated.cpp $(VROOT)/include/verilated_threads.cpp \
	  sim/sim_main.cpp -o $@ $(SDL_LIBS) -lpthread

system: check-python build/vsim_small/vsim build/vsim_720p/vsim
	$(PYTHON) -m pytest -q tests/system

test: lint unit system

# ------------------------------------------------------------- synthesis
synth:
	mkdir -p build/synth
	$(YOSYS) -q -l build/synth/yosys.log synth/synth.ys
	$(PYTHON) tools/synth_report.py build/synth/utilization.json > synth/report.md
	@cat synth/report.md

# ------------------------------------------------------------------ demo
play: build/vsim_720p/vsim
	./build/vsim_720p/vsim play 1

play-small: build/vsim_small/vsim
	./build/vsim_small/vsim play 4

# 720p walk rendered by the RTL -> MP4 (build/media) and a small GIF for the README
video: build/vsim_720p/vsim
	mkdir -p build/media
	./build/vsim_720p/vsim record sim/walk.txt build/media/walk.rgb
	$(FFMPEG) -loglevel error -y -f rawvideo -pix_fmt rgb24 -s 1280x720 -r 30 -i build/media/walk.rgb \
	  -c:v libx264 -pix_fmt yuv420p -crf 20 build/media/walk.mp4
	$(FFMPEG) -loglevel error -y -f rawvideo -pix_fmt rgb24 -s 1280x720 -r 30 -i build/media/walk.rgb \
	  -vf "fps=15,scale=480:-1:flags=area,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4" \
	  build/media/walk.gif
	$(FFMPEG) -loglevel error -y -f rawvideo -pix_fmt rgb24 -s 1280x720 -i build/media/walk.rgb \
	  -vf "select=eq(n\,160)" -frames:v 1 build/media/frame.png
	rm build/media/walk.rgb
	@ls -l build/media

# committed README media (each must stay under 1 MB): a short GIF and one
# exact frame of RTL output
docs-media: video
	$(FFMPEG) -loglevel error -y -ss 1 -t 9 -i build/media/walk.mp4 \
	  -vf "fps=8,scale=384:-1:flags=area,split[a][b];[a]palettegen=max_colors=48:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
	  docs/media/walk.gif
	cp build/media/frame.png docs/media/frame.png   # exact RTL pixels (lossless)
	@for f in docs/media/walk.gif docs/media/frame.png; do \
	  s=$$(wc -c < $$f); echo "$$f $$s bytes"; [ $$s -lt 1000000 ] || { echo "$$f is over 1 MB"; exit 1; }; done

bench: build/vsim_720p/vsim build/vsim_small/vsim
	./build/vsim_720p/vsim bench 30
	./build/vsim_small/vsim bench 300

# ----------------------------------------------------------------- setup
venv:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q -r requirements-dev.txt

clean:
	rm -rf build
