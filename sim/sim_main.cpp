// Verilator harness for raycast_system.
//
// Every pixel this program shows or saves is recovered by decoding the
// RTL's three 10-bit TMDS channels, the way an HDMI sink would: control
// tokens give hsync/vsync, data symbols give the pixel bytes.  Each symbol is
// also checked against the pre-encoder pixel (dbg_rgb) of the previous clock.
//
// Modes
//   run <scenario> <outdir>   headless; scenario lines are applied one per
//                             frame update ("L x y angle" teleports,
//                             "B mask" holds buttons; a trailing C captures
//                             the frame that shows that update).  Writes
//                             tables.bin, frames.bin, summary.json.
//   record <script> <out.rgb> scripted walk ("<updates> <mask>" lines), raw
//                             RGB24 frames for ffmpeg
//   bench <frames>            simulation speed
//   play [scale]              SDL window, keyboard drives the buttons
//
// Button mask bits: 0 fwd, 1 back, 2 turn left, 3 turn right,
//                   4 strafe left, 5 strafe right.

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "Vraycast_system.h"
#include "verilated.h"

#ifdef WITH_SDL
#include <SDL.h>
#endif

#ifndef SIM_W
#define SIM_W 1280
#endif
#ifndef SIM_H
#define SIM_H 720
#endif

// Legacy Verilator time hook (unused: the context keeps its own time).
double sc_time_stamp() { return 0; }

static const int W = SIM_W;
static const int H = SIM_H;

// ---------------------------------------------------------------- TMDS decode
struct Sym {
  bool ctrl;
  uint8_t value;  // data byte, or {C1, C0} for a control token
};
static Sym tmds_lut[1024];

static void build_tmds_lut() {
  const uint16_t tokens[4] = {0x354, 0x0AB, 0x154, 0x2AB};  // 00, 01, 10, 11
  for (int s = 0; s < 1024; s++) {
    tmds_lut[s] = {false, 0};
    bool is_ctrl = false;
    for (int c = 0; c < 4; c++)
      if (s == tokens[c]) {
        tmds_lut[s] = {true, (uint8_t)c};
        is_ctrl = true;
      }
    if (is_ctrl) continue;
    uint8_t q = s & 0xFF;
    if (s & 0x200) q = ~q;  // bit 9: data was inverted
    uint8_t d = q & 1;
    for (int i = 1; i < 8; i++) {
      int b = ((q >> i) ^ (q >> (i - 1))) & 1;  // XOR chain
      if (!(s & 0x100)) b ^= 1;                // bit 8 clear: XNOR chain
      d |= b << i;
    }
    tmds_lut[s].value = d;
  }
}

// --------------------------------------------------------------- simulation
struct Table {
  int32_t x = 0, y = 0, angle = 0, cycles = 0;
  std::vector<uint8_t> cols;  // W x 9 bytes (70-bit words, little endian)
  int written = 0;
};

struct Sim {
  std::unique_ptr<VerilatedContext> ctx;
  std::unique_ptr<Vraycast_system> top;
  uint64_t cycles = 0;

  // previous-cycle pre-encoder outputs, for the TMDS cross-check
  uint32_t prev_rgb = 0;
  int prev_de = 0, prev_hs = 0, prev_vs = 0;
  uint64_t tmds_errors = 0;
  bool armed = false;
  int disparity[3] = {0, 0, 0};
  int worst_disparity = 0;

  // decoded video stream
  std::vector<uint8_t> frame = std::vector<uint8_t>(W * H * 3);
  long pix = -1;  // -1: waiting for the first vsync
  int prev_dec_vs = 0;
  bool frame_ready = false;

  // engine bookkeeping
  std::vector<Table> tables;
  int front_table = -1;      // table shown by the frame being scanned now
  int scan_table = -1;       // table that was in front when this frame began
  int engine_started = 0;    // number of engine starts
  int engine_done = 0;
  uint32_t max_engine_cycles = 0;

  Sim(int argc, char** argv) : ctx(new VerilatedContext), top(nullptr) {
    ctx->commandArgs(argc, argv);
    top.reset(new Vraycast_system(ctx.get()));
    build_tmds_lut();
  }

  void reset() {
    top->rst_in = 1;
    for (int i = 0; i < 8; i++) tick_raw();
    top->rst_in = 0;
  }

  void tick_raw() {
    top->clk_in = 0;
    top->eval();
    top->clk_in = 1;
    top->eval();
    cycles++;
  }

