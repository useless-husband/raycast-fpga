#!/bin/bash
# ============================================================
#  玩玩看：在 Mac 上直接玩「硬體 3D 迷宮」
#
#  這個檔案在 Finder 裡雙擊就會打開「終端機」來執行。
#  它會做兩件事：
#    1. 用 Verilator 把 rtl/ 資料夾裡的硬體電路（SystemVerilog）
#       翻譯成 C++ 再編譯成一個模擬程式（第一次大約 10 秒）。
#    2. 打開一個視窗，畫面上的每一個像素都是「模擬出來的硬體電路」
#       送到 HDMI 線上的訊號，再解碼回來的結果，不是另外用軟體畫的。
#
#  操作方式：
#    W / ↑ 前進    S / ↓ 後退    ← → 轉向（也可以用 Q E）
#    A / D 左右平移              Esc 離開
#
#  需要先裝好：Xcode Command Line Tools、verilator、sdl2
#    （brew install verilator sdl2）
# ============================================================

# 先切換到這個檔案所在的資料夾。資料夾名稱有空白和中文，
# 所以 "$(dirname "$0")" 一定要用雙引號包起來。
cd "$(dirname "$0")" || exit 1

# 檢查需要的工具有沒有裝
for tool in verilator sdl2-config make c++; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "找不到 $tool。請先在終端機執行：brew install verilator sdl2"
    echo "（c++ 和 make 來自 Xcode Command Line Tools：xcode-select --install）"
    read -r -p "按 Enter 關閉視窗..."
    exit 1
  fi
done

echo "要用哪一種解析度？"
echo "  直接按 Enter：1280x720（真正的 720p 電路，模擬約每秒 11 張畫面）"
echo "  輸入 s 再按 Enter：320x180 放大 4 倍（同一份電路的縮小版，比較順）"
read -r choice

if [ "$choice" = "s" ] || [ "$choice" = "S" ]; then
  target=build/vsim_small/vsim
  scale=4
else
  target=build/vsim_720p/vsim
  scale=1
fi

echo "編譯模擬程式中（只有第一次或改過電路才會花時間）..."
if ! make "$target"; then
  echo "編譯失敗，上面的訊息會說明原因。"
  read -r -p "按 Enter 關閉視窗..."
  exit 1
fi

echo "開始！視窗標題會顯示模擬速度。按 Esc 結束。"
./"$target" play "$scale"
