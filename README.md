# drone-ai-agent

ドローンをAIエージェントから操作し、映像・状態をモニタリングするための実験用アプリケーションです。TELLO SDKのコマンド実行、Rerunダッシュボード、MCPサーバを提供します。

## 主要機能

- TELLOの基本操作: 離陸、着陸、前後左右移動、上下移動、回転、速度確認など
- Rerunダッシュボード: カメラ映像とテレメトリの表示
- 飛行ログ保存: コマンド履歴、状態CSV、動画、メタデータ
- MCPサーバ: AIエージェント向けのドローン操作・状態取得ツール
- CLI操作: AIエージェントが使えない場合の手動フォールバック

## セットアップ

依存関係は `uv` で管理します。

```bash
uv sync
```

TELLOを操作する場合は、実行前にPCをTELLOのWi-Fiへ接続してください。

### CLI / ダッシュボード

デフォルトではRerunダッシュボードを起動し、対話CLIを開始します。

```bash
uv run main.py
```

`main.py` でMCPサーバも同時起動する場合（AIアプリは起動済みMCPへ接続）:

```bash
uv run main.py --mcp-http
```

上記で `http://127.0.0.1:8000/mcp` にMCPエンドポイントが立ち上がります。

ダッシュボードを起動せずCLIだけ使う場合:

```bash
uv run main.py --no-dashboard
```

単発コマンドだけを実行する場合:

```bash
uv run main.py --command "battery?"
```

### MCPサーバ

MCPサーバは `stdio` transportで起動します。

```bash
uv run python -m src.mcp.server
```

このコマンドはMCPクライアントからstdioで起動される想定です。Gemini CLIで使う場合は、別ターミナルで先に起動し続けるのではなく、下記のGemini CLI設定から起動させてください。

すでに `uv run main.py --mcp-http` で起動済みのMCPへ接続する場合は、stdioではなくHTTP接続を利用してください。

提供ツール:

- `get_state`: 最新のドローン状態を取得
- `get_frame`: 最新カメラフレームをFastMCP `Image` として取得
- `send_command`: TELLO SDKコマンドを実行
- `get_flight_log`: 飛行ログを取得

### Gemini CLIでMCPサーバを使う

このリポジトリにはプロジェクト設定として [.gemini/settings.json](./.gemini/settings.json) を追加しています。

設定内容:

```json
{
  "mcpServers": {
    "drone_ai_agent": {
      "command": "uv",
      "args": ["run", "python", "-m", "src.mcp.server"],
      "cwd": ".",
      "timeout": 30000,
      "trust": false
    }
  }
}
```

使い方:

```bash
gemini
```

Gemini CLIをこのリポジトリのルートで起動すると、`drone_ai_agent` MCPサーバが登録されます。初回利用時はツール実行確認が出ます。TELLO実機を操作する場合は、Gemini CLI起動前にPCをTELLOのWi-Fiへ接続してください。

登録状況を確認する場合:

```bash
gemini mcp list
```

プロジェクト設定を使わず手動で登録する場合:

```bash
gemini mcp add drone_ai_agent uv run python -m src.mcp.server
```

`main.py --mcp-http` の起動済みサーバへ接続する場合:

```bash
gemini mcp add drone_ai_agent http://127.0.0.1:8000/mcp --transport http
```

## CLIコマンド例

```text
tello> battery?
tello> takeoff
tello> forward 30
tello> cw 90
tello> land
```

利用できる主なコマンド:

- `takeoff`, `land`, `emergency`
- `forward/back/left/right/up/down <20-500>`
- `cw/ccw <1-360>`
- `speed <10-100>`, `speed?`, `battery?`, `time?`
- `flip <l|r|f|b>`
- `raw <sdk command>`
- `help`, `quit`

## オプション

```bash
uv run main.py --help
```

主な設定:

- `--host`: TELLOのIPアドレス。デフォルトは `192.168.10.1`
- `--port`: TELLO SDKコマンドポート。デフォルトは `8889`
- `--local-port`: ローカルUDP bindポート。デフォルトは `9000`
- `--state-port`: 状態受信用UDPポート。デフォルトは `8890`
- `--video-port`: 映像受信用UDPポート。デフォルトは `11111`
- `--log-dir`: 飛行ログ保存先。デフォルトは `logs`
- `--max-log-sessions`: 保持する飛行ログセッション数。デフォルトは `20`
- `--state-sample-interval`: 飛行ログ用の状態サンプリング間隔。デフォルトは `0.5`
- `--dashboard-state-interval`: ダッシュボード状態プロット間隔。デフォルトは `0.2`
- `--keepalive-interval`: 飛行中に自動送信するkeepalive間隔（秒）。デフォルトは `8.0`
- `--keepalive-command`: keepaliveで送るSDKコマンド。`battery?` または `time?`（デフォルト: `battery?`）

## 飛行ログ

`takeoff` から `land` または `emergency` までを1セッションとして保存します。

保存先:

```text
logs/YYYYMMDD_hhmmss/
```

保存内容:

- `commands.csv`: 実行コマンドと応答
- `state.csv`: ドローン状態スナップショット
- `video.mp4`: 飛行中の動画
- `metadata.json`: セッション情報

## テスト

実機不要の単体テスト:

```bash
python -m unittest discover -s tests -v
```

フォーマット確認:

```bash
python -m black --check main.py src/mcp/server.py tests/test_tello_cli.py tests/test_mcp_server.py
```

## 関連ドキュメント

- [要件README](./docs/requirements/README.md)
- [機能一覧](./docs/requirements/feature-list.md)
- [MVP範囲](./docs/requirements/mvp-scope.md)
