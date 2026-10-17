# Firestore インデックス設定手順

## 概要
統一会話システム導入により、新しいFirestoreインデックスの作成が必要です。

## 必要なインデックス

### 1. conversations コレクション用

**実際には1つのインデックスのみ必要**
- コレクション: `conversations`
- フィールド:
  - `user_id` (昇順)
  - `message_type` (昇順)  
  - `timestamp` (降順)

**注意**: 現在の実装では暫定対応により、複合インデックスを使用せずシンプルなクエリに変更済み。
将来的にパフォーマンス向上のため上記インデックス作成を推奨。

### 2. conversation_archives コレクション用

**アーカイブ取得用インデックス**
- コレクション: `conversation_archives`
- フィールド:
  - `user_id` (昇順)
  - `archived_at` (降順)

**注意**: 現在の実装では暫定対応により、このインデックスも使用せずシンプルなクエリに変更済み。

## 設定手順

1. Google Cloud Console にアクセス
   https://console.firebase.google.com/project/discord-bot-project-469211/firestore/indexes

2. 「インデックスを作成」をクリック

3. 上記の各インデックスを順番に作成

## 自動生成されるインデックス作成リンク

エラーログに表示される以下のURLをクリックすると自動的にインデックス作成画面に移動します：

### conversations コレクション用
```
https://console.firebase.google.com/v1/r/project/discord-bot-project-469211/firestore/indexes?create_composite=CmBwcm9qZWN0cy9kaXNjb3JkLWJvdC1wcm9qZWN0LTQ2OTIxMS9kYXRhYmFzZXMvKGRlZmF1bHQpL2NvbGxlY3Rpb25Hcm91cHMvY29udmVyc2F0aW9ucy9pbmRleGVzL18QARoQCgxtZXNzYWdlX3R5cGUQARoLCgd1c2VyX2lkEAEaDQoJdGltZXN0YW1wEAIaDAoIX19uYW1lX18QAg
```

### conversation_archives コレクション用
```
https://console.firebase.google.com/v1/r/project/discord-bot-project-469211/firestore/indexes?create_composite=Cmhwcm9qZWN0cy9kaXNjb3JkLWJvdC1wcm9qZWN0LTQ2OTIxMS9kYXRhYmFzZXMvKGRlZmF1bHQpL2NvbGxlY3Rpb25Hcm91cHMvY29udmVyc2F0aW9uX2FyY2hpdmVzL2luZGV4ZXMvXxABGgsKB3VzZXJfaWQQARoPCgthcmNoaXZlZF9hdBACGgwKCF9fbmFtZV9fEAI
```

## 暫定対応

現在、インデックスエラーを回避するため以下の暫定対応を実装済み：

1. **クライアント側フィルタリング**: 複合クエリをシンプルなクエリに変更し、クライアント側でフィルタ・ソートを実行
2. **制限数調整**: フィルタ後の制限を考慮してより多くのドキュメントを取得
3. **エラーハンドリング強化**: インデックスエラー時も動作を継続

## 注意事項

- インデックス作成には数分〜数時間かかる場合があります
- 作成完了までは暫定対応で動作します

## 確認方法

インデックス作成完了後、以下のコマンドでエラーが解消されることを確認：

```bash
python3 main.py
```

ログに「インデックスエラー」が表示されなくなれば完了です。