# raycast-fpga：硬體 3D 迷宮

**用 SystemVerilog 寫的《德軍總部 3D》式光線投射（raycasting）繪圖電路，不用畫面緩衝區（framebuffer）就能輸出 1280x720、60 Hz 的 HDMI 畫面，而且每一個位元都和黃金參考模型逐一比對過。**

目標板子是 Real Digital Urbana（Spartan-7 XC7S50），也就是 MIT 6.205（數位系統實驗）上課用的板子。光線投射是 6.205 期末專題的經典題目；這個專案是為了學習而重做一次，不是新點子。它想做好的是周邊的工程：把規格寫成可以執行的模型、用單元測試與系統測試比對每一個位元、讓記憶體架構真的放得進這顆晶片、把每一格畫面的時脈預算量出來而不是用猜的，再加上合成報告。**它還沒有在實體板子上跑過。**

[English](README.md) · [設計說明（英文）](docs/DESIGN.md) · [專題報告（6.205 格式，英文）](docs/report.md) · [初學者導讀](docs/導讀.zh-TW.md)

![由 RTL 畫出來的迷宮行走畫面](docs/media/walk.gif)

*上面每一張畫面，都是 Verilator 模擬出來的 HDMI（TMDS）訊號再解碼回像素的結果，沒有任何一張是用軟體另外畫的。完整解析度的單張畫面：[docs/media/frame.png](docs/media/frame.png)。*

## 裡面有什麼

