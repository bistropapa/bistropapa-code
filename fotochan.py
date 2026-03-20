"""
ビストロパパ料理写真管理ツール「フォトちゃん」v2.0.0
=======================================================
【ステップ0】SDカード取り込み & 品質選別
  - フォルダから写真を取得、品質フィルタ（ブレ・暗さ）
  - 選別ビューワー（←→/Enter/Delete）
  - 料理名グルーピング・AI自動判定
  - ファイル名を「料理名_縦or横_日付_連番.jpg」にリネーム

【ステップ1】Canva修正後の一括リネーム処理
  - Canvaでダウンロードしたフォルダを指定
  - 料理名を入力してリネーム
  - 「料理名_縦or横_ロゴ付き_日付_連番.jpg」で保存
"""

import os
import sys
import json
import base64
import datetime
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageTk, ExifTags
    import requests
except ImportError as e:
    print(f"必要なライブラリが不足しています: {e}")
    print("pip install opencv-python pillow requests numpy を実行してください。")
    input("Enterキーで終了...")
    sys.exit(1)

# =============================================================================
# 設定
# =============================================================================
CONFIG_FILE  = Path(__file__).parent / "fotochan_config.json"
UNDO_LOG_FILE = Path(__file__).parent / "undo_log.json"

# =============================================================================
# Undo ログ管理
# =============================================================================
def save_undo_log(entries):
    """
    entries: list of dict
      step0(リネーム):  {"type": "rename", "new": str, "original": str, "folder": str}
      step1(移動):      {"type": "move",   "new": str, "original_name": str,
                         "src_folder": str, "dest_folder": str}
    """
    with open(UNDO_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f, ensure_ascii=False, indent=2)

