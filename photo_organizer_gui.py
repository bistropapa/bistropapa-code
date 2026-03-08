import os
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ExifTags, ImageEnhance
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass
class AppConfig:
    dish_name: str
    source_folder: Path
    canva_folder: Path
    output_base: Path
    apply_brightness: bool
    brightness_factor: float
    apply_resize: bool
    max_width: int
    max_height: int
    convert_format: str  # "keep", "jpg", "png"


class ImageOrganizer:
    def __init__(self, config: AppConfig, log_callback):
        self.config = config
        self.log = log_callback
        self.current_day_folder = None

    def sanitize_dish_name(self, dish_name: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        clean_name = ''.join('_' if c in invalid_chars else c for c in dish_name.strip())
        return clean_name.replace(' ', '_')

    def get_photo_taken_date(self, image_path: Path) -> datetime:
        try:
            with Image.open(image_path) as img:
                exif = img.getexif()
                if exif:
                    exif_dict = {ExifTags.TAGS.get(tag, tag): value for tag, value in exif.items()}
                    date_str = exif_dict.get("DateTimeOriginal") or exif_dict.get("DateTime")
                    if date_str:
                        return datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
        except Exception:
            pass

        return datetime.fromtimestamp(image_path.stat().st_mtime)

    def ensure_day_folders(self, shot_date: datetime) -> dict:
        year = shot_date.strftime("%Y")
        month = shot_date.strftime("%Y-%m")
        day = shot_date.strftime("%Y-%m-%d")
        dish = self.sanitize_dish_name(self.config.dish_name)

        base = self.config.output_base / year / month / f"{day}_{dish}"
        folders = {
            "day": base,
            "original": base / "01_original",
            "selected": base / "02_selected",
            "edited": base / "03_edited",
            "posted": base / "04_posted",
        }

        for folder in folders.values():
            folder.mkdir(parents=True, exist_ok=True)

        self.current_day_folder = folders
        return folders

    def _build_filename(self, shot_date: datetime, index: int, suffix: str, extension: str) -> str:
        date_part = shot_date.strftime("%Y%m%d")
        dish = self.sanitize_dish_name(self.config.dish_name)
        return f"{date_part}_{dish}_{index:02d}_{suffix}{extension}"

    def _next_index(self, folder: Path, shot_date: datetime, suffix: str) -> int:
        date_part = shot_date.strftime("%Y%m%d")
        dish = self.sanitize_dish_name(self.config.dish_name)
        prefix = f"{date_part}_{dish}_"
        matching = [p for p in folder.glob("*.*") if p.name.startswith(prefix) and p.stem.endswith(f"_{suffix}")]
        return len(matching) + 1

    def _apply_optional_edits(self, image_path: Path) -> Path:
        if not (self.config.apply_brightness or self.config.apply_resize or self.config.convert_format != "keep"):
            return image_path

        with Image.open(image_path) as img:
            if self.config.apply_brightness:
                enhancer = ImageEnhance.Brightness(img)
                img = enhancer.enhance(self.config.brightness_factor)

            if self.config.apply_resize:
                img.thumbnail((self.config.max_width, self.config.max_height))

            target_suffix = image_path.suffix.lower()
            save_format = None
            if self.config.convert_format == "jpg":
                target_suffix = ".jpg"
                save_format = "JPEG"
            elif self.config.convert_format == "png":
                target_suffix = ".png"
                save_format = "PNG"

            temp_output = image_path.with_name(f"temp_{image_path.stem}{target_suffix}")
            if save_format == "JPEG" and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            img.save(temp_output, format=save_format)

        if temp_output != image_path:
            image_path.unlink(missing_ok=True)

        return temp_output

    def organize_original_images(self) -> tuple[dict, list[Path]]:
        image_files = [
            p for p in self.config.source_folder.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        if not image_files:
            raise ValueError("元写真フォルダに画像が見つかりませんでした。")

        moved_files = []
        folder_map = None

        for src in sorted(image_files):
            shot_date = self.get_photo_taken_date(src)
            folder_map = self.ensure_day_folders(shot_date)
            index = self._next_index(folder_map["original"], shot_date, "org")
            ext = src.suffix.lower()
            new_name = self._build_filename(shot_date, index, "org", ext)
            dst = folder_map["original"] / new_name

            shutil.move(str(src), dst)
            edited_path = self._apply_optional_edits(dst)
            if edited_path != dst:
                dst = folder_map["original"] / self._build_filename(shot_date, index, "org", edited_path.suffix.lower())
                edited_path.rename(dst)

            moved_files.append(dst)
            self.log(f"移動完了: {src.name} -> {dst}")

        return folder_map, moved_files

    def move_selected_images(self, selected_paths: list[Path], folder_map: dict, shot_date: datetime):
        for src in selected_paths:
            if not src.exists():
                continue
            index = self._next_index(folder_map["selected"], shot_date, "org")
            new_name = self._build_filename(shot_date, index, "org", src.suffix.lower())
            dst = folder_map["selected"] / new_name
            shutil.move(str(src), dst)
            self.log(f"選択画像を移動: {src.name} -> {dst}")

    def move_canva_image(self, file_path: Path):
        if not file_path.exists() or file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return

        shot_date = datetime.now()
        folder_map = self.current_day_folder or self.ensure_day_folders(shot_date)
        index = self._next_index(folder_map["edited"], shot_date, "edit")
        new_name = self._build_filename(shot_date, index, "edit", file_path.suffix.lower())
        dst = folder_map["edited"] / new_name

        try:
            shutil.move(str(file_path), dst)
            self.log(f"Canva画像を移動: {file_path.name} -> {dst}")
        except Exception as e:
            self.log(f"Canva画像移動エラー: {e}")


class CanvaHandler(FileSystemEventHandler):
    def __init__(self, organizer: ImageOrganizer):
        super().__init__()
        self.organizer = organizer

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        # ダウンロード完了まで少し待つ
        for _ in range(5):
            try:
                if path.exists() and path.stat().st_size > 0:
                    break
            except Exception:
                pass
            threading.Event().wait(0.6)
        self.organizer.move_canva_image(path)


class OrganizerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("料理写真整理ツール")
        self.root.geometry("760x640")

        self.observer = None
        self.organizer = None
        self.last_folder_map = None
        self.last_moved_files = []

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        ttk.Label(frame, text="料理名").grid(row=0, column=0, sticky="w", **pad)
        self.dish_name_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.dish_name_var, width=50).grid(row=0, column=1, sticky="we", **pad)

        ttk.Label(frame, text="元写真フォルダ").grid(row=1, column=0, sticky="w", **pad)
        self.source_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.source_var, width=50).grid(row=1, column=1, sticky="we", **pad)
        ttk.Button(frame, text="参照", command=lambda: self._pick_folder(self.source_var)).grid(row=1, column=2, **pad)

        ttk.Label(frame, text="Canvaダウンロードフォルダ").grid(row=2, column=0, sticky="w", **pad)
        self.canva_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.canva_var, width=50).grid(row=2, column=1, sticky="we", **pad)
        ttk.Button(frame, text="参照", command=lambda: self._pick_folder(self.canva_var)).grid(row=2, column=2, **pad)

        ttk.Label(frame, text="出力ルートフォルダ（料理写真）").grid(row=3, column=0, sticky="w", **pad)
        self.output_var = tk.StringVar(value=str(Path.home() / "Pictures" / "料理写真"))
        ttk.Entry(frame, textvariable=self.output_var, width=50).grid(row=3, column=1, sticky="we", **pad)
        ttk.Button(frame, text="参照", command=lambda: self._pick_folder(self.output_var)).grid(row=3, column=2, **pad)

        ttk.Separator(frame, orient="horizontal").grid(row=4, column=0, columnspan=3, sticky="we", pady=8)

        self.brightness_var = tk.BooleanVar(value=False)
        self.resize_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="明るさ補正", variable=self.brightness_var).grid(row=5, column=0, sticky="w", **pad)
        ttk.Checkbutton(frame, text="リサイズ", variable=self.resize_var).grid(row=6, column=0, sticky="w", **pad)

        ttk.Label(frame, text="明るさ(1.0=変更なし)").grid(row=5, column=1, sticky="w", **pad)
        self.brightness_factor_var = tk.StringVar(value="1.1")
        ttk.Entry(frame, textvariable=self.brightness_factor_var, width=12).grid(row=5, column=2, sticky="w", **pad)

        ttk.Label(frame, text="最大サイズ (幅x高さ)").grid(row=6, column=1, sticky="w", **pad)
        resize_wrap = ttk.Frame(frame)
        resize_wrap.grid(row=6, column=2, sticky="w", **pad)
        self.max_width_var = tk.StringVar(value="1920")
        self.max_height_var = tk.StringVar(value="1080")
        ttk.Entry(resize_wrap, textvariable=self.max_width_var, width=8).pack(side="left")
        ttk.Label(resize_wrap, text="x").pack(side="left", padx=4)
        ttk.Entry(resize_wrap, textvariable=self.max_height_var, width=8).pack(side="left")

        ttk.Label(frame, text="形式変換").grid(row=7, column=0, sticky="w", **pad)
        self.format_var = tk.StringVar(value="keep")
        ttk.Combobox(frame, textvariable=self.format_var, values=["keep", "jpg", "png"], state="readonly", width=10).grid(row=7, column=1, sticky="w", **pad)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=8, column=0, columnspan=3, pady=10, sticky="we")
        ttk.Button(button_frame, text="実行（元写真整理）", command=self.run_organize).pack(side="left", padx=5)
        ttk.Button(button_frame, text="選択画像を02_selectedへ移動", command=self.move_selected).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Canva監視開始", command=self.start_watch).pack(side="left", padx=5)
        ttk.Button(button_frame, text="Canva監視停止", command=self.stop_watch).pack(side="left", padx=5)

        ttk.Label(frame, text="ログ").grid(row=9, column=0, sticky="w", **pad)
        self.log_text = tk.Text(frame, height=16)
        self.log_text.grid(row=10, column=0, columnspan=3, sticky="nsew", **pad)

        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(10, weight=1)

    def _pick_folder(self, variable: tk.StringVar):
        path = filedialog.askdirectory()
        if path:
            variable.set(path)

    def _log(self, message: str):
        now = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{now}] {message}\n")
        self.log_text.see("end")

    def _build_config(self) -> AppConfig:
        dish = self.dish_name_var.get().strip()
        source = Path(self.source_var.get().strip())
        canva = Path(self.canva_var.get().strip())
        output = Path(self.output_var.get().strip())

        if not dish:
            raise ValueError("料理名を入力してください。")
        if not source.exists():
            raise ValueError("元写真フォルダが存在しません。")
        if not canva.exists():
            raise ValueError("Canvaダウンロードフォルダが存在しません。")

        return AppConfig(
            dish_name=dish,
            source_folder=source,
            canva_folder=canva,
            output_base=output,
            apply_brightness=self.brightness_var.get(),
            brightness_factor=float(self.brightness_factor_var.get()),
            apply_resize=self.resize_var.get(),
            max_width=int(self.max_width_var.get()),
            max_height=int(self.max_height_var.get()),
            convert_format=self.format_var.get(),
        )

    def run_organize(self):
        try:
            config = self._build_config()
            self.organizer = ImageOrganizer(config, self._log)
            folder_map, moved_files = self.organizer.organize_original_images()
            self.last_folder_map = folder_map
            self.last_moved_files = moved_files
            self._log("元写真の整理が完了しました。")
            messagebox.showinfo("完了", "元写真の整理が完了しました。")
        except Exception as e:
            messagebox.showerror("エラー", str(e))
            self._log(f"エラー: {e}")

    def move_selected(self):
        if not self.organizer or not self.last_folder_map:
            messagebox.showwarning("注意", "先に『実行（元写真整理）』を行ってください。")
            return

        selected = filedialog.askopenfilenames(
            title="02_selectedへ移動する画像を選択",
            filetypes=[("Image files", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff")],
            initialdir=str(self.last_folder_map["original"])
        )
        if not selected:
            return

        sample_date = datetime.now()
        if self.last_moved_files:
            sample_date = self.organizer.get_photo_taken_date(self.last_moved_files[0])

        self.organizer.move_selected_images([Path(p) for p in selected], self.last_folder_map, sample_date)
        self._log("選択画像の移動が完了しました。")

    def start_watch(self):
        if self.observer:
            self._log("Canva監視はすでに開始されています。")
            return

        try:
            config = self._build_config()
            if not self.organizer:
                self.organizer = ImageOrganizer(config, self._log)
            self.observer = Observer()
            handler = CanvaHandler(self.organizer)
            self.observer.schedule(handler, str(config.canva_folder), recursive=False)
            self.observer.start()
            self._log("Canvaダウンロードフォルダの監視を開始しました。")
        except Exception as e:
            messagebox.showerror("エラー", str(e))
            self._log(f"エラー: {e}")

    def stop_watch(self):
        if not self.observer:
            return
        self.observer.stop()
        self.observer.join(timeout=2)
        self.observer = None
        self._log("Canva監視を停止しました。")


def main():
    root = tk.Tk()
    app = OrganizerGUI(root)

    def on_close():
        app.stop_watch()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
