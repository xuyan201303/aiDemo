# 統一費目の確認パネル（2026-09-12）

今回のUIは、比較表の左端から開く「統一費目を確認」を、原文カード・本次価格・名称入力一欄・保存ボタンに絞ります。比較行の全明細を既存の行確認APIへ送信します。価格・通貨の編集欄はありません。

## 操作

- 各社カードの「原文を見る →」で、そのアップロード原件の取得済み位置を開きます。閉じると入力途中の名称へ戻ります。ページしか分からないPDFは範囲未確定と表示します。
- 名称欄は既存候補の選択と新規入力に共用します。既存の英文確認名を翻訳しません。同名の複数候補はID付きの候補を明示的に選びます。APIと同じNFC・空白の正規化を使います。
- 1024文字まで。長すぎる入力は切り捨てずエラーを表示します。日本語IMEの変換確定Enterでは保存しません。
- 通常は原文・価格・短い出典・原文プレビュー・名称入力・保存だけを表示します。条件・出典不足、AI提案、全条件、各明細の学習適格性、記録版、再確認・撤回は、最下部の「詳細を見る」にまとめ、既定では閉じます。
- 保存中は二重操作を止めます。応答が失われたら結果未確認と表示し、同じ要求・版・idempotency keyで再確認します。結果が確定するまでは別名称の保存や撤回を止めます。
- 409では名称を保持し、短いエラーを表示します。「詳細を見る」内の「最新を読み直す」で原文と版を確認して再保存します。開いた後に増えた明細を黙って対象に追加しません。
- 名称確認は全メンバーへ反映します。次回の自動適用memoryは既存の学習条件を満たす明細だけです。部分的な人工確認だけでは行全体に確認済みと表示しません。

## 維持した契約

`POST /api/cases/{case_id}/comparison/{row_id}/confirmation` と既存 `RowConfirmationService` を再利用します。DB・移行・F1・価格ロジック・memory key・履歴・Mock/Real境界・AI予算は変更しません。横並びのグルーピングは維持し、projectionの顧客向け行状態だけを全メンバー確認に合わせました。Excel主sheetの価格セルは既存の共有projectionの金額・通貨のみです。

## 起動

プロジェクトのバックエンド用環境変数を設定済みのターミナルで、次の一命令を実行します。承認JSONは非秘密情報です。実行済みの承認ID・100回累計予算をそのまま利用します。

```sh
/Users/kyoen/Projects/sanzo-cost-table/.venv/bin/python /Users/kyoen/Projects/sanzo-cost-table/tools/start_demo.py start --reader-mode MOCK_SDK --ai-authorization /Users/kyoen/Projects/sanzo-cost-table/demo_materials/config/ai-authorization.json
```

URL: http://127.0.0.1:8765

この起動は本プロジェクトのWebコンテナを新ソースで再作成し、既存DB・アップロードvolumeを保ちます。本輪はユーザーの追加承認を受け、指定済みの本プロジェクト.envからKeyを後端環境へ渡して起動しました。Keyの値・長さ・接頭辞・ハッシュを出力せず、通常の8765を更新しています。AI呼び出しは行っていません。

## 検証の再実行

既存実演DBに保存しない検証入口:

```sh
PYTHONPATH=backend:sanzo_cost_table_framework_v1_1_0/reference_core .venv/bin/python -B tests/browser_fee_panel.py --evidence /private/tmp/sanzo-panel-offline-new
.venv/bin/python -B tools/validate_compact_panel.py --evidence /private/tmp/sanzo-panel-postgres-new
```

Chromeと既存Dockerを使用します。第2コマンドは独立の使い捨てPostgreSQLとテストWebを起動し、127.0.0.1:18765で実ブラウザ検証後にそのテスト資源だけを終了します。元の実演DBへは接続しません。合成データ・故障注入専用の `tests/panel_browser_server.py` は通常起動で使わず、業務APIには追加しません。

保存済みDemoの読取専用チェックは `tools/verify_compact_saved.py` です。既定ではローカルの静的ファイルとprojectionをブラウザで読み込み、実サーバーの保存済み明細・原件・ExcelをGETのみで照合します。`--live-ui`では静的ファイルのハッシュ一致を確認し、差し替えを一切せず実サーバーのUIを検証します。新UIの読み込み差し替えを証拠に明示し、常用サーバーの更新や新しいAI処理とは扱いません。

新しいAI呼び出しは0回。SDKは参照も実行もしていません。元の三ファイル・fixture・実演案件・memory・承認台帳を保持します。真のSDK接続は依然NOT_RUN、W0はINCOMPLETEで、プロジェクト全体のPARTIALは変更しません。旧段階の失敗・PARTIAL証拠はそのまま残します。