  // one clock plus all monitoring; returns true when a frame just completed
  bool tick() {
    tick_raw();
    frame_ready = false;
    monitor_engine();
    monitor_tmds();
    return frame_ready;
  }

  void monitor_engine() {
    if (top->dbg_engine_start_out) {
      Table t;
      t.x = top->dbg_pos_x_out;
      t.y = top->dbg_pos_y_out;
      t.angle = top->dbg_angle_out;
      t.cols.assign(W * 9, 0);
      tables.push_back(std::move(t));
      engine_started++;
    }
    if (top->dbg_col_we_out && !tables.empty()) {
      Table& t = tables.back();
      int x = top->dbg_col_x_out;
      const uint32_t* w = top->dbg_col_data_out.data();
      uint8_t* dst = &t.cols[x * 9];
      for (int i = 0; i < 9; i++) dst[i] = (w[i / 4] >> (8 * (i % 4))) & 0xFF;
      t.written++;
    }
    if (top->dbg_engine_done_out && !tables.empty()) {
      tables.back().cycles = top->dbg_engine_cycles_out;
      if ((uint32_t)top->dbg_engine_cycles_out > max_engine_cycles)
        max_engine_cycles = top->dbg_engine_cycles_out;
      engine_done++;
    }
    if (top->dbg_swap_out) front_table = engine_done - 1;
  }

  void monitor_tmds() {
    const uint16_t sym[3] = {(uint16_t)top->tmds_red_out, (uint16_t)top->tmds_green_out,
                             (uint16_t)top->tmds_blue_out};
    Sym r = tmds_lut[sym[0]], g = tmds_lut[sym[1]], b = tmds_lut[sym[2]];

    // cross-check against what entered the encoders one clock earlier;
    // armed at the first blanking symbol, where the encoders' disparity
    // counters are known to be zero
    if (!armed && cycles > 16 && !prev_de) armed = true;
    if (armed) {
      if (prev_de) {
        uint32_t rgb = (r.value << 16) | (g.value << 8) | b.value;
        if (r.ctrl || g.ctrl || b.ctrl || rgb != prev_rgb) {
          if (getenv("TMDS_DEBUG") && tmds_errors < 5)
            fprintf(stderr, "cyc %llu data: sym %03x %03x %03x dec %06x exp %06x ctrl %d%d%d\n",
                    (unsigned long long)cycles, sym[0], sym[1], sym[2], rgb, prev_rgb, r.ctrl, g.ctrl, b.ctrl);
          tmds_errors++;
        }
        for (int c = 0; c < 3; c++) {
          int ones = __builtin_popcount(sym[c]);
          disparity[c] += ones - (10 - ones);
          int a = disparity[c] < 0 ? -disparity[c] : disparity[c];
          if (a > worst_disparity) worst_disparity = a;
          if (a > 8) {
            if (getenv("TMDS_DEBUG") && tmds_errors < 5)
              fprintf(stderr, "cyc %llu disparity ch%d %d\n", (unsigned long long)cycles, c, disparity[c]);
            tmds_errors++;
          }
        }
      } else {
        if (!r.ctrl || !g.ctrl || !b.ctrl || r.value != 0 || g.value != 0 ||
            b.value != ((prev_vs << 1) | prev_hs))
          tmds_errors++;
        disparity[0] = disparity[1] = disparity[2] = 0;
      }
    }
    prev_rgb = top->dbg_rgb_out;
    prev_de = top->dbg_de_out;
    prev_hs = top->dbg_hs_out;
    prev_vs = top->dbg_vs_out;

    // sink side: rebuild frames from the decoded stream only
    int dec_vs = b.ctrl ? (b.value >> 1) & 1 : prev_dec_vs;
    if (dec_vs && !prev_dec_vs) {
      pix = 0;  // vsync rising edge: the next data symbol starts a frame
      scan_table = -2;
    }
    prev_dec_vs = dec_vs;
    if (!b.ctrl && pix >= 0 && pix < (long)W * H) {
      if (pix == 0) scan_table = front_table;
      uint8_t* p = &frame[pix * 3];
      p[0] = r.value;
      p[1] = g.value;
      p[2] = b.value;
      if (++pix == (long)W * H) frame_ready = true;
    }
  }

