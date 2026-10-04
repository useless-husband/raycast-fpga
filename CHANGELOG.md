# Changelog

## 0.1.0 (2026-10-04)

First complete version.

* RTL: CEA-861 720p timing generator, DVI 1.0 TMDS encoder, radix-2 divider,
  debouncer, player FSM with wall sliding, ray engine (DDA, perpendicular
  distance, texture coordinates, shading), frame controller, double-buffered
  column table, five-stage pixel pipeline, Urbana board wrapper (MMCM,
  OSERDESE2, OBUFDS) and pin constraints.
* Golden model in integer Python; procedural texture and map asset generator.
* Tests: model tests, cocotb unit tests for every module (Icarus), Verilator
  full-system tests at 320x180 and 1280x720 with an HDMI-decoding sink.
* Yosys synthesis for the XC7S50 with a resource report.
* Verilator + SDL2 interactive demo, scripted video recorder, macOS
  double-click launcher.

Bugs fixed during development (each now covered by a test):

* ray engine: the ray component in the hit-point multiply was zero-extended
  instead of sign-extended, misaligning textures on some faces.
* video timing: the first frame after reset started one pixel late.
* frame controller: an engine-done pulse in the same cycle as the new-frame
  pulse was lost and the controller waited forever.
