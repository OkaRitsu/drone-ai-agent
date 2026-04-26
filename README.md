# drone-ai-agent

## TELLOをターミナルから操作する
1. PCをTELLOのWi-Fiに接続
2. `opencv-python` をインストールしておく（cv2）
3. 対話モード起動（起動時に映像ストリーミングウィンドウを表示）

```bash
uv run main.py
```

4. 例: バッテリー確認

```text
tello> battery?
```

5. 例: 離陸・前進・着陸

```text
tello> takeoff
tello> forward 30
tello> land
```

## 飛行ログ保存

`takeoff` から `land`（または `emergency`）までを1セッションとして保存します。

- 保存先: `logs/YYYYMMDD_hhmmss/`
- 保存内容:
  - `commands.csv`（実行コマンドと応答）
  - `state.csv`（ドローン状態スナップショット）
  - `video.mp4`（飛行中の動画）
  - `metadata.json`

保存件数は `--max-log-sessions` で制御でき、上限を超えた古いログは自動削除されます。

## ワンショット実行

```bash
uv run main.py --command "battery?"
```

## 映像ウィンドウを出さない場合

```bash
uv run main.py --no-video
```

## ログ保存の設定例

```bash
uv run main.py --log-dir logs --max-log-sessions 30 --state-sample-interval 0.5
```

## 単体テスト（実機不要）

```bash
python -m unittest discover -s tests -v
```
