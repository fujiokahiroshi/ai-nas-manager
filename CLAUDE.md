# このリポジトリについて

Windows11 + WSL(Ubuntu)環境で、複数のMCPサーバーをClaude(Desktop/Code)が
橋渡しする形の実験・開発を行っている。最終目標は Rockchip RK3588 NAS上で動く
自然言語対応の「AI NAS manager」（`ai-nas-manager_v-01_instructions.md`参照、
将来 `ai-nas-manager/docs/v-01-instructions.md` に保存予定）。

設計原則（今回の検証で確立):
**各MCPサーバーは互いの存在を知らなくてよい。Claudeが自然言語の意図を理解して
複数のMCPサーバーをまたいでデータを橋渡しする。** そのため各コンポーネントは
自分のプラットフォームに一番自然な形（WSL側はLinuxネイティブ、Windows側は
Windowsネイティブ）で作ればよく、コンポーネント間の直接通信（独自プロトコルや
ネットワークブリッジ）は基本的に不要。

## 実行環境の前提

- 作業ディレクトリはWSL側 (`/home/hiroshi/test`)。Windows側からは
  UNCパス `\\wsl.localhost\Ubuntu\home\hiroshi\test` でアクセスする。
- Node.js (Windows) と Python (Windows + WSL両方) を今回のセッションでインストール済み。
- **UNCパス上でのビルド/venv作成は極端に遅い**。Windows側で完結するツール
  (epg-rendererのvenvなど)は必ずローカルディスク(`C:\...`)に置く。
  ソースコード自体はUNCパス上のままでよい(1回読むだけの起動は問題ない)。
- `wsl.exe -e <cmd>` でWindows側からWSLのプログラムをstdio起動するのは
  信頼できるパターン(今回何度も使った)。逆にWSL側からWindowsの127.0.0.1へは
  直接届かないため、WSL→Windows方向の通信が必要な場合はWindows側のデフォルト
  ゲートウェイIP(`ip route`の`default via`)を使う。Windows→WSLへのlocalhost
  アクセスは自動転送されるので素直に動く。

## コンポーネント

### 1. windows-message-mcp (`/`, TypeScript/Node.js)
Windows/WSL間でメッセージを相互送受信するMCPサーバー(stdio + ローカルHTTP)。
`get_messages`/`send_to_linux`/`peek_messages`ツール。
ビルド: `node node_modules/typescript/bin/tsc -p tsconfig.json`
(UNCパス上で`npm run build`をそのまま実行するとcmd.exeがUNC非対応で失敗するため、
tscを直接呼ぶ必要がある)

複数のMCPクライアント(Claude Desktop/Code)が同時にこのサーバーを起動すると
HTTPポート(39217)の奪い合いが起きるため、ポートを取れなかったプロセスは
「フォロワー」としてリーダーへHTTP転送する仕組みを実装済み(`src/index.ts`)。

現状の位置づけ: ai-nas-managerの本流とは無関係な実験用コンポーネント。
削除はしていないが、今後の開発には基本的に絡まない想定。

### 2. ai-nas-manager (`ai-nas-manager/`, Python, WSLネイティブ)
v-01の本体。venvは `ai-nas-manager/.venv` (WSL内、UNCパス上だが小規模なので問題なし)。

- `server.py` — MCPツール: `ping`, `list_media`(ダミー), `discover_tuners`,
  `get_tuner_status`, `get_tuner_channels`, `get_tuner_program_guide`
- `discovery.py` — mDNSでTunerを発見 (`_tuner._tcp.local.`)
- `tuner_client.py` — 発見したTunerのMCPサーバーへstreamable-httpで接続する汎用クライアント
- `cli.py` — `python cli.py tuner list` / `tuner status <name>`

テスト: `cd ai-nas-manager && .venv/bin/python -m pytest -v` (9件、実プロセス間通信で検証)

### 3. virtual_tuner (`ai-nas-manager/virtual_tuner/`, Python, WSLネイティブ)
実機Tuner(Amlogic搭載)が用意できるまでの代役。ai-nas-managerとは独立プロセス。

