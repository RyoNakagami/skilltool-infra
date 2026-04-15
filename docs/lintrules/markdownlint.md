---
author: "Ryo Nakagami"
date-modified: "2026-04-15"
project: skill-tool
---

# Markdownlint ルールドキュメント

| ルール   | 有効か  | 適用範囲・補足           |
| ----- | ---- | ----------------- |
| MD013 | ✅ 有効 | 80文字制限，テーブルは除外    |
| MD033 | ❌ 無効 | インライン HTML の使用を許可 |

## MD013 – 行の長さ (Line Length)

- MD013 は Markdown の各行の最大文字数を制限するルール
- 可読性を確保しつつ，テーブルについては柔軟性を持たせる設定

### MD013 VScode用設定例

```json
"MD013": {
  "tables": false,
  "code_blocks": true,
  "headings": true,
  "line_length": 80
}
```

| オプション       | 値     | 説明                                       |
| ----------- | ----- | ---------------------------------------- |
| `tables`      | `false` | テーブル内の行は行長制限を適用しない |
| `code_blocks` | `true`  | コードブロック内は行長制限を適用       |
| `headings`    | `true`  | 見出しにも行長制限を適用        |
| `line_length` | `80`    | 最大文字数を 80文字 に設定                      |

## MD033 – インライン HTML

- MD033 は Markdown 内でのインライン HTML の使用を警告するルール
- `false`に設定することで，HTMLを組み合わせた高度な書式設定を可能にする

### MD033 VScode用設定例

```json
"MD033": false
```

## MD036 – 見出しの前後のスペース（No Emphasis As Header）

- MD036は，強調（**text** や _text_）を見出しとして使用している場合に警告を出すルール
- 例: `**Heading**` や `_Heading_` のような書き方はNG
- `false` に設定することで，この警告を無効化

### MD036 VSCode用設定例

```json
"MD036": false
```
