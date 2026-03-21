# フォトちゃん SPEC.md
最終更新：2026-03-21

---

## 現在のバージョン：v2.0.1

## ファイル構成
```
bistropapa_photo_tool_v2/
├── fotochan.py          # メインファイル（唯一触るファイル）
├── SPEC.md              # このファイル
├── .gitignore           # config類を除外済み
├── fotochan_config.json # ★Gitに入れない（APIキー含む）
└── config.json          # ★Gitに入れない
```

※ bistropapa_tool.py は旧バージョン。参照・編集禁止。

---

## 実装済み機能

### Step 0（SDカード取り込み & 品質選別）
- [x] SDカード/フォルダ選択
- [x] 日付フォルダ作成 & 写真移動
- [x] 品質フィルター削除済み（全写真を選別画面に渡す）
- [x] 選別ビューワー（←→/Enter/Delete/R回転）
- [x] 料理名入力 & グルーピング
- [x] ファイルリネーム（料理名_縦or横_日付_連番.jpg）
- [x] 料理名1つ戻すUNDO機能

### Step 1（Canvaダウンロード後リネーム）
- [x] Canvaダウンロードフォルダ指定
- [x] 料理名入力してリネーム

### 設定
- [x] Anthropic APIキー入力欄
- [x] 出力フォルダ設定
- [x] config類はGitignore済み（APIキー漏洩防止）

---

## Git履歴（直近）
```
3ffe8f5 security: config類をgitignoreに追加
4ec1dfd feat: 料理名1つ戻す機能追加
62c614e remove config.json from tracking
ae56c13 fix: 品質フィルター完全削除 v2.0.1
```

---

## TODO（優先順）

### v2.1.0（次のマイルストーン）
- [ ] SDカードのデフォルトパス E:\DCIM を永続化
- [ ] Step 0のボタン整理（不要ボタン削除）
- [ ] Claude API 料理名自動判定モード
- [ ] PyInstaller で .exe 化
- [ ] GitHub自動アップデート機能

---

## 開発ルール（Cursor用）

```
【Cursorへの指示テンプレート】
fotochan.pyを参照。
今回のタスク：〇〇の部分だけ修正。
変更箇所のコードのみ出力。全体出力不要。
```

- 編集対象は fotochan.py のみ
- bistropapa_tool.py は触らない
- config.json / fotochan_config.json はGitにコミットしない
- コミットメッセージ形式：`feat:` / `fix:` / `security:` / `docs:`

---

## 次セッションの始め方

このSPEC.mdの内容をClaudeに貼り付けて：

```
SPEC.mdを共有します。
今回のタスク：〇〇
変更箇所のコードのみ出力。全体出力不要。
```

これだけで即スタートできます。