起動: `cd ai-nas-manager && .venv/bin/python -m virtual_tuner --port 8765`
(mDNSアドバタイズ + streamable-httpでポート8765を公開。**起動しっぱなしにしないと
discover_tuners/get_tuner_status等が失敗する**)

- `get_status` — 電源/チャンネル/録画状態のダミー
- `list_channels` / `get_program_guide` — サンプル番組表(`epg.py`)。
  NHK総合・NHK Eテレ・日本テレビ・テレビ朝日・TBS・テレビ東京・フジテレビの
  東京7局构成、30番組。`channel`/`genre`/`keyword`/`at`(ISO8601時刻)で絞り込み可能。

### 4. epg-renderer (`epg-renderer/`, Python, **Windowsネイティブ**)
番組表を実際に画面表示する専用MCPサーバー。WSLに一切依存しない。
venvは `C:\Users\yukik\epg-renderer-venv\.venv` (UNC上に置くとpip installが
極端に遅かったためローカルパス)。ソース(`server.py`)自体はリポジトリ内。

- `render_epg(programs)` — `get_tuner_program_guide`の戻り値をそのまま渡すと、
  チャンネル×時間の格子HTMLを生成し既定ブラウザで開く。現在時刻の赤線表示、
  番組クリックでの詳細パネル表示に対応。

## MCP登録状況

Claude Code (`~/.claude.json` の user scope) とClaude Desktop
(`%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`)
の**両方**に、以下4サーバーを個別登録済み(登録先が違うので新サーバー追加時は両方への追加が必要):

- `windows-message-mcp` — `node.exe` + `dist/index.js`
- `ai-nas-manager` — `wsl.exe -e .../ai-nas-manager/.venv/bin/python .../server.py`
- `epg-renderer` — `C:\Users\yukik\epg-renderer-venv\.venv\Scripts\python.exe` + `epg-renderer/server.py`

いずれも新しいセッション/ウィンドウで自動起動される。**このセッション内で
ツールを追加した場合、実行中のセッションはツール一覧をキャッシュしているので
再起動しないと反映されない。**

`claude mcp add`はGit Bash(MSYS)がUNCパスの`\\`を`\`に潰してしまうため、
そのままでは動かない。`MSYS_NO_PATHCONV=1`を付けるか、`~/.claude.json`を
直接編集する(JSON文字列内は`\\\\`で1つの`\\`を表す)。

## 既知の注意点・詰まりどころ

- Windows側は`python`コマンドがMicrosoft Storeスタブを指すことがある。
  `winget install Python.Python.3.12`で実体をインストール済み。
- 新しくインストールしたツールのPATHは、既存のシェル/セッションには反映されない。
  絶対パス指定(`C:\Program Files\nodejs\node.exe`等)で回避するのが確実。
- WSL側で`sudo`が必要な場合あり(ユーザー: hiroshi)。
- Gitはこのリポジトリ用に `safe.directory`(global)と`user.name`/`user.email`
  (local)を設定済み。

## v-01 進捗 (指示書 第9章 受け入れ基準)

- [x] 仮想Tuner起動中に `discover_tuners`/`tuner list` で1件発見
- [x] `get_tuner_status`/`tuner status <name>` で状態取得
- [x] 仮想Tuner停止中は0件・クラッシュなし
- [x] 存在しないTuner名でエラーメッセージ
- [x] 単体・結合テスト全パス(9件)
- [x] Claude Code/Desktopへの登録・疎通確認
- [x] (指示書スコープ外だが追加実装) 番組表機能、Windows側レンダラー連携

未実施:
- [ ] `ai-nas-manager/docs/v-01-instructions.md` として元指示書を保存
  (この会話には全文があるので、次回保存できる)
- [ ] 実機Tunerとの差し替え、録画予約などの実操作コマンド、NAS連携、Web UI (v-02以降)

## 次にできそうなこと

- 録画予約など「書き込み系」操作をTunerに追加し、同じ中継パターンで実装
- 複数Tuner対応(discover_tunersが複数返す場合のUX)
- epg-rendererをtkinter/PyQt等でネイティブGUI化(検討したが今回はブラウザ版を選択)
- v-01指示書自体をリポジトリに保存
