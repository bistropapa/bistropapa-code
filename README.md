# 料理写真整理ツール（Windows向け / Python）

料理写真を日付ベースで整理し、ファイル名を統一し、Canvaで補正した画像を自動で `03_edited` に振り分けるGUIツールです。

## できること

- 料理名・元写真フォルダ・CanvaダウンロードフォルダをGUIで指定
- 以下フォルダ構造を自動作成

```text
料理写真
 └ YYYY
     └ YYYY-MM
         └ YYYY-MM-DD_料理名
             ├ 01_original
             ├ 02_selected
             ├ 03_edited
             └ 04_posted
```

- 元写真を撮影日（EXIF優先、なければファイル更新日時）で整理
- 自動リネーム
  - `YYYYMMDD_料理名_01_org`
  - `YYYYMMDD_料理名_02_org`
- 選択画像を `02_selected` へ移動
- Canvaダウンロードフォルダ監視
  - 新規画像を `03_edited` へ移動
  - `YYYYMMDD_料理名_01_edit` の命名
- 追加機能（任意）
  - 明るさ補正
  - リサイズ
  - PNG/JPG変換

---

## 1. 必要なPythonライブラリ

Python 3.10以上を推奨します（Windows 11）。

```bash
pip install pillow watchdog
```

---

## 2. 実行方法（Windows）

1. このプロジェクトフォルダを開く
2. PowerShell または コマンドプロンプトで以下を実行

```bash
python photo_organizer_gui.py
```

3. GUIで以下を入力
   - 料理名
   - 元写真フォルダ
   - Canvaダウンロードフォルダ
   - 出力ルート（初期値: `ユーザー/Pictures/料理写真`）
4. `実行（元写真整理）` を押す
5. `選択画像を02_selectedへ移動` で必要画像を選択
6. Canvaから書き出した画像を自動整理したい場合は `Canva監視開始`

---

## 3. 命名ルール

- 元画像（01_original）
  - `YYYYMMDD_料理名_01_org.jpg`
- Canva補正画像（03_edited）
  - `YYYYMMDD_料理名_01_edit.png`

番号はフォルダ内で自動採番されます。

---

## 4. exe化（配布用）

PyInstallerを使うと、Python未導入のPCでも実行しやすくなります。

### インストール

```bash
pip install pyinstaller
```

### exe作成

```bash
pyinstaller --noconfirm --onefile --windowed --name RyoriPhotoOrganizer photo_organizer_gui.py
```

### 出力先

- `dist/RyoriPhotoOrganizer.exe`

---

## 5. 補足（初心者向け）

- 最初はテスト用のフォルダで試してください。
- Canva監視中は、Canvaダウンロード先をGUIで指定したフォルダに合わせてください。
- 監視停止はGUIの `Canva監視停止` ボタン、またはウィンドウ終了時に自動停止します。
