---
author: "xxxxx"
date-modified: "2026-04-15"
project: skill-tool
---

# Versioning Policy

- `skill-tool` のバージョン管理は [Semantic Versioning](https://semver.org/)に基づいています
- リリース番号は `MAJOR.MINOR.PATCH` 形式で管理されます

## MAJOR.MINOR.PATCH

| リリース種別 | 主な内容 | 後方互換性 | 主な適用例 |
| :-------------- | :---------- | :------------ | :------------ |
| **Major Release** | - 後方互換性のない変更（breaking changes）<br>- 廃止予定の削除<br>- 仕様変更を伴うAPIの更新<br>- Major release に含まれる変更は **Release Note** に記録| ❌ なし | `v1.0.0 → v2.0.0` |
| **Minor Release** | - 新機能の追加<br>- 大規模なバグ修正<br>- deprecationアナウンスの追加 | ✅ あり | `v1.1.0 → v1.2.0` |
| **Patch Release** | - バグ修正<br>- 動作安定性・性能改善（非破壊）<br>- 既存コードが問題なく動作することを保証 | ✅ あり | `v1.2.1 → v1.2.2` |

---

### Deprecation Policy

`skill-tool` では、非推奨化 (deprecation) のプロセスを以下の方針で実施します。

1. **Minor Release** で deprecation をアナウンスします。
2. 警告メッセージには次の 2 点を明示します：
   - 代替手段（replacement method / attribute）
   - 廃止が強制される予定のバージョン（例：`will be removed in 2.0.0`）
3. アナウンス後、同一メジャーバージョン内 (`1.x`) では引き続き動作します。
4. 次の **Major Release** (`2.0.0`) で削除が行われます。

---

### Example: Deprecation Flow

| バージョン | 状態 | 内容 |
| :----------- | :------ | :------ |
| `1.2.0` | 🔔 アナウンス | 関数 `old_method()` を非推奨化。代替として `new_method()` を案内。 |
| `1.3.0` | ⚠ 継続警告 | 警告付きで引き続き動作。移行推奨。 |
| `2.0.0` | ⛔ 削除 | `old_method()` を完全削除。 |

---

## References

- [Semantic Versioning](https://semver.org/)
- [Python Package Building Techniques for Regmonkeys > Versioning Policy](https://ryonakagami.github.io/python-statisticalpackage-techniques/posts/python-packaging-guide/versioning.html)
