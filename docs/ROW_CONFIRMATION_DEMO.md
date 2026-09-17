# 行単位の統一費目確認

主表は左端に統一費目名・区別条件・統一状態・確認ボタンを集約する。各社セルは今回の価格と通貨だけで、クリック編集や価格入力はない。

左端の「確認・修正」で同じ行の全社の原文、今回単価、AI提案、出典と原文プレビューを表示する。既存費目を選択するか、新しい統一費目名を入力して保存する。区別条件は表示専用。

行名が人工確認済みなら「確認済み」。各社の学習可能性とは別で、詳細内に条件・出典不足や記憶版・撤回状態を示す。過去の人工確認名を持つA社と未確認提案のB社が同じ行にある場合も、行の名称は確認済みとして表示できる。B社の原文対応まで確認された意味にはしない。行保存を実行したとき、行に含まれる各明細を明示的に確認する。

## 保存境界

POST /api/cases/{case_id}/comparison/{row_id}/confirmation は、サーバー側projectionと全member集合、案件版、全item版と記憶版を検証する。価格・通貨・原文・適格フラグは入力として受け付けない。

既存ReviewServiceの単一明細保存を同じPostgreSQLトランザクション内で呼び、全件成功または全件ロールバックとする。新しいDB、テーブル、migrationは追加していない。既存CORRECT台帳に型付き行commandと全件responseを保存し、各明細の既存イベント/記憶も個別に保存する。冪等keyは全payloadに結びつき、同じkeyの別内容は409。再読込は明示操作で、競合時に自動的に新しい版へ上書きしない。

原文表記と会社・条件・source eligibilityから既存memory_keyを算出する。適格明細だけ記憶を作り、条件/出典不足の明細は今回の名称を確認しても記憶を作らない。競合/撤回は既存の遷移規則と明示再確認を保持。価格、通貨、source、AI提案は書き換えない。金額修正の既存APIは回帰互換のため残す。

## Projection と Excel

ComparisonRow: row_id, display_name, fee_identity, differentiating_conditions, confirmation_status, member_items, cells_by_forwarder。
各社cellはprice/currency/item_id/source_ref。従来のcells等は監査ツール互換のため残すが、普通Web/Excelは価格専用のcells_by_forwarderを使う。
Excel主sheetは費目×各社単価。行状態、各社raw label、AI提案、確認名、記憶版とsourceは別sheet「確認情報」。既存の「明細と出典」「未確認事項」と草稿表示を保持する。

## 起動

プロジェクト /Users/kyoen/Projects/sanzo-cost-table で実行：
```sh
bash -c 'set -a; source sanzo_cost_table_framework_v1_1_0/.env; set +a; exec .venv/bin/python tools/start_demo.py --reader-mode MOCK_SDK --ai-authorization demo_materials/config/ai-authorization.json'
```
URL: http://127.0.0.1:8765
資格情報はバックエンドだけで扱う。SDK未接続/W0 INCOMPLETEを変更しない。既存DBと固定累計枠を保つ。

## 本輪ブラウザ検証

実処理済み案件を使う検証入口（AIは呼ばない）：
```sh
.venv/bin/python tools/verify_row_confirmation.py --first-case <案件ID> --second-case <次回案件ID> --evidence /private/tmp/SANZO_ROW_CHECK
```
省略時は表示・原文・ダウンロードをGET-onlyで検証。--saveを明示すると指定案件の20FT行と配送費行を画面から実際に保存する。再実行は新しい証拠ディレクトリを使う。本輪は既存の実OpenAI結果を検証し、新しいAI抽出や三ファイル再アップロードを実行していない。生成Excelは検証先に保存するが、Review Bundleには主表の照合結果/ハッシュとスクリーンショットを収録し、xlsx原件は収録しない。