  void set_buttons(int mask) { top->btn_in = mask & 0x3F; }
  void set_load(bool on, int x = 0, int y = 0, int a = 0) {
    top->dbg_load_in = on;
    top->dbg_x_in = x;
    top->dbg_y_in = y;
    top->dbg_angle_in = a;
  }
};

static double now_s() {
  using namespace std::chrono;
  return duration<double>(steady_clock::now().time_since_epoch()).count();
}

// ------------------------------------------------------------------ run mode
struct Entry {
  bool load = false;
  int x = 0, y = 0, a = 0, mask = 0;
  bool capture = false;
};

static std::vector<Entry> read_scenario(const char* path) {
  std::vector<Entry> v;
  std::ifstream f(path);
  if (!f) {
    fprintf(stderr, "cannot open %s\n", path);
    exit(2);
  }
  std::string line;
  while (std::getline(f, line)) {
    if (line.empty() || line[0] == '#') continue;
    std::istringstream is(line);
    std::string op, flag;
    Entry e;
    is >> op;
    if (op == "L") {
      e.load = true;
      is >> e.x >> e.y >> e.a;
    } else if (op == "B") {
      is >> e.mask;
    } else {
      fprintf(stderr, "bad scenario line: %s\n", line.c_str());
      exit(2);
    }
    if (is >> flag && flag == "C") e.capture = true;
    v.push_back(e);
  }
  return v;
}

static void apply(Sim& s, const Entry* e) {
  if (!e) {
    s.set_load(false);
    s.set_buttons(0);
  } else if (e->load) {
    s.set_load(true, e->x, e->y, e->a);
    s.set_buttons(0);
  } else {
    s.set_load(false);
    s.set_buttons(e->mask);
  }
}

static void write_u32(FILE* f, uint32_t v) { fwrite(&v, 4, 1, f); }

static int run_mode(int argc, char** argv, const char* scen, const std::string& out) {
  std::vector<Entry> entries = read_scenario(scen);
  if (entries.empty()) return 2;
  Sim s(argc, argv);
  apply(s, &entries[0]);
  s.reset();
  std::vector<std::pair<int, std::vector<uint8_t>>> captured;
  int wanted = 0;
  for (auto& e : entries) wanted += e.capture;
  int last_started = 0;
  bool shown_last = false;
  double t0 = now_s();
  uint64_t limit = (uint64_t)(entries.size() + 4) * 3000000ULL;
  while (!shown_last && s.cycles < limit) {
    bool fr = s.tick();
    if (s.engine_started != last_started) {
      last_started = s.engine_started;
      int next = s.engine_started;  // entry for the next update
      apply(s, next < (int)entries.size() ? &entries[next] : nullptr);
    }
    if (fr && s.scan_table >= 0) {
      int t = s.scan_table;
      if (t < (int)entries.size() && entries[t].capture) {
        bool dup = false;
        for (auto& c : captured) dup |= c.first == t;
        if (!dup) captured.emplace_back(t, s.frame);
      }
      if (t == (int)entries.size() - 1) shown_last = true;
    }
  }
  double dt = now_s() - t0;
  if (!shown_last) {
    fprintf(stderr, "timeout: %d tables done, front=%d\n", s.engine_done, s.front_table);
    return 1;
  }

  FILE* f = fopen((out + "/tables.bin").c_str(), "wb");
  fwrite("RCT1", 4, 1, f);
  write_u32(f, W);
  write_u32(f, H);
  int n = (int)entries.size();
  write_u32(f, n);
  for (int i = 0; i < n; i++) {
    Table& t = s.tables[i];
    write_u32(f, t.x);
    write_u32(f, t.y);
    write_u32(f, t.angle);
    write_u32(f, t.cycles);
    write_u32(f, t.written);
    fwrite(t.cols.data(), 1, t.cols.size(), f);
  }
  fclose(f);
  f = fopen((out + "/frames.bin").c_str(), "wb");
  fwrite("RCF1", 4, 1, f);
  write_u32(f, W);
  write_u32(f, H);
  write_u32(f, captured.size());
  for (auto& c : captured) {
    write_u32(f, c.first);
    fwrite(c.second.data(), 1, c.second.size(), f);
  }
  fclose(f);
  f = fopen((out + "/summary.json").c_str(), "w");
  fprintf(f,
          "{\"w\": %d, \"h\": %d, \"cycles\": %llu, \"seconds\": %.3f, \"tables\": %d, "
          "\"captured\": %zu, \"wanted\": %d, \"drops\": %d, \"tmds_errors\": %llu, "
          "\"worst_disparity\": %d, \"max_engine_cycles\": %u}\n",
          W, H, (unsigned long long)s.cycles, dt, s.engine_done, captured.size(), wanted,
          (int)s.top->dbg_drops_out, (unsigned long long)s.tmds_errors, s.worst_disparity,
          s.max_engine_cycles);
  fclose(f);
  printf("ran %llu cycles in %.2f s (%.1f M cycles/s), %d tables, %zu frames captured, "
         "tmds errors %llu, drops %d\n",
         (unsigned long long)s.cycles, dt, s.cycles / dt / 1e6, s.engine_done, captured.size(),
         (unsigned long long)s.tmds_errors, (int)s.top->dbg_drops_out);
  return 0;
}