def load_undo_log():
    if UNDO_LOG_FILE.exists():
        with open(UNDO_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("entries", [])
    return []

def clear_undo_log():
    if UNDO_LOG_FILE.exists():
        UNDO_LOG_FILE.unlink()

def execute_undo(entries):
    """
    undo_logのentriesを逆順に処理して元に戻す。
    成功・失敗メッセージのリストを返す。
    """
    import shutil
    messages = []
    for e in reversed(entries):
        try:
            if e["type"] == "rename":
                # 新ファイル名 → 元ファイル名 にリネーム（同フォルダ内）
                new_path  = Path(e["folder"]) / e["new"]
                orig_path = Path(e["folder"]) / e["original"]
                if new_path.exists():
                    new_path.rename(orig_path)
                    messages.append(f"✅ {e['new']} → {e['original']}")
                else:
                    messages.append(f"⚠️ ファイルが見つかりません: {e['new']}")
            elif e["type"] == "move":
                # 保存先 → 元フォルダ に移動してファイル名を元に戻す
                src  = Path(e["dest_folder"]) / e["new"]
                dest = Path(e["src_folder"])  / e["original_name"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                if src.exists():
                    shutil.move(str(src), str(dest))
                    messages.append(f"✅ {e['new']} → {e['original_name']}")
                else:
                    messages.append(f"⚠️ ファイルが見つかりません: {e['new']}")
        except Exception as ex:
            messages.append(f"❌ エラー: {ex}")
    return messages

DEFAULT_CONFIG = {
    "anthropic_api_key": "",
    "canva_api_key": "",
    "step0_output_folder": str(Path.home() / "OneDrive" / "画像" / "カメラのインポート"),
    "step1_output_folder": str(Path.home() / "Desktop" / "bistropapa_step1"),
    "templates": {
        "横": [{"name": "BistroPapa横クレジット13", "design_id": "DAG82rf3QdU", "count": 0}],
        "縦": [{"name": "BistroPapaクレジット縦9",  "design_id": "DAHBJ3vaBZs", "count": 0}]
    },
    "ai_concurrent": 3,
    "step1_suffix": "ロゴ付き"
}

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            if k not in cfg:
                cfg[k] = v
        return cfg
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# =============================================================================
# 画像ユーティリティ
# =============================================================================
def imread_jp(p):
    try:
        arr = np.fromfile(str(p), dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None

def imread_jp_gray(p):
    try:
        arr = np.fromfile(str(p), dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    except Exception:
        return None

def open_image_corrected(p):
    """EXIF回転を自動補正して開く"""
    with open(p, 'rb') as f:
        img = Image.open(f)
        img.load()
    img = img.copy()
    try:
        exif = img._getexif()
        if exif:
            for tag, val in exif.items():
                if ExifTags.TAGS.get(tag) == "Orientation":
                    rotations = {3: 180, 6: 270, 8: 90}
                    if val in rotations:
                        img = img.rotate(rotations[val], expand=True)
                    break
    except Exception:
        pass
    return img

def get_orientation(p, rotation=0):
    try:
        img = open_image_corrected(p)
        if rotation:
            img = img.rotate(rotation, expand=True)
        w, h = img.size
        return "横" if w >= h else "縦"
    except Exception:
        return "横"

def detect_dish_name(p, api_key):
    try:
        with open(p, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        mt = "image/jpeg" if Path(p).suffix.lower() in (".jpg", ".jpeg") else "image/png"
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-opus-4-5", "max_tokens": 100,
                  "messages": [{"role": "user", "content": [
                      {"type": "image", "source": {"type": "base64", "media_type": mt, "data": data}},
                      {"type": "text", "text": "この料理写真の料理名を日本語で答えてください。料理名だけを返してください（例：肉じゃが、唐揚げ）。不明な場合は「料理」と返してください。"}
                  ]}]}, timeout=30)
        if resp.status_code == 200:
            return resp.json()["content"][0]["text"].strip()
    except Exception:
        pass
    return "料理"

def generate_filename(dish, orient, date_str, out_folder, suffix=""):
    Path(out_folder).mkdir(parents=True, exist_ok=True)
    base = f"{dish}_{orient}"
    if suffix:
        base += f"_{suffix}"
    base += f"_{date_str}"
    i = 1
    while True:
        fn = f"{base}_{i:03d}.jpg"
        if not (Path(out_folder) / fn).exists():
            return fn
        i += 1

# =============================================================================
# 共通ウィジェット
# =============================================================================
class ProgressDialog:
    def __init__(self, parent, title, total):
        self.win = tk.Toplevel(parent)
        self.win.title(title)
        self.win.geometry("440x160")
        self.win.configure(bg="#2C3E50")
        self.win.resizable(False, False)
        self.win.grab_set()
        self.cancelled = False
        self.total = max(total, 1)
        self.lv = tk.StringVar(value="処理中...")
        tk.Label(self.win, textvariable=self.lv, bg="#2C3E50", fg="white",
                 font=("Meiryo", 10)).pack(pady=(18, 4))
        self.bar = ttk.Progressbar(self.win, length=380, mode="determinate",
                                   maximum=self.total)
        self.bar.pack(padx=20, pady=4)
        self.cv = tk.StringVar(value=f"0 / {self.total}")
        tk.Label(self.win, textvariable=self.cv, bg="#2C3E50", fg="#ECF0F1",
                 font=("Meiryo", 9)).pack()
        tk.Button(self.win, text="キャンセル", command=self._cancel,
                  bg="#E63946", fg="white", font=("Meiryo", 9),
                  relief=tk.FLAT, padx=12).pack(pady=8)

    def _cancel(self):
        self.cancelled = True
        if self.win.winfo_exists():
            self.win.destroy()

    def update(self, n, msg=""):
        if not self.win.winfo_exists():
            return
        self.bar["value"] = n
        self.cv.set(f"{n} / {self.total}")
        if msg:
            self.lv.set(msg)
        self.win.update()

    def close(self):
        if self.win.winfo_exists():
            self.win.destroy()


# =============================================================================
# ランチャー（トップ画面）
# =============================================================================
class LauncherApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ビストロパパ料理写真管理ツール「フォトちゃん」v2.0.0")
        self.root.geometry("900x680")
        self.root.configure(bg="#1E2A3A")
        self.config = load_config()
        self._build()

    def _build(self):
        # ── タイトル ──
        tk.Label(self.root,
                 text="🔍  ビストロパパ料理写真管理ツール「フォトちゃん」",
                 font=("Meiryo", 16, "bold"), bg="#C41E3A", fg="white",
                 pady=14).pack(fill=tk.X)

        tk.Label(self.root, text="2つのステップで料理写真を管理します",
                 font=("Meiryo", 10), bg="#1E2A3A", fg="#8899AA",
                 pady=8).pack()

        body = tk.Frame(self.root, bg="#1E2A3A")
        body.pack(fill=tk.BOTH, expand=True, padx=30, pady=10)

        # ── ステップ0カード ──
        self._step_card(
            parent=body,
            header_text="【ステップ０】SDカード取り込み ＆ 品質選別  ★ NEW",
            header_bg="#7B2FBE",
            bullets=[
                "① SDカード（またはフォルダ）を選択",
                "② 「日付フォルダへコピー」で自動コピー",
                "   例: bistropapa_step0 / 2026-03-11 / *.jpg",
                "③ 品質フィルタ（ブレ・暗さ）で自動判定",
                "④ 選別 → 料理名を付ける（AI自動判定も可）",
                "⑤ リネームして出力フォルダに保存",
            ],
            btn_text="▶  ステップ０を開く",
            btn_color="#7B2FBE",
            btn_cmd=self.open_step0
        )

        tk.Frame(body, bg="#1E2A3A", height=16).pack()

        # ── ステップ1カード ──
        self._step_card(
            parent=body,
            header_text="【ステップ１】Canva修正後の一括処理",
            header_bg="#D4820A",
            bullets=[
                "Canvaでダウンロード（1,2,3…）",
                "修正済み写真フォルダを指定",
                "「料理名」を入力",
                '自動で「料理名_縦_ロゴ付き.jpg」にリネーム',
                "保存先フォルダを開く",
            ],
            btn_text="▶  ステップ１を開く",
            btn_color="#D4820A",
            btn_cmd=self.open_step1
        )

        # ── 設定ボタン ──
        tk.Button(self.root, text="⚙  設定", command=self.open_settings,
                  bg="#2C3E50", fg="#8899AA", font=("Meiryo", 9),
                  relief=tk.FLAT, padx=14, pady=4,
                  activebackground="#34495E", activeforeground="white"
                  ).pack(side=tk.BOTTOM, anchor=tk.SW, padx=16, pady=12)

    def _step_card(self, parent, header_text, header_bg, bullets,
                   btn_text, btn_color, btn_cmd):
        card = tk.Frame(parent, bg="#263040", bd=0)
        card.pack(fill=tk.X)

        # ヘッダー
        tk.Label(card, text=header_text, bg=header_bg, fg="white",
                 font=("Meiryo", 11, "bold"), pady=9,
                 anchor=tk.CENTER).pack(fill=tk.X)

        # 本文
        inner = tk.Frame(card, bg="#263040")
        inner.pack(fill=tk.BOTH, padx=40, pady=12)

        # 箇条書き
        bl = tk.Frame(inner, bg="#263040")
        bl.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        for b in bullets:
            tk.Label(bl, text=f"• {b}", bg="#263040", fg="#BDC8D4",
                     font=("Meiryo", 10), anchor=tk.W,
                     justify=tk.LEFT).pack(anchor=tk.W, pady=1)

        # ボタン
        br = tk.Frame(inner, bg="#263040")
        br.pack(side=tk.RIGHT, padx=20)
        tk.Button(br, text=btn_text, command=btn_cmd,
                  bg=btn_color, fg="white", font=("Meiryo", 11, "bold"),
                  relief=tk.FLAT, padx=22, pady=12,
                  activebackground=btn_color, activeforeground="white",
                  cursor="hand2").pack(expand=True)

    def open_step0(self):
        win = tk.Toplevel(self.root)
        Step0App(win, self.config)

    def open_step1(self):
        win = tk.Toplevel(self.root)
        Step1App(win, self.config)

    def open_settings(self):
        SettingsDialog(self.root, self.config)


# =============================================================================
# ステップ０ウィンドウ
# =============================================================================
class Step0App:
    def __init__(self, root, config):
        self.root = root
        self.root.title("【ステップ０】SDカード取り込み & 品質選別")
        self.root.geometry("980x720")
        self.root.configure(bg="#2C3E50")
        self.config = config
        self.sd_folder = ""
        self._loaded_folder = ""
        # デフォルトSDフォルダ: E:\DCIM\168_PANA → E:\DCIM → 空欄
        self._default_sd_folder = self._detect_default_sd()
        self._rotations = {}         # {index: rotation_degrees}
        self._rejected_images = []   # [(Path, reason)] 除外写真
        self.current_images = []
        self.current_index = 0
        self.photo_tk = None
        self._build()
        self._bind_keys()
        # デフォルトSDフォルダを初期表示に反映
        if self._default_sd_folder:
            self.sd_folder = self._default_sd_folder
            self.sd_var.set(self._default_sd_folder)
            self.folder_var.set(self._default_sd_folder)
            self._loaded_folder = self._default_sd_folder
            self.log(f"SDフォルダ自動検出: {self._default_sd_folder}")

    def _build(self):
        # タイトル
        tk.Label(self.root,
                 text="【ステップ０】SDカード取り込み ＆ 品質選別",
                 font=("Meiryo", 13, "bold"), bg="#7B2FBE", fg="white",
                 pady=9).pack(fill=tk.X)

        body = tk.Frame(self.root, bg="#2C3E50")
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        # 左サイドバー
        left = tk.Frame(body, bg="#2C3E50", width=215)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        left.pack_propagate(False)

        def section(t):
            tk.Frame(left, bg="#4A5568", height=1).pack(fill=tk.X, pady=6)
            tk.Label(left, text=t, bg="#2C3E50", fg="#F39C12",
                     font=("Meiryo", 9, "bold")).pack(anchor=tk.W)

        # ── SDカード取り込み ──
        section("💾 SDカード/写真フォルダ")
        self.sd_var = tk.StringVar(value="SDカードフォルダを選択")
        tk.Label(left, textvariable=self.sd_var, bg="#34495E", fg="#ECF0F1",
                 wraplength=200, justify=tk.LEFT, padx=5, pady=3).pack(fill=tk.X)
        self._btn(left, "📂 フォルダを選択", self.select_sd_folder, "#C41E3A", bold=True)

        # ── 現在のフォルダ ──
        section("📁 現在のフォルダ")
        self.folder_var = tk.StringVar(value="（未選択）")
        self.folder_lbl = tk.Label(left, textvariable=self.folder_var,
                 bg="#34495E", fg="#5DADE2",
                 wraplength=200, justify=tk.LEFT, padx=5, pady=3,
                 cursor="hand2", font=("Meiryo", 8, "underline"))
        self.folder_lbl.pack(fill=tk.X)
        self.folder_lbl.bind("<Button-1>", self._open_current_folder)
        self._btn(left, "📂 既存フォルダを再選別", self.select_folder, "#3498DB")

        # ── 日付フォルダ ──
        section("📅 日付フォルダ")
        tk.Label(left, text="日付フォーマット: YYYY-MM-DD",
                 bg="#2C3E50", fg="#7F8C8D", font=("Meiryo", 7)).pack(anchor=tk.W)
        self.date_entry_var = tk.StringVar(
            value=datetime.datetime.now().strftime("%Y-%m-%d"))
        tk.Entry(left, textvariable=self.date_entry_var,
                 font=("Meiryo", 10), bg="white", fg="#2C3E50").pack(fill=tk.X, pady=2)
        self._btn(left, "📥 日付フォルダ作成 & 移動", self.import_from_sd, "#F39C12", bold=True)

        # ── フィルタ ──
        self._btn(left, "📷 写真を読み込む",   self.run_filter,      "#8E44AD")
        self._btn(left, "🔄 フィルタをリセット", self.reset_filter,    "#7F8C8D")
        self._btn(left, "👁 除外写真を見直す",   self.review_rejected, "#E67E22")

        # ── 選別状況 ──
        section("📊 選別状況")
        self.status_var = tk.StringVar(value="候補: 0枚\n選択: 0枚  スキップ: 0枚")
        tk.Label(left, textvariable=self.status_var, bg="#34495E", fg="#ECF0F1",
                 padx=5, pady=4, justify=tk.LEFT).pack(fill=tk.X)
        self._btn(left, "📋 選別済み写真一覧", self.show_selected_list, "#2980B9")

        # ── 料理名設定 ──
        section("🍽 料理名設定")
        self.dish_entry_var = tk.StringVar()
        tk.Entry(left, textvariable=self.dish_entry_var,
                 font=("Meiryo", 11), bg="white", fg="#2C3E50").pack(fill=tk.X, pady=2)
        self._btn(left, "✅ この名前を適用", self.apply_dish_name,   "#27AE60", bold=True)
        self._btn(left, "🔍 確認・変更",     self.process_selected,  "#8E44AD")

        # ── 処理実行 ──
        section("🚀 ファイル保存・リネーム")
        self._btn(left, "💾 選択画像を保存する", self.process_selected, "#C41E3A", bold=True)

        # 右ビューワー
        right = tk.Frame(body, bg="#1A252F")
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(right, text="← → 移動　　Enter 選択（緑）　　Delete スキップ（赤）　　R 回転90°",
                 bg="#263040", fg="#7F8C8D", font=("Meiryo", 8),
                 pady=3).pack(fill=tk.X, padx=4)

        self.canvas = tk.Canvas(right, bg="#1A252F", cursor="hand2",
                                highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self.img_info_var = tk.StringVar(value="写真を読み込んでください")
        tk.Label(right, textvariable=self.img_info_var, bg="#1A252F",
                 fg="#BDC3C7", font=("Meiryo", 9)).pack(pady=1)

        nav = tk.Frame(right, bg="#1A252F")
        nav.pack(pady=5)
        for txt, cmd, col in [("◀ 前",         self.prev_image,    "#34495E"),
                               ("✅ 選択",       self.select_current,"#27AE60"),
                               ("❌ スキップ",   self.skip_current,  "#C41E3A"),
                               ("🗑 削除",       self.delete_current,"#922B21"),
                               ("🔄 回転90°",   self.rotate_current,"#7F8C8D"),
                               ("次 ▶",         self.next_image,    "#34495E")]:
            tk.Button(nav, text=txt, command=cmd, bg=col, fg="white",
                      font=("Meiryo", 9, "bold") if "選択" in txt else ("Meiryo", 9),
                      relief=tk.FLAT, padx=8, pady=5).pack(side=tk.LEFT, padx=2)

        # ログ
        lf = tk.Frame(self.root, bg="#2C3E50")
        lf.pack(fill=tk.X, padx=12, pady=(0, 8))
        tk.Label(lf, text="📝 ログ", bg="#2C3E50", fg="#7F8C8D",
                 font=("Meiryo", 8, "bold")).pack(anchor=tk.W)
        self.log_txt = tk.Text(lf, height=4, bg="#1A252F", fg="#95A5A6",
                               font=("Courier", 8), state=tk.DISABLED, wrap=tk.WORD)
        sb = tk.Scrollbar(lf, command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_txt.pack(fill=tk.X)

    def _open_current_folder(self, event=None):
        """現在のフォルダをエクスプローラー/Finderで開く"""
        folder = getattr(self, '_loaded_folder', None) or self.folder_var.get()
        if not folder or folder in ("（未選択）", "← SDコピー後に自動セット"):
            messagebox.showinfo("確認", "フォルダが選択されていません。", parent=self.root)
            return
        if not os.path.isdir(folder):
            messagebox.showwarning("確認", f"フォルダが見つかりません:\n{folder}", parent=self.root)
            return
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                os.system(f'open "{folder}"')
            else:
                os.system(f'xdg-open "{folder}"')
        except Exception as ex:
            messagebox.showerror("エラー", str(ex), parent=self.root)

    def _btn(self, p, t, cmd, col, bold=False):
        tk.Button(p, text=t, command=cmd, bg=col, fg="white",
                  font=("Meiryo", 9, "bold") if bold else ("Meiryo", 9),
                  relief=tk.FLAT, pady=5,
                  activebackground=col, activeforeground="white"
                  ).pack(fill=tk.X, pady=2)

    def _bind_keys(self):
        self.root.bind("<Return>", lambda e: self.select_current())
        self.root.bind("<Delete>", lambda e: self.skip_current())
        self.root.bind("<Left>",   lambda e: self.prev_image())
        self.root.bind("<Right>",  lambda e: self.next_image())
        self.root.bind("<r>",      lambda e: self.rotate_current())
        self.root.bind("<R>",      lambda e: self.rotate_current())
        self.root.focus_set()

    def log(self, msg):
        def _do():
            self.log_txt.config(state=tk.NORMAL)
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            self.log_txt.insert(tk.END, f"[{ts}] {msg}\n")
            self.log_txt.see(tk.END)
            self.log_txt.config(state=tk.DISABLED)
        self.root.after(0, _do)

    def _detect_default_sd(self):
        """E:\\DCIM\\168_PANA → E:\\DCIM → 空欄 の順で存在チェック"""
        candidates = [
            r"E:\DCIM\168_PANA",
            r"E:\DCIM",
        ]
        for c in candidates:
            if os.path.isdir(c):
                return c
        return ""

    def select_sd_folder(self):
        # ダイアログの初期フォルダをデフォルトSDに合わせる
        init_dir = self.sd_folder or self._default_sd_folder or "/"
        f = filedialog.askdirectory(title="SDカードまたは写真フォルダを選択",
                                    initialdir=init_dir, parent=self.root)
        if f:
            self.sd_folder = f
            self.sd_var.set(f)
            self.folder_var.set(f)
            self._loaded_folder = f
            self.log(f"フォルダ選択: {f}")

    def import_from_sd(self):
        """SDカードから日付フォルダへ移動（カット＆ペースト）"""
        sd = self.sd_folder
        if not sd or not os.path.isdir(sd):
            messagebox.showerror("エラー", "先にSDカード/写真フォルダを選択してください", parent=self.root)
            return

        date_str = self.date_entry_var.get().strip() or datetime.datetime.now().strftime("%Y-%m-%d")
        base_out = self.config.get("step0_output_folder",
                                   str(Path.home() / "Desktop" / "bistropapa_step0"))
        dest_folder = Path(base_out) / date_str
        dest_folder.mkdir(parents=True, exist_ok=True)

        exts = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}
        src_files = []
        for root_dir, dirs, files in os.walk(sd):
            for fn in files:
                if Path(fn).suffix in exts:
                    src_files.append(Path(root_dir) / fn)

        if not src_files:
            messagebox.showwarning("確認", "フォルダに画像が見つかりませんでした。", parent=self.root)
            return

        if not messagebox.askyesno("確認",
                f"{len(src_files)}枚の写真を\n{dest_folder}\nに移動します（SDカードから削除されます）。\n続けますか？",
                parent=self.root):
            return

        dlg = ProgressDialog(self.root, "日付フォルダへ移動中...", len(src_files))

        def worker():
            import shutil
            moved = failed = renamed = 0
            for i, src in enumerate(src_files):
                if dlg.cancelled: break
                dest_path = dest_folder / src.name
                # 同名ファイルが既にある場合は連番付与
                if dest_path.exists():
                    stem, sfx = src.stem, src.suffix
                    c = 1
                    while dest_path.exists():
                        dest_path = dest_folder / f"{stem}_{c:03d}{sfx}"
                        c += 1
                    renamed += 1
                try:
                    # コピーしてから元ファイルを削除（カット＆ペーストと同等）
                    shutil.copy2(str(src), str(dest_path))
                    src.unlink()   # SDカード側を削除
                    moved += 1
                except Exception as ex:
                    self.root.after(0, lambda nm=src.name, e=str(ex):
                        self.log(f"⚠️ 移動失敗（SDカードに残します）: {nm} → {e}"))
                    failed += 1
                    # コピー先に中途ファイルが残っていれば削除
                    if dest_path.exists():
                        try: dest_path.unlink()
                        except Exception: pass
                self.root.after(0, lambda n=i+1, nm=src.name: dlg.update(n, f"移動: {nm}"))

            self.root.after(0, dlg.close)
            msg = f"✅ 移動完了: {moved}枚 → {dest_folder}"
            if renamed: msg += f"  (重複リネーム{renamed}枚)"
            if failed:  msg += f"  ⚠️ 失敗{failed}枚（SDカードに残っています）"
            self.root.after(0, lambda: self.log(msg))
            self.root.after(100, lambda: self.folder_var.set(str(dest_folder)))
            self.root.after(100, lambda: setattr(self, '_loaded_folder', str(dest_folder)))
            def _ask():
                detail = f"✅ {moved}枚を移動しました\n保存先: {dest_folder}\n"
                if failed:
                    detail += f"\n⚠️ {failed}枚は移動失敗のためSDカードに残っています。\n"
                detail += "\nこのまま品質フィルタを実行して写真を選別しますか？\n（「いいえ」の場合は手動で「品質フィルタ実行」を押してください）"
                ans = messagebox.askyesno("移動完了", detail, parent=self.root)
                if ans:
                    self.run_filter()
            self.root.after(200, _ask)

        threading.Thread(target=worker, daemon=True).start()

    def select_folder(self):
        f = filedialog.askdirectory(title="写真フォルダを選択", parent=self.root)
        if f:
            self.folder_var.set(f)
            self._loaded_folder = f
            self.log(f"フォルダ選択: {f}")

    def run_filter(self):
        folder = getattr(self, '_loaded_folder', None) or self.folder_var.get()
        if not os.path.isdir(folder):
            messagebox.showerror("エラー", "有効なフォルダを選択してください", parent=self.root)
            return
        self.log("写真を読み込み中...")
        self.root.update()
        exts = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}
        all_files = sorted([f for f in Path(folder).iterdir() if f.suffix in exts])

        # 品質フィルタ/除外ロジックは完全削除（除外なし）
        self._rejected_images = []
        self._rotations = {}
        self.current_images = [(f, "未選択", "") for f in all_files]
        self.current_index = 0
        self._update_status()
        if self.current_images:
            self._show_image()
        else:
            messagebox.showinfo("結果",
                "対象の写真がありませんでした。\n"
                ".jpg/.jpeg/.png を含むフォルダを選択してください。",
                parent=self.root)

    def _show_image(self):
        if not self.current_images:
            return
        p, status, ai = self.current_images[self.current_index]
        try:
            img = open_image_corrected(p)
        except Exception:
            self.log(f"読込エラー: {p.name}")
            return
        # 回転適用
        rot = self._rotations.get(self.current_index, 0)
        if rot:
            img = img.rotate(rot, expand=True)
        cw = self.canvas.winfo_width() or 680
        ch = self.canvas.winfo_height() or 480
        img.thumbnail((cw - 20, ch - 20), Image.LANCZOS)
        colors = {"選択": (0, 200, 0), "スキップ": (200, 0, 0)}
        if status in colors:
            arr = np.array(img); c = colors[status]
            arr[:6, :] = c; arr[-6:, :] = c
            arr[:, :6] = c; arr[:, -6:] = c
            img = Image.fromarray(arr)
        self.photo_tk = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, anchor=tk.CENTER, image=self.photo_tk)
        ori = get_orientation(p, rot)
        total = len(self.current_images)
        sel = sum(1 for _, s, _ in self.current_images if s == "選択")
        rot_str = f"  🔄{rot}°" if rot else ""
        self.img_info_var.set(
            f"{self.current_index+1}/{total}　{p.name}　[{ori}]{rot_str}　状態:{status}　選択:{sel}枚")

    def _update_status(self):
        t = len(self.current_images)
        s = sum(1 for _, st, _ in self.current_images if st == "選択")
        sk = sum(1 for _, st, _ in self.current_images if st == "スキップ")
        self.status_var.set(f"候補: {t}枚\n選択: {s}枚  スキップ: {sk}枚")

    def select_current(self):
        if not self.current_images: return
        p, _, ai = self.current_images[self.current_index]
        self.current_images[self.current_index] = (p, "選択", ai)
        self._update_status(); self._show_image(); self.next_image()

    def skip_current(self):
        if not self.current_images: return
        p, _, ai = self.current_images[self.current_index]
        self.current_images[self.current_index] = (p, "スキップ", ai)
        self._update_status(); self._show_image(); self.next_image()

    def prev_image(self):
        if self.current_images and self.current_index > 0:
            self.current_index -= 1; self._show_image()

    def next_image(self):
        if self.current_images and self.current_index < len(self.current_images) - 1:
            self.current_index += 1; self._show_image()

    def ai_detect_all(self):
        api_key = self.config.get("anthropic_api_key", "")
        if not api_key:
            messagebox.showerror("APIキー未設定", "設定画面でAnthropic APIキーを入力してください。", parent=self.root)
            return
        targets = [(i, p) for i, (p, s, ai) in enumerate(self.current_images)
                   if s == "選択" and not ai]
        if not targets:
            sel = [(i, p) for i, (p, s, _) in enumerate(self.current_images) if s == "選択"]
            if not sel:
                messagebox.showwarning("確認", "先に写真を選択してください。", parent=self.root)
                return
            if not messagebox.askyesno("確認", f"選択済み{len(sel)}枚を再判定しますか？", parent=self.root):
                return
            targets = sel

        dlg = ProgressDialog(self.root, "AI料理名判定中...", len(targets))

        def worker():
            done = 0
            with ThreadPoolExecutor(max_workers=self.config.get("ai_concurrent", 3)) as ex:
                futures = {ex.submit(detect_dish_name, p, api_key): (i, p) for i, p in targets}
                for fut in as_completed(futures):
                    if dlg.cancelled: break
                    idx, path = futures[fut]
                    name = "料理"
                    try: name = fut.result()
                    except Exception: pass
                    p2, s2, _ = self.current_images[idx]
                    self.current_images[idx] = (p2, s2, name)
                    done += 1
                    self.root.after(0, lambda n=done, nm=name: dlg.update(n, f"判定: {nm}"))
                    self.log(f"🤖 {path.name} → {name}")
            self.root.after(0, dlg.close)
            self.root.after(100, self._show_image)
            self.root.after(100, lambda: self.log(f"AI判定完了: {done}枚"))

        threading.Thread(target=worker, daemon=True).start()

    def rotate_current(self):
        """現在の画像を90°回転してプレビュー（rotation状態を保持）"""
        if not self.current_images: return
        p, s, ai = self.current_images[self.current_index]
        rot = self._rotations.get(self.current_index, 0)
        self._rotations[self.current_index] = (rot + 90) % 360
        self._show_image()

    def delete_current(self):
        """現在の画像をリストから完全に除外（ファイルは消さない）"""
        if not self.current_images: return
        p, s, ai = self.current_images[self.current_index]
        if not messagebox.askyesno("確認", f"{p.name}\nをリストから除外しますか？", parent=self.root):
            return
        self.current_images.pop(self.current_index)
        if self.current_index >= len(self.current_images):
            self.current_index = max(0, len(self.current_images) - 1)
        self._update_status()
        if self.current_images:
            self._show_image()
        else:
            self.canvas.delete("all")
            self.img_info_var.set("写真がありません")

    def reset_filter(self):
        """フィルタをリセットしてフォルダを再読み込み"""
        self.current_images = []
        self._rotations = {}
        self.current_index = 0
        self._update_status()
        self.canvas.delete("all")
        self.img_info_var.set("写真を読み込んでください")
        self.log("フィルタをリセットしました")

    def review_rejected(self):
        """除外された写真を確認・救済するウィンドウ"""
        if not hasattr(self, '_rejected_images') or not self._rejected_images:
            messagebox.showinfo("確認", "除外された写真はありません。\n先に品質フィルタを実行してください。", parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("除外写真の見直し")
        win.geometry("900x560")
        win.configure(bg="#2C3E50")
        win.grab_set()

        tk.Label(win, text="👁  除外された写真（クリックで候補に追加）",
                 bg="#E67E22", fg="white", font=("Meiryo", 11, "bold"),
                 pady=7).pack(fill=tk.X)

        outer = tk.Frame(win, bg="#1A252F")
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        hsc = tk.Scrollbar(outer, orient=tk.HORIZONTAL)
        vsc = tk.Scrollbar(outer, orient=tk.VERTICAL)
        tc = tk.Canvas(outer, bg="#1A252F", xscrollcommand=hsc.set, yscrollcommand=vsc.set)
        hsc.config(command=tc.xview); vsc.config(command=tc.yview)
        hsc.pack(side=tk.BOTTOM, fill=tk.X); vsc.pack(side=tk.RIGHT, fill=tk.Y)
        tc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tf = tk.Frame(tc, bg="#1A252F")
        tc.create_window((0, 0), window=tf, anchor="nw")

        thumb_refs = {}
        rescued = set()
        COLS = 5

        def toggle_rescue(i, cf, nl):
            if i in rescued:
                rescued.discard(i)
                cf.config(bg="#34495E")
                nl.config(fg="#E74C3C")
            else:
                rescued.add(i)
                cf.config(bg="#27AE60")
                nl.config(fg="white")

        for i, (p, reason) in enumerate(self._rejected_images):
            row, col = divmod(i, COLS)
            cf = tk.Frame(tf, bg="#34495E", padx=2, pady=2)
            cf.grid(row=row, column=col, padx=4, pady=4)
            try:
                img = open_image_corrected(p)
                img.thumbnail((140, 140), Image.LANCZOS)
                ph = ImageTk.PhotoImage(img)
                thumb_refs[i] = ph
            except Exception:
                ph = None
            il = tk.Label(cf, image=ph, bg="#1A252F", cursor="hand2", width=140, height=140)
            il.pack()
            nl = tk.Label(cf, text=f"除外: {reason[:20]}\n{p.name[:18]}",
                          bg="#34495E", fg="#E74C3C", font=("Meiryo", 7),
                          wraplength=138, justify=tk.CENTER)
            nl.pack()
            il.bind("<Button-1>", lambda e, idx=i, c=cf, n=nl: toggle_rescue(idx, c, n))
            nl.bind("<Button-1>", lambda e, idx=i, c=cf, n=nl: toggle_rescue(idx, c, n))

        tf.update_idletasks()
        tc.config(scrollregion=tc.bbox("all"))

        def do_rescue():
            for i in rescued:
                p, _ = self._rejected_images[i]
                self.current_images.append((p, "未選択", ""))
            self._update_status()
            if self.current_images and rescued:
                self.current_index = len(self.current_images) - len(rescued)
                self._show_image()
            self.log(f"✅ {len(rescued)}枚を候補に追加しました")
            win.destroy()

        bf = tk.Frame(win, bg="#2C3E50")
        bf.pack(pady=6)
        tk.Button(bf, text=f"✅ 選択した写真を候補に追加",
                  command=do_rescue,
                  bg="#27AE60", fg="white", font=("Meiryo", 10, "bold"),
                  relief=tk.FLAT, padx=16, pady=6).pack(side=tk.LEFT, padx=6)
        tk.Button(bf, text="閉じる", command=win.destroy,
                  bg="#7F8C8D", fg="white", font=("Meiryo", 10),
                  relief=tk.FLAT, padx=16).pack(side=tk.LEFT, padx=6)

    def show_selected_list(self):
        """選別済み写真のサムネイル一覧ウィンドウ（クリック選択・角度変更・料理名付与）"""
        sel_indices = [i for i, (p, s, ai) in enumerate(self.current_images) if s == "選択"]
        win = tk.Toplevel(self.root)
        win.title("選別済み写真一覧")
        win.geometry("1000x680")
        win.configure(bg="#2C3E50")
        win.grab_set()

        tk.Label(win, text=f"📋  選択済み写真一覧: {len(sel_indices)}枚　　クリックで選択 → 料理名入力 → 適用",
                 bg="#2980B9", fg="white", font=("Meiryo", 11, "bold"),
                 pady=8).pack(fill=tk.X)

        # サムネイルエリア（スクロール）
        outer = tk.Frame(win, bg="#1A252F")
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=5)
        vsc = tk.Scrollbar(outer, orient=tk.VERTICAL)
        hsc = tk.Scrollbar(outer, orient=tk.HORIZONTAL)
        tc = tk.Canvas(outer, bg="#1A252F", xscrollcommand=hsc.set, yscrollcommand=vsc.set)
        hsc.config(command=tc.xview); vsc.config(command=tc.yview)
        hsc.pack(side=tk.BOTTOM, fill=tk.X); vsc.pack(side=tk.RIGHT, fill=tk.Y)
        tc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tf = tk.Frame(tc, bg="#1A252F")
        tc.create_window((0, 0), window=tf, anchor="nw")

        COLS = 6
        THUMB_SZ = 145
        thumb_refs = {}
        thumb_lbls = {}
        clicked = set()
        local_rotations = {orig: self._rotations.get(orig, 0) for orig in sel_indices}

        def load_ph(orig_idx, rot=0):
            p, _, ai = self.current_images[orig_idx]
            try:
                img = open_image_corrected(p)
                if rot: img = img.rotate(rot, expand=True)
                img.thumbnail((THUMB_SZ, THUMB_SZ), Image.LANCZOS)
                return ImageTk.PhotoImage(img)
            except Exception:
                return None

        def refresh(pos, orig_idx):
            rot = local_rotations[orig_idx]
            ph = load_ph(orig_idx, rot)
            if ph is None: return
            thumb_refs[pos] = ph
            p, _, ai = self.current_images[orig_idx]
            is_sel = pos in clicked
            col = "#F39C12" if is_sel else ("#27AE60" if ai else "#34495E")
            thumb_lbls[pos]["frame"].config(bg=col)
            thumb_lbls[pos]["img"].config(image=ph)
            rot_s = f" 🔄{rot}°" if rot else ""
            thumb_lbls[pos]["name"].config(
                text=f"{ai or '未設定'}{rot_s}\n{p.name[:16]}",
                fg="#F39C12" if is_sel else ("#ECF0F1" if ai else "#7F8C8D"))

        def toggle(pos):
            if pos in clicked: clicked.discard(pos)
            else: clicked.add(pos)
            refresh(pos, sel_indices[pos])
            n = len(clicked)
            sel_lv.set(f"{n}枚選択中" if n else "サムネイルをクリックして選択")
            if n:
                ais = {self.current_images[sel_indices[j]][2] for j in clicked}
                if len(ais) == 1 and list(ais)[0]:
                    dish_var.set(list(ais)[0])

        for pos, orig_idx in enumerate(sel_indices):
            row, col = divmod(pos, COLS)
            cf = tk.Frame(tf, bg="#34495E", padx=2, pady=2)
            cf.grid(row=row, column=col, padx=3, pady=3)
            _, _, ai = self.current_images[orig_idx]
            ph = load_ph(orig_idx, local_rotations[orig_idx])
            thumb_refs[pos] = ph
            il = tk.Label(cf, image=ph, bg="#1A252F", cursor="hand2",
                          width=THUMB_SZ, height=THUMB_SZ)
            il.pack()
            nl = tk.Label(cf, text=f"{ai or '未設定'}\n{self.current_images[orig_idx][0].name[:16]}",
                          bg="#34495E", fg="#7F8C8D" if not ai else "#ECF0F1",
                          font=("Meiryo", 7), wraplength=142, justify=tk.CENTER)
            nl.pack()
            thumb_lbls[pos] = {"frame": cf, "img": il, "name": nl}
            il.bind("<Button-1>", lambda e, p=pos: toggle(p))
            nl.bind("<Button-1>", lambda e, p=pos: toggle(p))

        tf.update_idletasks()
        tc.config(scrollregion=tc.bbox("all"))

        # 操作パネル
        ctrl = tk.Frame(win, bg="#2C3E50")
        ctrl.pack(fill=tk.X, padx=8, pady=3)

        sel_lv = tk.StringVar(value="サムネイルをクリックして選択")
        tk.Label(ctrl, textvariable=sel_lv, bg="#2C3E50", fg="#F39C12",
                 font=("Meiryo", 10, "bold")).pack(anchor=tk.W)

        ir = tk.Frame(ctrl, bg="#2C3E50")
        ir.pack(fill=tk.X, pady=3)
        tk.Label(ir, text="料理名：", bg="#2C3E50", fg="white",
                 font=("Meiryo", 10, "bold")).pack(side=tk.LEFT)
        dish_var = tk.StringVar()
        entry = tk.Entry(ir, textvariable=dish_var, font=("Meiryo", 12), width=22)
        entry.pack(side=tk.LEFT, padx=4)

        def apply_name(event=None):
            name = dish_var.get().strip()
            if not name or not clicked: return
            for pos in list(clicked):
                orig = sel_indices[pos]
                p2, s2, _ = self.current_images[orig]
                self.current_images[orig] = (p2, s2, name)
                refresh(pos, orig)
            clicked.clear()
            dish_var.set("")
            sel_lv.set("✅ 適用しました！")

        tk.Button(ir, text="✅ 適用", command=apply_name,
                  bg="#27AE60", fg="white", font=("Meiryo", 10, "bold"),
                  relief=tk.FLAT, padx=10).pack(side=tk.LEFT, padx=4)
        entry.bind("<Return>", apply_name)

        # 回転
        rr = tk.Frame(ctrl, bg="#2C3E50")
        rr.pack(fill=tk.X, pady=2)
        tk.Label(rr, text="回転（選択中）：", bg="#2C3E50", fg="white",
                 font=("Meiryo", 9)).pack(side=tk.LEFT)
        def apply_rot(deg):
            for pos in list(clicked):
                orig = sel_indices[pos]
                local_rotations[orig] = deg
                self._rotations[orig] = deg
                refresh(pos, orig)
        for deg, lbl in [(90,"↻90°"),(180,"↔180°"),(270,"↺270°"),(0,"⟳リセット")]:
            tk.Button(rr, text=lbl, command=lambda d=deg: apply_rot(d),
                      bg="#7F8C8D", fg="white", font=("Meiryo", 8),
                      relief=tk.FLAT, padx=7).pack(side=tk.LEFT, padx=2)

        br = tk.Frame(ctrl, bg="#2C3E50")
        br.pack(fill=tk.X, pady=2)
        def sel_all():
            for pos in range(len(sel_indices)):
                clicked.add(pos); refresh(pos, sel_indices[pos])
            sel_lv.set(f"{len(sel_indices)}枚選択中")
        def desel_all():
            clicked.clear()
            for pos in range(len(sel_indices)): refresh(pos, sel_indices[pos])
            sel_lv.set("サムネイルをクリックして選択")
        tk.Button(br, text="全選択", command=sel_all,
                  bg="#34495E", fg="white", relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=2)
        tk.Button(br, text="全解除", command=desel_all,
                  bg="#34495E", fg="white", relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=2)
        tk.Button(br, text="閉じる", command=win.destroy,
                  bg="#7F8C8D", fg="white", font=("Meiryo", 10),
                  relief=tk.FLAT, padx=12).pack(side=tk.RIGHT, padx=4)

    def apply_dish_name(self):
        """サイドバーの料理名入力欄から現在の画像に料理名を適用"""
        name = self.dish_entry_var.get().strip()
        if not name:
            messagebox.showwarning("確認", "料理名を入力してください", parent=self.root)
            return
        if not self.current_images: return
        p, s, _ = self.current_images[self.current_index]
        self.current_images[self.current_index] = (p, "選択", name)
        self._update_status()
        self._show_image()
        self.log(f"✅ {p.name} → 料理名:「{name}」で選択")
        # 次の未選択画像に自動移動
        self.next_image()

    def process_selected(self):
        sel = [(p, s, ai) for p, s, ai in self.current_images if s == "選択"]
        if not sel:
            messagebox.showwarning("確認", "選択された写真がありません。", parent=self.root)
            return
        if not messagebox.askyesno("確認", f"{len(sel)}枚を処理します。続けますか？", parent=self.root):
            return
        # 回転情報をインデックスで引き継ぐ
        sel_rotations = {}
        sel_idx = 0
        for orig_idx, (p, s, ai) in enumerate(self.current_images):
            if s == "選択":
                sel_rotations[sel_idx] = self._rotations.get(orig_idx, 0)
                sel_idx += 1
        # 日付フォルダ（元写真のある場所）を保存先として渡す
        src_folder = getattr(self, '_loaded_folder', None) or self.folder_var.get()
        GroupingWindow(self.root, self.config, sel, suffix="",
                       rotations=sel_rotations, src_folder=src_folder)


# =============================================================================
# グルーピングウィンドウ（ステップ０用）
# =============================================================================
class GroupingWindow:
    def __init__(self, parent, config, selected, suffix="", rotations=None, src_folder=None):
        self.parent = parent
        self.config = config
        self.selected = selected
        # 保存先 = 元写真のある日付フォルダ（なければ設定のstep0_output_folder）
        self.out_folder = src_folder or config["step0_output_folder"]
        self.date_str = datetime.datetime.now().strftime("%Y%m%d")
        self.suffix = suffix
        pre_rot = rotations or {}
        self.image_states = {i: {"dish": ai or "", "rotation": pre_rot.get(i, 0)}
                             for i, (p, s, ai) in enumerate(selected)}
        self.history = []  # 料理名適用の履歴（1つ戻す用）
        self.sel_idx = set()
        self.thumb_refs = {}
        self.thumb_lbls = {}

        win = tk.Toplevel(parent)
        win.title("グループ設定・料理名入力")
        win.geometry("1060x710")
        win.configure(bg="#2C3E50")
        win.grab_set()
        self.win = win
        self._build()

    def _build(self):
        tk.Label(self.win,
                 text="🍽️  サムネイルをクリックして選択 → 料理名入力 → 適用",
                 bg="#7B2FBE", fg="white", font=("Meiryo", 11, "bold"),
                 pady=7).pack(fill=tk.X)

        # サムネイルエリア
        outer = tk.Frame(self.win, bg="#1A252F")
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=5)
        hsc = tk.Scrollbar(outer, orient=tk.HORIZONTAL)
        vsc = tk.Scrollbar(outer, orient=tk.VERTICAL)
        self.tc = tk.Canvas(outer, bg="#1A252F",
                            xscrollcommand=hsc.set, yscrollcommand=vsc.set)
        hsc.config(command=self.tc.xview)
        vsc.config(command=self.tc.yview)
        hsc.pack(side=tk.BOTTOM, fill=tk.X)
        vsc.pack(side=tk.RIGHT, fill=tk.Y)
        self.tc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.tf = tk.Frame(self.tc, bg="#1A252F")
        self.tc.create_window((0, 0), window=self.tf, anchor="nw")

        COLS = 6
        for i, (p, s, ai) in enumerate(self.selected):
            row, col = divmod(i, COLS)
            cf = tk.Frame(self.tf, bg="#34495E", padx=2, pady=2)
            cf.grid(row=row, column=col, padx=4, pady=4)
            ph = self._load_thumb(i)
            self.thumb_refs[i] = ph
            il = tk.Label(cf, image=ph, bg="#1A252F", cursor="hand2",
                          width=155, height=155)
            il.pack()
            nl = tk.Label(cf, text=ai or "未設定", bg="#34495E", fg="#7F8C8D",
                          font=("Meiryo", 7), wraplength=150, justify=tk.CENTER)
            nl.pack()
            self.thumb_lbls[i] = {"frame": cf, "img": il, "name": nl}
            il.bind("<Button-1>", lambda e, idx=i: self._toggle(idx))
            nl.bind("<Button-1>", lambda e, idx=i: self._toggle(idx))

        self.tf.update_idletasks()
        self.tc.config(scrollregion=self.tc.bbox("all"))

        # 操作パネル
        ctrl = tk.Frame(self.win, bg="#2C3E50")
        ctrl.pack(fill=tk.X, padx=8, pady=3)

        self.sel_lv = tk.StringVar(value="サムネイルをクリックして選択")
        tk.Label(ctrl, textvariable=self.sel_lv, bg="#2C3E50", fg="#F39C12",
                 font=("Meiryo", 10, "bold")).pack(anchor=tk.W)

        # 料理名入力
        ir = tk.Frame(ctrl, bg="#2C3E50")
        ir.pack(fill=tk.X, pady=3)
        tk.Label(ir, text="料理名：", bg="#2C3E50", fg="white",
                 font=("Meiryo", 11, "bold")).pack(side=tk.LEFT)
        self.dish_var = tk.StringVar()
        self.entry = tk.Entry(ir, textvariable=self.dish_var,
                              font=("Meiryo", 13), width=22)
        self.entry.pack(side=tk.LEFT, padx=4)
        tk.Button(ir, text="✅ 適用", command=self._apply,
                  bg="#27AE60", fg="white", font=("Meiryo", 10, "bold"),
                  relief=tk.FLAT, padx=10).pack(side=tk.LEFT, padx=4)
        self.entry.bind("<Return>", lambda e: self._apply())
        tk.Button(ir, text="全枚に適用", command=self._apply_all,
                  bg="#8E44AD", fg="white", font=("Meiryo", 9),
                  relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=4)

        # 回転
        rr = tk.Frame(ctrl, bg="#2C3E50")
        rr.pack(fill=tk.X, pady=2)
        tk.Label(rr, text="回転：", bg="#2C3E50", fg="white",
                 font=("Meiryo", 9)).pack(side=tk.LEFT)
        for deg, lbl in [(90, "↻ 90°"), (180, "↔ 180°"), (270, "↺ 270°"), (0, "⟳ リセット")]:
            tk.Button(rr, text=lbl, command=lambda d=deg: self._rotate(d),
                      bg="#7F8C8D", fg="white", font=("Meiryo", 8),
                      relief=tk.FLAT, padx=7).pack(side=tk.LEFT, padx=2)

        # 全選択/解除
        sr = tk.Frame(ctrl, bg="#2C3E50")
        sr.pack(fill=tk.X, pady=2)
        tk.Button(sr, text="全選択", command=self._select_all,
                  bg="#34495E", fg="white", font=("Meiryo", 8),
                  relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=2)
        tk.Button(sr, text="全解除", command=self._deselect_all,
                  bg="#34495E", fg="white", font=("Meiryo", 8),
                  relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=2)
        tk.Button(sr, text="↩ 1つ戻す", command=self._undo_last_apply,
                  bg="#34495E", fg="white", font=("Meiryo", 8),
                  relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=2)

        # 保存
        tk.Button(self.win, text="💾  保存する", command=self._save,
                  bg="#C41E3A", fg="white", font=("Meiryo", 12, "bold"),
                  relief=tk.FLAT, pady=9).pack(fill=tk.X, padx=8, pady=5)

    def _load_thumb(self, i):
        p, _, _ = self.selected[i]
        try:
            img = open_image_corrected(p)
            rot = self.image_states[i]["rotation"]
            if rot:
                img = img.rotate(rot, expand=True)
            img.thumbnail((155, 155), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _refresh(self, i):
        ph = self._load_thumb(i)
        if ph is None: return
        self.thumb_refs[i] = ph
        self.thumb_lbls[i]["img"].config(image=ph)
        dish = self.image_states[i]["dish"]
        is_sel = i in self.sel_idx
        col = "#F39C12" if is_sel else ("#27AE60" if dish else "#34495E")
        self.thumb_lbls[i]["frame"].config(bg=col)
        rot = self.image_states[i]["rotation"]
        rot_s = f" 🔄{rot}°" if rot else ""
        p, _, _ = self.selected[i]
        self.thumb_lbls[i]["name"].config(
            text=f"{dish or '未設定'}{rot_s}\n{p.name[:18]}",
            fg="#F39C12" if is_sel else ("#ECF0F1" if dish else "#7F8C8D"))

    def _toggle(self, i):
        if i in self.sel_idx: self.sel_idx.discard(i)
        else: self.sel_idx.add(i)
        self._refresh(i)
        self._update_sel_label()

    def _update_sel_label(self):
        if self.sel_idx:
            dishes = {self.image_states[j]["dish"] for j in self.sel_idx}
            if len(dishes) == 1 and list(dishes)[0]:
                self.dish_var.set(list(dishes)[0])
            self.entry.focus_set()
            self.sel_lv.set(f"{len(self.sel_idx)}枚選択中")
        else:
            self.sel_lv.set("サムネイルをクリックして選択")

    def _apply(self):
        name = self.dish_var.get().strip()
        if not name:
            self.entry.config(bg="#FFCCCC")
            self.win.after(400, lambda: self.entry.config(bg="white"))
            return
        if not self.sel_idx:
            messagebox.showwarning("確認", "写真を選択してから適用してください", parent=self.win)
            return
        applied = list(self.sel_idx)
        changed_before = {}
        for j in applied:
            if self.image_states[j]["dish"] != name:
                changed_before[j] = self.image_states[j]["dish"]
            self.image_states[j]["dish"] = name
            self._refresh(j)
        self.sel_idx.clear()
        self.dish_var.set("")

        # ── 類似写真への自動付与チェック ──
        similar = self._find_similar(applied)
        unnamed_similar = [j for j in similar if not self.image_states[j]["dish"]]
        if unnamed_similar:
            ans = messagebox.askyesno(
                "類似写真を検出",
                f"「{name}」と似た写真が {len(unnamed_similar)}枚 見つかりました。\n"
                f"同じ料理名を自動で付けますか？",
                parent=self.win)
            if ans:
                for j in unnamed_similar:
                    if self.image_states[j]["dish"] != name:
                        changed_before[j] = self.image_states[j]["dish"]
                    self.image_states[j]["dish"] = name
                    self._refresh(j)
                if changed_before:
                    self.history.append(changed_before)
                self.sel_lv.set(f"✅ 計{len(applied)+len(unnamed_similar)}枚に「{name}」を設定しました")
                return
        if changed_before:
            self.history.append(changed_before)
        self.sel_lv.set("✅ 適用しました！次のグループを選択してください")

    def _undo_last_apply(self):
        if not self.history:
            messagebox.showinfo("確認", "取り消せる適用履歴がありません。", parent=self.win)
            return
        prev_states = self.history.pop()
        for idx, prev_dish in prev_states.items():
            self.image_states[idx]["dish"] = prev_dish
            self._refresh(idx)
        self.sel_lv.set("↩ 直前の適用を取り消しました")

    def _find_similar(self, applied_indices, threshold=0.85):
        """適用済み写真と色ヒストグラムが近い未設定写真を返す"""
        import colorsys
        def get_hist(path):
            try:
                img = Image.open(path).convert("RGB").resize((64, 64))
                arr = np.array(img, dtype=np.float32) / 255.0
                # 色相・彩度・明度の各ヒストグラム（軽量）
                r, g, b = arr[:,:,0], arr[:,:,1], arr[:,:,2]
                hist_r = np.histogram(r, bins=16, range=(0,1))[0]
                hist_g = np.histogram(g, bins=16, range=(0,1))[0]
                hist_b = np.histogram(b, bins=16, range=(0,1))[0]
                h = np.concatenate([hist_r, hist_g, hist_b]).astype(np.float32)
                n = np.linalg.norm(h)
                return h / n if n > 0 else h
            except Exception:
                return None

        # 適用済みのヒストグラムを計算
        ref_hists = []
        for j in applied_indices:
            p, _, _ = self.selected[j]
            h = get_hist(p)
            if h is not None:
                ref_hists.append(h)
        if not ref_hists:
            return []

        # 未設定写真と比較
        similar = []
        for j in range(len(self.selected)):
            if j in applied_indices: continue
            if self.image_states[j]["dish"]: continue
            p, _, _ = self.selected[j]
            h = get_hist(p)
            if h is None: continue
            # 参照ヒストグラムとの最大類似度
            sim = max(float(np.dot(ref_h, h)) for ref_h in ref_hists)
            if sim >= threshold:
                similar.append(j)
        return similar

    def _apply_all(self):
        name = self.dish_var.get().strip()
        if not name:
            messagebox.showwarning("確認", "料理名を入力してください", parent=self.win)
            return
        for j in range(len(self.selected)):
            self.image_states[j]["dish"] = name
            self._refresh(j)
        self.sel_lv.set(f"全{len(self.selected)}枚に「{name}」を設定しました")

    def _rotate(self, deg):
        if not self.sel_idx:
            messagebox.showwarning("確認", "回転する写真を選択してください", parent=self.win)
            return
        for j in self.sel_idx:
            self.image_states[j]["rotation"] = deg
            self._refresh(j)

    def _select_all(self):
        for j in range(len(self.selected)):
            self.sel_idx.add(j)
            self._refresh(j)
        self._update_sel_label()

    def _deselect_all(self):
        self.sel_idx.clear()
        for j in range(len(self.selected)):
            self._refresh(j)
        self.sel_lv.set("サムネイルをクリックして選択")

    def _save(self):
        unnamed = [i for i in range(len(self.selected)) if not self.image_states[i]["dish"]]
        if unnamed:
            if not messagebox.askyesno("確認",
                    f"{len(unnamed)}枚に料理名が未設定です。\n「料理」として保存しますか？",
                    parent=self.win):
                return
            for i in unnamed:
                self.image_states[i]["dish"] = "料理"

        dlg = ProgressDialog(self.win, "保存中...", len(self.selected))
        results = []

        def worker():
            import shutil
            date_str = self.date_str
            out = Path(self.out_folder)
            out.mkdir(parents=True, exist_ok=True)

            # 使用中のファイル名を先に集めて連番重複を防ぐ
            used_names = set(f.name for f in out.iterdir() if f.is_file())

            for i, (p, _, _) in enumerate(self.selected):
                if dlg.cancelled: break
                dish = self.image_states[i]["dish"]
                rot  = self.image_states[i]["rotation"]
                try:
                    img = open_image_corrected(p)
                    if rot:
                        img = img.rotate(rot, expand=True)

                    # ステップ０は料理名のみ（縦横はステップ１で付与）
                    # 同名ファイルは (1)(2)... で連番
                    base_fn = f"{dish}.jpg"
                    if base_fn not in used_names:
                        fn = base_fn
                    else:
                        counter = 1
                        while True:
                            fn = f"{dish}({counter}).jpg"
                            if fn not in used_names:
                                break
                            counter += 1
                    used_names.add(fn)

                    dest = out / fn

                    # 元ファイルと同じフォルダに保存 → 元ファイルを削除（実質リネーム）
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")

                    # 一時ファイルに書いてからアトミックに置換
                    tmp = dest.with_suffix(".tmp.jpg")
                    img.save(str(tmp), "JPEG", quality=95)

                    # 元ファイルが新名と異なる場合のみ元ファイルを削除
                    if p.resolve() != dest.resolve():
                        tmp.replace(dest)
                        if p.exists() and p.parent.resolve() == out.resolve():
                            p.unlink()
                    else:
                        tmp.replace(dest)

                    results.append({"original": p.name, "new_name": fn,
                                    "dish": dish, "orientation": "",
                                    "path": str(dest)})
                    self.win.after(0, lambda n=i+1, nm=fn: dlg.update(n, f"保存: {nm}"))
                except Exception as ex:
                    self.win.after(0, lambda msg=str(ex): print(f"保存エラー: {msg}"))

            self.win.after(0, dlg.close)
            self.win.after(100, self.win.destroy)
            # undoログ保存（リネーム操作）
            undo_entries = [
                {"type": "rename",
                 "new": r["new_name"],
                 "original": r["original"],
                 "folder": self.out_folder}
                for r in results
            ]
            save_undo_log(undo_entries)
            self.win.after(200, lambda: ResultsWindow(self.parent, results, self.out_folder))

        threading.Thread(target=worker, daemon=True).start()


# =============================================================================
# ステップ１ウィンドウ（Canvaダウンロード後のリネーム）
# =============================================================================
class Step1App:
    def __init__(self, root, config):
        self.root = root
        self.root.title("【ステップ１】Canva修正後の一括リネーム")
        self.root.geometry("820x680")
        self.root.configure(bg="#2C3E50")
        self.config = config
        self.thumb_refs = {}
        self.file_states = {}      # {i: {"dish": str, "orientation": str}}
        self.files = []            # [Path]
        self.photo_tk_large = None
        self._build()

    def _build(self):
        tk.Label(self.root,
                 text="【ステップ１】Canva修正後の一括リネーム処理",
                 font=("Meiryo", 13, "bold"), bg="#D4820A", fg="white",
                 pady=9).pack(fill=tk.X)

        # 説明
        desc = tk.Frame(self.root, bg="#263040")
        desc.pack(fill=tk.X, padx=16, pady=6)
        bullets = [
            "① Canvaでダウンロードしたフォルダを指定",
            "② 各写真に料理名を設定（サムネイルをクリック → 名前入力）",
            "③「保存する」で「料理名_縦_ロゴ付き_日付_連番.jpg」にリネームして指定フォルダへ移動",
        ]
        for b in bullets:
            tk.Label(desc, text=b, bg="#263040", fg="#BDC8D4",
                     font=("Meiryo", 9), anchor=tk.W).pack(anchor=tk.W, padx=12, pady=1)

        # ── Canvaダウンロードフォルダ（クリックで開く） ──
        fold_row = tk.Frame(self.root, bg="#2C3E50")
        fold_row.pack(fill=tk.X, padx=16, pady=3)
        tk.Label(fold_row, text="Canvaダウンロードフォルダ：",
                 bg="#2C3E50", fg="white", font=("Meiryo", 10, "bold")).pack(side=tk.LEFT)
        self.folder_var = tk.StringVar(value="フォルダを選択してください")
        self.canva_lbl = tk.Label(fold_row, textvariable=self.folder_var,
                 bg="#34495E", fg="#5DADE2",
                 padx=6, pady=4, width=34, anchor=tk.W,
                 font=("Meiryo", 9, "underline"), cursor="hand2")
        self.canva_lbl.pack(side=tk.LEFT, padx=6)
        self.canva_lbl.bind("<Button-1>", self._open_canva_folder)
        tk.Button(fold_row, text="📂 選択", command=self._select_folder,
                  bg="#D4820A", fg="white", font=("Meiryo", 9, "bold"),
                  relief=tk.FLAT, padx=10).pack(side=tk.LEFT)
        tk.Button(fold_row, text="▶ 読み込む", command=self._load_files,
                  bg="#27AE60", fg="white", font=("Meiryo", 9, "bold"),
                  relief=tk.FLAT, padx=10).pack(side=tk.LEFT, padx=6)

        # ── 保存先フォルダ（設定から読み込み・永続化） ──
        save_row = tk.Frame(self.root, bg="#2C3E50")
        save_row.pack(fill=tk.X, padx=16, pady=3)
        tk.Label(save_row, text="保存先フォルダ：",
                 bg="#2C3E50", fg="white", font=("Meiryo", 10, "bold")).pack(side=tk.LEFT)
        saved_dest = self.config.get("step1_output_folder",
                                     str(Path.home() / "Desktop" / "bistropapa_step1"))
        self.dest_var = tk.StringVar(value=saved_dest)
        self.dest_lbl = tk.Label(save_row, textvariable=self.dest_var,
                 bg="#34495E", fg="#5DADE2",
                 padx=6, pady=4, width=34, anchor=tk.W,
                 font=("Meiryo", 9, "underline"), cursor="hand2")
        self.dest_lbl.pack(side=tk.LEFT, padx=6)
        self.dest_lbl.bind("<Button-1>", self._open_dest_folder)
        tk.Button(save_row, text="📂 変更", command=self._select_dest_folder,
                  bg="#7F8C8D", fg="white", font=("Meiryo", 9),
                  relief=tk.FLAT, padx=10).pack(side=tk.LEFT)

        # サムネイル + 操作エリア
        mid = tk.Frame(self.root, bg="#2C3E50")
        mid.pack(fill=tk.BOTH, expand=True, padx=16, pady=4)

        # サムネイルキャンバス
        thumb_outer = tk.Frame(mid, bg="#1A252F")
        thumb_outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hsc = tk.Scrollbar(thumb_outer, orient=tk.HORIZONTAL)
        vsc = tk.Scrollbar(thumb_outer, orient=tk.VERTICAL)
        self.tc = tk.Canvas(thumb_outer, bg="#1A252F",
                            xscrollcommand=hsc.set, yscrollcommand=vsc.set)
        hsc.config(command=self.tc.xview)
        vsc.config(command=self.tc.yview)
        hsc.pack(side=tk.BOTTOM, fill=tk.X)
        vsc.pack(side=tk.RIGHT, fill=tk.Y)
        self.tc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tf = tk.Frame(self.tc, bg="#1A252F")
        self.tc.create_window((0, 0), window=self.tf, anchor="nw")

        # 右操作パネル
        right = tk.Frame(mid, bg="#2C3E50", width=240)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        right.pack_propagate(False)

        # プレビュー
        tk.Label(right, text="プレビュー", bg="#2C3E50", fg="#F39C12",
                 font=("Meiryo", 9, "bold")).pack(anchor=tk.W)
        self.preview_canvas = tk.Canvas(right, bg="#1A252F",
                                        width=220, height=180,
                                        highlightthickness=0)
        self.preview_canvas.pack(pady=4)

        # 選択状況
        self.sel_lv = tk.StringVar(value="写真をクリックして選択")
        tk.Label(right, textvariable=self.sel_lv, bg="#2C3E50", fg="#F39C12",
                 font=("Meiryo", 9, "bold"), wraplength=220).pack(anchor=tk.W, pady=4)

        # 料理名入力
        tk.Label(right, text="料理名：", bg="#2C3E50", fg="white",
                 font=("Meiryo", 10, "bold")).pack(anchor=tk.W)
        self.dish_var = tk.StringVar()
        self.entry = tk.Entry(right, textvariable=self.dish_var,
                              font=("Meiryo", 12), width=18)
        self.entry.pack(fill=tk.X, pady=3)
        self.entry.bind("<Return>", lambda e: self._apply())

        tk.Button(right, text="✅ 選択に適用", command=self._apply,
                  bg="#27AE60", fg="white", font=("Meiryo", 9, "bold"),
                  relief=tk.FLAT, pady=5).pack(fill=tk.X, pady=2)
        tk.Button(right, text="全枚に適用", command=self._apply_all,
                  bg="#8E44AD", fg="white", font=("Meiryo", 9),
                  relief=tk.FLAT, pady=4).pack(fill=tk.X, pady=2)

        # 縦横切り替え
        tk.Frame(right, bg="#4A5568", height=1).pack(fill=tk.X, pady=6)
        tk.Label(right, text="向き（自動検出・手動変更可）:",
                 bg="#2C3E50", fg="white", font=("Meiryo", 8)).pack(anchor=tk.W)
        self.orient_var = tk.StringVar(value="横")
        orient_f = tk.Frame(right, bg="#2C3E50")
        orient_f.pack(fill=tk.X, pady=3)
        for o in ["横", "縦"]:
            tk.Radiobutton(orient_f, text=o, variable=self.orient_var, value=o,
                           bg="#2C3E50", fg="white", selectcolor="#2C3E50",
                           activebackground="#2C3E50", activeforeground="white",
                           font=("Meiryo", 10, "bold"),
                           command=self._apply_orient).pack(side=tk.LEFT, padx=8)

        tk.Frame(right, bg="#4A5568", height=1).pack(fill=tk.X, pady=6)

        # 全選択/解除
        sr = tk.Frame(right, bg="#2C3E50")
        sr.pack(fill=tk.X, pady=2)
        tk.Button(sr, text="全選択", command=self._select_all,
                  bg="#34495E", fg="white", font=("Meiryo", 8),
                  relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=2)
        tk.Button(sr, text="全解除", command=self._deselect_all,
                  bg="#34495E", fg="white", font=("Meiryo", 8),
                  relief=tk.FLAT, padx=8).pack(side=tk.LEFT, padx=2)

        # 設定：ロゴサフィックス
        tk.Frame(right, bg="#4A5568", height=1).pack(fill=tk.X, pady=6)
        tk.Label(right, text=f"ファイル名サフィックス:\n「{self.config.get('step1_suffix','ロゴ付き')}」",
                 bg="#2C3E50", fg="#7F8C8D", font=("Meiryo", 8),
                 justify=tk.LEFT).pack(anchor=tk.W)

        # 状況
        self.status_lv = tk.StringVar(value="ファイル: 0枚  設定済み: 0枚")
        tk.Label(right, textvariable=self.status_lv, bg="#2C3E50", fg="#ECF0F1",
                 font=("Meiryo", 8), justify=tk.LEFT).pack(anchor=tk.W, pady=4)

        # 保存
        tk.Button(self.root, text="💾  保存する（リネーム実行）",
                  command=self._save,
                  bg="#C41E3A", fg="white", font=("Meiryo", 12, "bold"),
                  relief=tk.FLAT, pady=10).pack(fill=tk.X, padx=16, pady=8)

        self.sel_indices = set()

    def _open_canva_folder(self, event=None):
        """Canvaダウンロードフォルダをエクスプローラーで開く"""
        f = self.folder_var.get()
        if not os.path.isdir(f):
            messagebox.showinfo("確認", "フォルダが選択されていません。", parent=self.root)
            return
        try:
            if sys.platform == "win32": os.startfile(f)
            elif sys.platform == "darwin": os.system(f'open "{f}"')
            else: os.system(f'xdg-open "{f}"')
        except Exception as ex:
            messagebox.showerror("エラー", str(ex), parent=self.root)

    def _open_dest_folder(self, event=None):
        """保存先フォルダをエクスプローラーで開く"""
        f = self.dest_var.get()
        if not os.path.isdir(f):
            messagebox.showinfo("確認", "フォルダが存在しません。先に保存を実行するか、フォルダを作成してください。",
                                parent=self.root)
            return
        try:
            if sys.platform == "win32": os.startfile(f)
            elif sys.platform == "darwin": os.system(f'open "{f}"')
            else: os.system(f'xdg-open "{f}"')
        except Exception as ex:
            messagebox.showerror("エラー", str(ex), parent=self.root)

    def _select_dest_folder(self):
        """保存先フォルダを変更して設定に永続化"""
        f = filedialog.askdirectory(title="保存先フォルダを選択", parent=self.root)
        if f:
            self.dest_var.set(f)
            self.config["step1_output_folder"] = f
            save_config(self.config)

    def _select_folder(self):
        f = filedialog.askdirectory(title="Canvaダウンロードフォルダを選択", parent=self.root)
        if f:
            self.folder_var.set(f)

    def _load_files(self):
        folder = self.folder_var.get()
        if not os.path.isdir(folder):
            messagebox.showerror("エラー", "有効なフォルダを選択してください", parent=self.root)
            return
        exts = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}
        self.files = sorted([f for f in Path(folder).iterdir() if f.suffix in exts])
        if not self.files:
            messagebox.showinfo("結果", "対象画像が見つかりませんでした。", parent=self.root)
            return

        # 向き自動検出
        self.file_states = {}
        for i, p in enumerate(self.files):
            orient = get_orientation(p)
            self.file_states[i] = {"dish": "", "orientation": orient}

        self.sel_indices = set()
        self.thumb_refs = {}

        # サムネイル描画
        for w in self.tf.winfo_children():
            w.destroy()

        COLS = 4
        for i, p in enumerate(self.files):
            row, col = divmod(i, COLS)
            cf = tk.Frame(self.tf, bg="#34495E", padx=2, pady=2)
            cf.grid(row=row, column=col, padx=4, pady=4)
            ph = self._load_thumb(i)
            self.thumb_refs[i] = {"ph": ph, "frame": cf}
            il = tk.Label(cf, image=ph, bg="#1A252F", cursor="hand2",
                          width=150, height=150)
            il.pack()
            orient_tag = self.file_states[i]["orientation"]
            nl = tk.Label(cf, text=f"未設定 [{orient_tag}]\n{p.name[:20]}",
                          bg="#34495E", fg="#7F8C8D",
                          font=("Meiryo", 7), wraplength=148, justify=tk.CENTER)
            nl.pack()
            self.thumb_refs[i]["img"] = il
            self.thumb_refs[i]["name"] = nl
            il.bind("<Button-1>", lambda e, idx=i: self._toggle(idx))
            nl.bind("<Button-1>", lambda e, idx=i: self._toggle(idx))

        self.tf.update_idletasks()
        self.tc.config(scrollregion=self.tc.bbox("all"))
        self._update_status()
        messagebox.showinfo("読み込み完了", f"{len(self.files)}枚の写真を読み込みました。", parent=self.root)

    def _load_thumb(self, i):
        p = self.files[i]
        try:
            img = open_image_corrected(p)
            img.thumbnail((150, 150), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _refresh(self, i):
        if i not in self.thumb_refs:
            return
        dish = self.file_states[i]["dish"]
        orient = self.file_states[i]["orientation"]
        is_sel = i in self.sel_indices
        col = "#D4820A" if is_sel else ("#27AE60" if dish else "#34495E")
        self.thumb_refs[i]["frame"].config(bg=col)
        p = self.files[i]
        self.thumb_refs[i]["name"].config(
            text=f"{dish or '未設定'} [{orient}]\n{p.name[:20]}",
            fg="#D4820A" if is_sel else ("#ECF0F1" if dish else "#7F8C8D"))

    def _toggle(self, i):
        if i in self.sel_indices:
            self.sel_indices.discard(i)
        else:
            self.sel_indices.add(i)
        self._refresh(i)
        self._on_select_change()

    def _on_select_change(self):
        if not self.sel_indices:
            self.sel_lv.set("写真をクリックして選択")
            self.preview_canvas.delete("all")
            return
        n = len(self.sel_indices)
        self.sel_lv.set(f"{n}枚選択中")
        # 代表プレビュー（最後に選んだもの）
        last = max(self.sel_indices)
        self._update_preview(last)
        # 同じ料理名なら入力欄に反映
        dishes = {self.file_states[j]["dish"] for j in self.sel_indices}
        if len(dishes) == 1 and list(dishes)[0]:
            self.dish_var.set(list(dishes)[0])
        # 向きを反映
        orients = {self.file_states[j]["orientation"] for j in self.sel_indices}
        if len(orients) == 1:
            self.orient_var.set(list(orients)[0])
        self.entry.focus_set()

    def _update_preview(self, i):
        p = self.files[i]
        try:
            img = open_image_corrected(p)
            img.thumbnail((218, 178), Image.LANCZOS)
            ph = ImageTk.PhotoImage(img)
            self._preview_ph = ph  # 参照保持
            self.preview_canvas.delete("all")
            self.preview_canvas.create_image(110, 90, anchor=tk.CENTER, image=ph)
        except Exception:
            pass

    def _apply(self):
        name = self.dish_var.get().strip()
        if not name:
            self.entry.config(bg="#FFCCCC")
            self.root.after(400, lambda: self.entry.config(bg="white"))
            return
        if not self.sel_indices:
            messagebox.showwarning("確認", "写真を選択してから適用してください", parent=self.root)
            return
        for j in list(self.sel_indices):
            self.file_states[j]["dish"] = name
            self._refresh(j)
        self.sel_indices.clear()
        self.dish_var.set("")
        self.sel_lv.set("✅ 適用しました！次の写真を選択してください")
        self._update_status()

    def _apply_all(self):
        name = self.dish_var.get().strip()
        if not name:
            messagebox.showwarning("確認", "料理名を入力してください", parent=self.root)
            return
        for j in range(len(self.files)):
            self.file_states[j]["dish"] = name
            self._refresh(j)
        self.sel_lv.set(f"全{len(self.files)}枚に「{name}」を設定しました")
        self._update_status()

    def _apply_orient(self):
        orient = self.orient_var.get()
        for j in list(self.sel_indices):
            self.file_states[j]["orientation"] = orient
            self._refresh(j)

    def _select_all(self):
        for j in range(len(self.files)):
            self.sel_indices.add(j)
            self._refresh(j)
        self._on_select_change()

    def _deselect_all(self):
        self.sel_indices.clear()
        for j in range(len(self.files)):
            self._refresh(j)
        self.sel_lv.set("写真をクリックして選択")

    def _update_status(self):
        total = len(self.files)
        done = sum(1 for s in self.file_states.values() if s["dish"])
        self.status_lv.set(f"ファイル: {total}枚  設定済み: {done}枚")

    def _save(self):
        if not self.files:
            messagebox.showwarning("確認", "先にフォルダを読み込んでください。", parent=self.root)
            return
        unnamed = [i for i in range(len(self.files)) if not self.file_states[i]["dish"]]
        if unnamed:
            if not messagebox.askyesno("確認",
                    f"{len(unnamed)}枚に料理名が未設定です。\n「料理」として保存しますか？",
                    parent=self.root):
                return
            for i in unnamed:
                self.file_states[i]["dish"] = "料理"

        out_folder = self.dest_var.get()
        if not out_folder:
            messagebox.showerror("エラー", "保存先フォルダを指定してください。", parent=self.root)
            return
        # 保存先を設定に永続化
        self.config["step1_output_folder"] = out_folder
        save_config(self.config)

        suffix = self.config.get("step1_suffix", "ロゴ付き")
        date_str = datetime.datetime.now().strftime("%Y%m%d")
        Path(out_folder).mkdir(parents=True, exist_ok=True)
        dlg = ProgressDialog(self.root, "リネーム保存中...", len(self.files))
        results = []

        def worker():
            import shutil
            used_names = set(f.name for f in Path(out_folder).iterdir() if f.is_file())
            for i, p in enumerate(self.files):
                if dlg.cancelled: break
                dish = self.file_states[i]["dish"]
                orient = self.file_states[i]["orientation"]
                try:
                    img = open_image_corrected(p)
                    # 連番ファイル名生成（保存先フォルダで重複しない）
                    base = f"{dish}_{orient}"
                    if suffix:
                        base += f"_{suffix}"
                    base += f"_{date_str}"
                    counter = 1
                    while True:
                        fn = f"{base}_{counter:03d}.jpg"
                        if fn not in used_names:
                            break
                        counter += 1
                    used_names.add(fn)
                    dest = Path(out_folder) / fn
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    # 一時ファイル経由でアトミック保存
                    tmp = dest.with_suffix(".tmp.jpg")
                    img.save(str(tmp), "JPEG", quality=95)
                    tmp.replace(dest)
                    # 元ファイルをCanvaダウンロードフォルダから削除（移動完了）
                    if p.exists():
                        p.unlink()
                    results.append({"original": p.name, "new_name": fn,
                                    "dish": dish, "orientation": orient,
                                    "path": str(dest)})
                    self.root.after(0, lambda n=i+1, nm=fn: dlg.update(n, f"移動: {nm}"))
                except Exception as ex:
                    self.root.after(0, lambda m=str(ex): print(f"保存エラー: {m}"))

            self.root.after(0, dlg.close)
            # undoログ保存（移動操作）
            canva_folder = self.folder_var.get()
            undo_entries = [
                {"type": "move",
                 "new": r["new_name"],
                 "original_name": r["original"],
                 "src_folder": canva_folder,
                 "dest_folder": out_folder}
                for r in results
            ]
            save_undo_log(undo_entries)
            self.root.after(200, lambda: ResultsWindow(self.root, results, out_folder))

        threading.Thread(target=worker, daemon=True).start()


# =============================================================================
# 結果ウィンドウ（共通）
# =============================================================================
class ResultsWindow:
    def __init__(self, parent, results, out_folder):
        win = tk.Toplevel(parent)
        win.title("処理完了")
        win.geometry("640x460")
        win.configure(bg="#2C3E50")

        tk.Label(win, text=f"✅  {len(results)}枚の処理が完了しました",
                 bg="#27AE60", fg="white", font=("Meiryo", 12, "bold"),
                 pady=10).pack(fill=tk.X)

        frm = tk.Frame(win, bg="#2C3E50")
        frm.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)
        txt = tk.Text(frm, bg="#1A252F", fg="#ECF0F1", font=("Meiryo", 9))
        sb = tk.Scrollbar(frm, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        txt.pack(fill=tk.BOTH, expand=True)

        for r in results:
            txt.insert(tk.END, f"✅ {r['dish']} [{r['orientation']}]\n")
            txt.insert(tk.END, f"   {r['original']}  →  {r['new_name']}\n\n")
        txt.config(state=tk.DISABLED)

        bf = tk.Frame(win, bg="#2C3E50")
        bf.pack(pady=10)
        tk.Button(bf, text="📁 出力フォルダを開く",
                  command=lambda: (os.startfile(out_folder) if sys.platform == "win32"
                                   else os.system(f'open "{out_folder}"')),
                  bg="#F39C12", fg="white", font=("Meiryo", 10, "bold"),
                  relief=tk.FLAT, padx=14, pady=5).pack(side=tk.LEFT, padx=5)

        def do_undo():
            entries = load_undo_log()
            if not entries:
                messagebox.showinfo("確認", "取り消せる操作がありません。", parent=win)
                return
            if not messagebox.askyesno("取り消し確認",
                    f"直前の操作（{len(entries)}枚）を取り消しますか？\n"
                    "ファイル名・場所が元に戻ります。",
                    parent=win):
                return
            msgs = execute_undo(entries)
            clear_undo_log()
            result_text = "\n".join(msgs)
            messagebox.showinfo("取り消し完了", result_text, parent=win)
            win.destroy()

        tk.Button(bf, text="↩ 取り消す",
                  command=do_undo,
                  bg="#E67E22", fg="white", font=("Meiryo", 10, "bold"),
                  relief=tk.FLAT, padx=14, pady=5).pack(side=tk.LEFT, padx=5)
        tk.Button(bf, text="閉じる", command=win.destroy,
                  bg="#7F8C8D", fg="white", font=("Meiryo", 10),
                  relief=tk.FLAT, padx=14).pack(side=tk.LEFT, padx=5)


# =============================================================================
# 設定ダイアログ
# =============================================================================
class SettingsDialog:
    def __init__(self, parent, config):
        self.config = config
        win = tk.Toplevel(parent)
        win.title("設定")
        win.geometry("540x520")
        win.configure(bg="#2C3E50")
        win.grab_set()
        self.win = win

        tk.Label(win, text="⚙️  設定", bg="#C41E3A", fg="white",
                 font=("Meiryo", 12, "bold"), pady=9).pack(fill=tk.X)

        nb = ttk.Notebook(win)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        # ── タブ1: API
        f1 = tk.Frame(nb, bg="#2C3E50", padx=15, pady=10)
        nb.add(f1, text=" API設定 ")

        def lbl(p, t):
            tk.Label(p, text=t, bg="#2C3E50", fg="#ECF0F1",
                     font=("Meiryo", 9)).pack(anchor=tk.W, pady=(10, 2))

        lbl(f1, "Anthropic API Key（AI料理名判定）")
        self.api_var = tk.StringVar(value=config.get("anthropic_api_key", ""))
        tk.Entry(f1, textvariable=self.api_var, show="*", width=52,
                 font=("Meiryo", 9)).pack(fill=tk.X)

        lbl(f1, "Canva API Key（自動アップロード・省略可）")
        self.canva_var = tk.StringVar(value=config.get("canva_api_key", ""))
        tk.Entry(f1, textvariable=self.canva_var, show="*", width=52,
                 font=("Meiryo", 9)).pack(fill=tk.X)

        lbl(f1, "AI並列処理数（1〜5）")
        self.conc_var = tk.StringVar(value=str(config.get("ai_concurrent", 3)))
        tk.Entry(f1, textvariable=self.conc_var, width=5, font=("Meiryo", 9)).pack(anchor=tk.W)

        # ── タブ2: フォルダ
        f2 = tk.Frame(nb, bg="#2C3E50", padx=15, pady=10)
        nb.add(f2, text=" フォルダ設定 ")

        lbl(f2, "ステップ０ 出力フォルダ")
        self.out0_var = tk.StringVar(value=config.get("step0_output_folder", ""))
        r0 = tk.Frame(f2, bg="#2C3E50"); r0.pack(fill=tk.X)
        tk.Entry(r0, textvariable=self.out0_var, width=40, font=("Meiryo", 9)).pack(side=tk.LEFT)
        tk.Button(r0, text="参照", command=lambda: self.out0_var.set(
            filedialog.askdirectory() or self.out0_var.get()),
                  bg="#7F8C8D", fg="white", relief=tk.FLAT).pack(side=tk.LEFT, padx=5)

        lbl(f2, "ステップ１ 出力フォルダ（Canvaリネーム後）")
        self.out1_var = tk.StringVar(value=config.get("step1_output_folder", ""))
        r1 = tk.Frame(f2, bg="#2C3E50"); r1.pack(fill=tk.X)
        tk.Entry(r1, textvariable=self.out1_var, width=40, font=("Meiryo", 9)).pack(side=tk.LEFT)
        tk.Button(r1, text="参照", command=lambda: self.out1_var.set(
            filedialog.askdirectory() or self.out1_var.get()),
                  bg="#7F8C8D", fg="white", relief=tk.FLAT).pack(side=tk.LEFT, padx=5)

        lbl(f2, "ステップ１ファイル名サフィックス（例: ロゴ付き）")
        self.suffix_var = tk.StringVar(value=config.get("step1_suffix", "ロゴ付き"))
        tk.Entry(f2, textvariable=self.suffix_var, width=20, font=("Meiryo", 9)).pack(anchor=tk.W)

        # ── タブ3: フィルタ
        f3 = tk.Frame(nb, bg="#2C3E50", padx=15, pady=10)
        nb.add(f3, text=" 品質フィルタ ")

        lbl(f3, "輝度最小値（暗さ下限。推奨: 40）")
        self.bmin_var = tk.StringVar(value=str(config.get("brightness_min", 40)))
        tk.Entry(f3, textvariable=self.bmin_var, width=8, font=("Meiryo", 9)).pack(anchor=tk.W)

        lbl(f3, "輝度最大値（明るさ上限。推奨: 220）")
        self.bmax_var = tk.StringVar(value=str(config.get("brightness_max", 220)))
        tk.Entry(f3, textvariable=self.bmax_var, width=8, font=("Meiryo", 9)).pack(anchor=tk.W)

        tk.Button(win, text="💾  保存", command=self._save,
                  bg="#27AE60", fg="white", font=("Meiryo", 11, "bold"),
                  relief=tk.FLAT, pady=8).pack(fill=tk.X, padx=10, pady=8)

    def _save(self):
        c = self.config
        c["anthropic_api_key"]   = self.api_var.get().strip()
        c["canva_api_key"]       = self.canva_var.get().strip()
        c["step0_output_folder"] = self.out0_var.get().strip()
        c["step1_output_folder"] = self.out1_var.get().strip()
        c["step1_suffix"]        = self.suffix_var.get().strip() or "ロゴ付き"
        try:
            c["ai_concurrent"]   = max(1, min(5, int(self.conc_var.get())))
            c["brightness_min"]  = int(self.bmin_var.get())
            c["brightness_max"]  = int(self.bmax_var.get())
        except ValueError:
            pass
        save_config(c)
        messagebox.showinfo("保存", "設定を保存しました ✅", parent=self.win)
        self.win.destroy()


# =============================================================================
# エントリーポイント
# =============================================================================
def main():
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    LauncherApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
