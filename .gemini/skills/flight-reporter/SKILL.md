---
name: flight-reporter
description: Use this skill to summerise flight report after drone landing.
---

# Flight Reporter
このスキルは、ドローン飛行後の結果をレポートにまとめるスキルです。

## Workflow
1. ドローンの飛行ログを確認します
   -  動作中の会話の履歴や飛行ログを取得するツールを活用します。
   -  どんな事があったのか時系列でまとめてください。
2. 撮影した写真を解析します。
   - 撮影した画像をツールを使って読み込んでください。
   - 写っているものや特徴的な事柄を解析します。
   - 撮影した画像を順番に解析していきます。
3. レポートとしてまとめます。
   - `assets/report_template.md` を読み込み、まとめた結果を埋めてください。
   - レポートには画像を適切に配置し、説明を加えてください。
   - 出力した結果を `log/YYYYMMDDhhmmss/report.md` としてファイルに出力してください。