// --------------------------------------------------------- record and bench
static std::vector<int> read_script(const char* path) {
  std::vector<int> masks;
  std::ifstream f(path);
  if (!f) {
    fprintf(stderr, "cannot open %s\n", path);
    exit(2);
  }
  std::string line;
  while (std::getline(f, line)) {
    if (line.empty() || line[0] == '#') continue;
    std::istringstream is(line);
    int n = 0, mask = 0;
    is >> n >> mask;
    for (int i = 0; i < n; i++) masks.push_back(mask);
  }
  return masks;
}

// Hold `masks[k]` for the update that produces table k and emit every frame
// that shows a new table.  Buttons pass through the real debouncer, so a
// mask is applied right after the previous engine start (a frame early).
static int record_mode(int argc, char** argv, const char* script, const char* out) {
  std::vector<int> masks = read_script(script);
  Sim s(argc, argv);
  s.set_load(false);
  s.set_buttons(0);
  s.reset();
  FILE* f = strcmp(out, "-") == 0 ? stdout : fopen(out, "wb");
  int last_started = 0, last_shown = -1, written = 0;
  double t0 = now_s();
  uint64_t limit = (uint64_t)(masks.size() + 4) * 3000000ULL;
  while (last_shown < (int)masks.size() - 1) {
    if (s.cycles > limit) {
      fprintf(stderr, "record: timeout after %d frames\n", written);
      if (f != stdout) fclose(f);
      return 1;
    }
    bool fr = s.tick();
    if (s.engine_started != last_started) {
      last_started = s.engine_started;
      s.set_buttons(last_started < (int)masks.size() ? masks[last_started] : 0);
    }
    if (fr && s.scan_table > last_shown) {
      last_shown = s.scan_table;
      fwrite(s.frame.data(), 1, s.frame.size(), f);
      written++;
    }
  }
  if (f != stdout) fclose(f);
  double dt = now_s() - t0;
  fprintf(stderr, "recorded %d frames, %.1f s, %.2f fps, tmds errors %llu, drops %d\n", written,
          dt, written / dt, (unsigned long long)s.tmds_errors, (int)s.top->dbg_drops_out);
  return s.tmds_errors ? 1 : 0;
}

static int bench_mode(int argc, char** argv, int frames) {
  Sim s(argc, argv);
  s.reset();
  s.set_buttons(1 | 8);  // walk forward while turning: keeps the engine busy
  int got = 0;
  for (int i = 0; i < 2 * 2000000 && got < 1; i++) got += s.tick();  // warm up one frame
  double t0 = now_s();
  uint64_t c0 = s.cycles;
  got = 0;
  while (got < frames) got += s.tick();
  double dt = now_s() - t0;
  double mhz = (s.cycles - c0) / dt / 1e6;
  printf("{\"w\": %d, \"h\": %d, \"frames\": %d, \"seconds\": %.3f, \"fps\": %.2f, "
         "\"mcycles_per_s\": %.2f, \"realtime_fraction\": %.4f, \"max_engine_cycles\": %u, "
         "\"tmds_errors\": %llu, \"drops\": %d}\n",
         W, H, frames, dt, frames / dt, mhz, mhz / 74.25, s.max_engine_cycles,
         (unsigned long long)s.tmds_errors, (int)s.top->dbg_drops_out);
  return 0;
}

