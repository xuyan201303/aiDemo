# 統一費目 Demo（Mock SDK＋実OpenAI）
案件 No.202608310011。主表は統一費目＋必要な区別条件を行、フォワーダーを列とする。
価格・通貨は今回資料の保存値。通常の確認画面では読み取り専用で、名称確認リクエストに金額を含めない。
既存の金額修正APIは回帰互換のため保持する。

## 起動
プロジェクトディレクトリで実行：
```sh
bash -c 'set -a; source sanzo_cost_table_framework_v1_1_0/.env; set +a; exec .venv/bin/python tools/start_demo.py --reader-mode MOCK_SDK --ai-authorization demo_materials/config/ai-authorization.json'
```
URL: http://127.0.0.1:8765
Keyは本機バックエンドだけで扱う。モデル・固定authorization・累計上限を変更しない。
既存DB・アップロードは保持される。SDK本体は使用しない。

## 操作
案件を作成し、A社Excel＋B社PDFをまとめて選択して「アップロード・解析開始」。
単価セルから統一費目名を確認し、既存費目を選択するか新名称を入力して保存する。
原文・条件・出典はDialog。未確認条件を補造しない。
第三のExcelは別案件へ追加する。名称だけ確認記録から再利用し、価格は新しいファイルから取得する。

## 表示と記録
raw_label、AI提案、人工確認fee_id/名称、条件、今回価格、通貨を別々に保持する。
comparison.pyの同一projectionをHTTPとExcelが使用する。原始10明細は削除・統合せず5行に表示できる。
候補が曖昧/既知条件に矛盾する場合は確認必要。未知条件は学習のwildcardではない。
主表の簡潔な確認状態と、学習可能性/出典適格性は別。詳細Dialogと監査sheetに未確認事項を保持する。
過去の人工確認名に条件が既に含まれる場合、その名称を自動変更せず重複表示だけ避ける。
Excel主sheetは統一費目と価格・通貨。詳細状態と由来は「明細と出典」「未確認事項」に保持する。

## 検証入口
```sh
.venv/bin/python tools/rehearse_unified_fee.py --execute-approved-ai --evidence /private/tmp/SANZO_UNIFIED_FEE_REHEARSAL
```
明示実行すると承認済み実AI枠を消費する。自動再試行・モデル切替なし。
スクリプトの8,000/9,500等は結果の受入アサーションであり、入力・AI回答・価格生成には使用しない。
既存記憶で配送費が既に正しい場合、誤答を作らず実際の人手確認を保存する。
新しい実行は新しい証拠ディレクトリを使う。失敗証拠は削除しない。

実SDKはNOT_RUN、W0はINCOMPLETE。Mock＋実AIの実行成功を実SDK接続成功と表現しない。