* **光線引擎**（`rtl/ray_engine.sv`）：螢幕每一行（column）射一條光線，用 DDA 在格子地圖上一格一格走，算出垂直距離（所以沒有魚眼變形），四個除法共用一顆逐位元除法器，再算出貼圖座標、牆面明暗與遠近明暗。全部用定點數，格式寫在 [DESIGN.md](docs/DESIGN.md#4-fixed-point-formats)。
* **不用畫面緩衝區**：引擎每一行只寫 70 個位元到一張「行資料表」（雙緩衝，2 x 1280 筆，只佔晶片區塊記憶體的 6.5%）；五級像素管線在掃描畫面的同時，用這張表加上貼圖 ROM 即時算出每一個像素。
* **HDMI**：CEA-861 的 720p 時序產生器、照 DVI 1.0 規格做 DC 平衡的 TMDS 編碼器，板子上再用 OSERDESE2 做 10:1 序列化、用 MMCM 產生時脈。
* **玩家**：前進、後退、平移、轉向（按鍵有去彈跳），撞牆會沿著牆滑過去。
* **素材**：地圖是一個文字檔（`maps/level1.txt`）；八張 64x64 貼圖由 `tools/gen_assets.py` 用程式產生，沒有用任何遊戲素材。
* **黃金模型**（`model/raycast_model.py`）：用整數 Python 算出和硬體完全一樣的數值，位元寬度、捨入方式都一樣。
* **測試**：模型性質測試、每個模組的 cocotb 單元測試（Icarus）、320x180 與 1280x720 的 Verilator 全系統測試、`verilator -Wall` lint、Yosys 對 XC7S50 合成、GitHub Actions CI。
* **互動展示**：真正的 RTL 在視窗裡跑，用鍵盤操作。

## 快速開始

需要 Verilator 5（測過 5.052）、Icarus Verilog（測過 13.0）、Yosys（合成測過 0.69 與 0.33）、Python 3.10 以上、C++17 編譯器；視窗需要 SDL2，錄影片需要 ffmpeg（macOS：`brew install verilator icarus-verilog yosys sdl2 ffmpeg`）。

```sh
make venv        # 第一次：建立 .venv，裝 cocotb 和 pytest（requirements-dev.txt）
make test        # lint + 模型 + 單元（cocotb/Icarus）+ 系統（Verilator），約 90 秒
make play        # 開視窗玩 720p 的 RTL 模擬（WASD/方向鍵，Esc 離開）
make play-small  # 同一份 RTL 用 320x180 編譯再放大，順很多
make synth       # Yosys synth_xilinx 合成到 XC7S50 -> synth/report.md
make video       # 照腳本走一圈 -> build/media/walk.mp4 與 .gif
make bench       # 量模擬速度
```

在 Mac 上也可以直接雙擊 **`玩玩看.command`**：它會檢查工具、編譯模擬程式，然後打開遊戲視窗。

操作：`W`/`↑` 前進、`S`/`↓` 後退、`←`/`→`（或 `Q`/`E`）轉向、`A`/`D` 平移。想改關卡就編輯 `maps/level1.txt`，執行 `make assets` 再重新編譯。

![互動視窗：標題列即時顯示模擬速度](docs/media/play-window.png)

## 結果

以下數字都在 Apple M5（10 核心，當時同時有其他工作在跑）上量測，工具為 Verilator 5.052、Icarus Verilog 13.0、Yosys 0.69、cocotb 2.1。

### 驗證

`make test` 輸出（節錄）：

```
lint: verilator -Wall clean
17 passed in 63.89s        # tests/model（6）+ tests/unit（11 個 cocotb 編譯、30 個測試案例）
6 passed in 18.83s         # tests/system（Verilator）
```

| 層級 | 比對什麼 | 數量 |
|---|---|---|
| 模型測試 | DDA 最壞步數、時脈預算不等式、玩家不會走進牆裡、ROM 映像可重現 | 6 個測試 |
| TMDS 編碼器 | 每一種可能的 disparity 狀態 x 每一個位元組，對照依 DVI 規格獨立寫的參考實作 | 2,304 種組合 + 60,000 個隨機週期 |
| 光線引擎（單元） | 每一行都和模型逐位元相同，引擎自己的週期計數器也和週期模型相同；真實地圖與隨機地圖 | 3 種解析度共 328 張 |
| 像素管線（單元） | 每一個像素和模型相同，每個週期檢查同步訊號對齊 | 11 張 |
| 全系統 | 玩家狀態、全部 1280（或 320）筆行資料，以及從 TMDS 解碼回來的整張畫面，全部對照模型 | 347,840 筆行資料、285 張畫面、0 個不一致 |

刻意測試的邊界情況：剛好 0/90/180/270 度的視角（光線某個分量剛好是 0）、沿著格線與穿過格點的光線、剛好站在牆面上（距離 0，高度飽和）以及離牆只差一個最小單位。隨機測試用固定種子（`TEST_SEED`，預設 6205），失敗訊息一定會印出種子。開發過程中找到三個真正的 bug：兩個是測試抓到的，一個是自己檢查程式時發現、再補上一個在舊程式上會失敗的測試，列在[報告](docs/report.md#43-bugs-the-tests-found)裡。

### 合成（Yosys `synth_xilinx`，XC7S50）

| 資源 | 使用 | 總量 | 比例 |
|---|---:|---:|---:|
| LUT | 2,293 | 32,600 | 7.0% |
| 正反器 | 1,207 | 65,200 | 1.9% |
| 區塊記憶體（36 Kb） | 13 | 75 | 17.3% |
| DSP48E1 | 19 | 120 | 15.8% |

各區塊明細在 [synth/report.md](synth/report.md)。Yosys 會對應到 7 系列的基本元件，但不做擺放、繞線與時序分析；**這顆晶片目前沒有任何開源流程能給出時序簽核，所以這裡不宣稱任何最高頻率。**

### 時脈預算

720p 每一格畫面有 1,237,500 個 74.25 MHz 時脈週期。引擎每一行剛好花 `141 + 2 x（DDA 步數）` 個週期，測試會在每一張畫面用引擎自己的計數器驗證這個公式。在四周封閉的 32x32 地圖裡，一條光線最多走 59 步，所以最壞的一格畫面要 331,520 個週期（26.8%）；測試裡遇到最慢的一格是 215,098（17.4%）。從來沒有掉格。

### 模擬速度（`make bench`）

| 版本 | 每秒畫面 | 模擬時脈 | 相對真實速度 |
|---|---:|---:|---:|
| 1280x720 | 11.9 | 14.7 MHz | 19.9% |
| 320x180 | 156 | 14.3 MHz | 不適用（不是真的影像模式） |

## 運作方式

![方塊圖](docs/block_diagram.svg)

1. 每一格畫面，`frame_ctrl` 先讓玩家移動一次（先轉向，再走 x、再走 y，每一步都查地圖），再啟動光線引擎。
2. 引擎對 1280 行逐一算出光線方向，在格子上一格一格走到撞牆，算出牆在螢幕上的高度和貼圖的哪一行，把一筆 70 位元的資料寫進行資料表的「背面」那一半。
3. 同時，像素管線掃描「正面」那一半：像素 `(x, y)` 讀第 `x` 筆，判斷是牆、天花板還是地板，算出貼圖的列，讀出貼圖顏色編號與調色盤顏色，套上明暗，再交給 TMDS 編碼器。
4. 這一格畫面的可見部分結束時，兩半交換。

設計理由（為什麼不用畫面緩衝區、為什麼只用一個時脈、定點數格式與溢位論證、魚眼修正、考慮過但放棄的做法）都在 [docs/DESIGN.md](docs/DESIGN.md)。

## 限制

* **沒有在硬體上跑過。** 沒有 bitstream：開源工具在這裡只能做到合成，也沒有用 Vivado。腳位檔 `synth/urbana.xdc` 是從 Real Digital 公開的 Urbana 約束檔複製來的（並修正原檔兩個錯字），但還沒有經過 Vivado。
* 沒有時序分析（見上）。
* 互動展示在 720p 大約每秒 12 張，因為它模擬硬體的每一個時脈週期；`play-small` 比較順。
* 只有一張編譯時就固定的 32x32 地圖；沒有地板與天花板貼圖、角色圖、門或小地圖。
* 板子上的操作受限於 Urbana 的四個按鍵和開關（btn[0] 重置、btn[1] 前進，sw[0] 打開時變後退、btn[2]/btn[3] 轉向，sw[1] 打開時變平移）。

## 相關作品

光線投射是很常見的 FPGA 專題，本專案不宣稱是新點子。比較過的作品：

* **MazeCaster**（T. Hagenlocker、C. Hu、H. Hussein，MIT 6.205，2024 秋）：同一塊 Urbana 板子，平行的 DDA 單元、8.8 定點數，以及兩個四分之一 720p、8 位元色彩的畫面緩衝區，用光了整顆晶片 2.7 Mbit 的區塊記憶體。本專案主要的不同是用行資料表取代畫面緩衝區，因此能用 17% 的區塊記憶體輸出完整 720p、24 位元色彩，另外加上以模型為基準的逐位元測試。
* 6.205 的[期末專題清單](https://fpga.mit.edu/6205/F25/final_project_archive)還有其他繪圖專題，例如 "Voxel Ray Tracer"（2024）、"Poor Man's VR: Raymarching with Stereoscopic Offset and Gyroscopic Control"（2023）、"FPGA Fractal Ray Marcher" 與 "REND3R"（2022）、"FPGA Ray Tracer"（2019）。
* [dormando/verilog-raycaster](https://github.com/dormando/verilog-raycaster)：用 Verilog 寫的光線投射，接 320x240 SPI 液晶，畫上一行的同時投射下一條光線。
* [mayawarrier/raycast-3D-CycloneFPGA](https://github.com/mayawarrier/raycast-3D-CycloneFPGA)：Cyclone V 上的德軍總部式光線投射，每格畫 160 條。
* [dataflowg/fpga-raycaster](https://github.com/dataflowg/fpga-raycaster)：用 LabVIEW FPGA 寫的光線投射，平行繪製多行。
* 哥倫比亞大學 CSEE 4840（2022 春）的 "Lightspeed" 專題：同一系譜的 FPGA 光線投射。
* 演算法本身照 Lode Vandevenne 的[光線投射教學](https://lodev.org/cgtutor/raycasting.html)（DDA、`deltaDist = 1/|ray|`、垂直距離），改成定點數，並配合「列往下增加」的地圖座標。

`video_sig_gen`、`tmds_encoder` 等模組介面沿用 6.205 實驗課的慣例；本 repo 的程式碼是我自己寫的。

## 目錄結構

```
maps/level1.txt          關卡（改完執行 `make assets`）
tools/gen_assets.py      地圖 -> ROM 映像、程式產生的貼圖、調色盤、sin/cos 表
model/raycast_model.py   逐位元一致的黃金模型（也就是規格）
rtl/                     可攜式 SystemVerilog（有模擬、有測試）
rtl/board/               Urbana 外殼：MMCM、OSERDESE2、OBUFDS（只合成）
rtl/mem/                 用 $readmemh 載入的 ROM 映像（自動產生）
sim/sim_main.cpp         Verilator 測試架：TMDS 解碼接收端，play/record/bench/run 模式
tests/model              模型與素材測試
tests/unit               cocotb 單元測試（Icarus）
tests/system             Verilator 全系統測試
synth/                   Yosys 腳本、資源報告、Urbana 腳位約束
docs/                    設計說明、專題報告、初學者導讀、方塊圖、圖片
玩玩看.command            macOS 雙擊啟動
```

## 授權

MIT，見 [LICENSE](LICENSE)。
