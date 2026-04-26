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

## ワンショット実行

```bash
uv run main.py --command "battery?"
```

## 映像ウィンドウを出さない場合

```bash
uv run main.py --no-video
```

## 単体テスト（実機不要）

```bash
python -m unittest discover -s tests -v
```