// ---------------------------------------------------------------- play mode
#ifdef WITH_SDL
static int play_mode(int argc, char** argv, int scale) {
  if (SDL_Init(SDL_INIT_VIDEO) != 0) {
    fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
    return 1;
  }
  SDL_Window* win = SDL_CreateWindow("raycast-fpga", SDL_WINDOWPOS_CENTERED,
                                     SDL_WINDOWPOS_CENTERED, W * scale, H * scale,
                                     SDL_WINDOW_ALLOW_HIGHDPI);
  SDL_Renderer* ren = SDL_CreateRenderer(win, -1, SDL_RENDERER_ACCELERATED);
  SDL_Texture* tex =
      SDL_CreateTexture(ren, SDL_PIXELFORMAT_RGB24, SDL_TEXTUREACCESS_STREAMING, W, H);
  Sim s(argc, argv);
  s.reset();
  bool quit = false;
  // RAYCAST_PLAY_FRAMES=n: quit by itself after n frames (smoke tests)
  const char* auto_env = getenv("RAYCAST_PLAY_FRAMES");
  long auto_quit = auto_env ? atol(auto_env) : 0, shown = 0;
  double t_start = now_s();
  uint64_t c_start = 0;
  double t_last = now_s();
  uint64_t c_last = 0;
  int frames = 0;
  while (!quit) {
    SDL_Event ev;
    while (SDL_PollEvent(&ev)) {
      if (ev.type == SDL_QUIT) quit = true;
      if (ev.type == SDL_KEYDOWN && ev.key.keysym.sym == SDLK_ESCAPE) quit = true;
    }
    const Uint8* k = SDL_GetKeyboardState(nullptr);
    int mask = 0;
    if (k[SDL_SCANCODE_W] || k[SDL_SCANCODE_UP]) mask |= 1;
    if (k[SDL_SCANCODE_S] || k[SDL_SCANCODE_DOWN]) mask |= 2;
    if (k[SDL_SCANCODE_LEFT] || k[SDL_SCANCODE_Q]) mask |= 4;
    if (k[SDL_SCANCODE_RIGHT] || k[SDL_SCANCODE_E]) mask |= 8;
    if (k[SDL_SCANCODE_A]) mask |= 16;
    if (k[SDL_SCANCODE_D]) mask |= 32;
    s.set_buttons(mask);
    while (!s.tick()) {
    }
    frames++;
    SDL_UpdateTexture(tex, nullptr, s.frame.data(), W * 3);
    SDL_RenderClear(ren);
    SDL_RenderCopy(ren, tex, nullptr, nullptr);
    SDL_RenderPresent(ren);
    if (auto_quit && ++shown >= auto_quit) quit = true;
    double t = now_s();
    if (t - t_last > 0.5) {
      double fps = frames / (t - t_last);
      double mhz = (s.cycles - c_last) / (t - t_last) / 1e6;
      char title[200];
      snprintf(title, sizeof title,
               "raycast-fpga RTL  %dx%d  %.1f fps  %.1f M cycles/s (%.0f%% of 74.25 MHz)  "
               "TMDS errors %llu   WASD + arrows, Esc quits",
               W, H, fps, mhz, 100 * mhz / 74.25, (unsigned long long)s.tmds_errors);
      SDL_SetWindowTitle(win, title);
      t_last = t;
      c_last = s.cycles;
      frames = 0;
    }
  }
  double dt = now_s() - t_start;
  printf("play: %ld frames in %.1f s, %.2f fps, %.1f M cycles/s, tmds errors %llu\n", shown, dt,
         shown / dt, (s.cycles - c_start) / dt / 1e6, (unsigned long long)s.tmds_errors);
  SDL_DestroyTexture(tex);
  SDL_DestroyRenderer(ren);
  SDL_DestroyWindow(win);
  SDL_Quit();
  return 0;
}
#endif

int main(int argc, char** argv) {
  if (argc >= 4 && !strcmp(argv[1], "run")) return run_mode(argc, argv, argv[2], argv[3]);
  if (argc >= 4 && !strcmp(argv[1], "record")) return record_mode(argc, argv, argv[2], argv[3]);
  if (argc >= 3 && !strcmp(argv[1], "bench")) return bench_mode(argc, argv, atoi(argv[2]));
#ifdef WITH_SDL
  if (argc >= 2 && !strcmp(argv[1], "play"))
    return play_mode(argc, argv, argc >= 3 ? atoi(argv[2]) : 1);
#endif
  fprintf(stderr,
          "usage: %s run <scenario> <outdir> | record <script> <out.rgb|-> | bench <frames>"
#ifdef WITH_SDL
          " | play [scale]"
#endif
          "\n",
          argv[0]);
  return 2;
}
