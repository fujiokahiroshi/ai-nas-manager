# ai-nas-manager v-01（試案）実装指示書

対象: Claude Code（WSL上の `ai-nas-manager` リポジトリで作業するエージェント）
位置づけ: v-01 = 最初の試案（もっとも小さい、たたき台コアのみを対象とし、以降のバージョンで段階的に機能を追加していく前提）

---

## 0. 作業開始前の確認事項

本書は現時点の設計メモをもとにしたたたき台であり、実際のリポジトリの中身を正とする。作業開始時に必ず以下を行うこと。

- 既存の `ai-nas-manager` の骨組み（ディレクトリ構成、`server.py`/`cli.py` 等の実装、登録済みMCPツール一覧、依存パッケージ）を確認する
- 本書の内容と既存実装に矛盾がある場合は既存実装を優先し、差分を報告する
- 不明点・判断が分かれる点は、実装を進める前に確認を求める（第11章「未確定事項」も参照）

---

## 1. 背景（参考情報）

- `ai-nas-manager` は、xit4構想（放送・録画・NAS・IoTホーム・電源制御・AIエージェント層を統合する次世代ホームAIノード5）における、AIエージェントが Rockchip RK3588上で動作するコンポーネント
- Python製のMCP / Web / CLI の3系統のインターフェースに対応する計画
- 役割: Tuner（Amlogic搭載フランスコーダーを持つ録画機）をAI経由で制御し、Linuxベースの標準NASマネージャーと連携して、Tunerから取得したデータを抽出・解釈・加工する
- Tunerは自律ノードであり、単独動作が可能（USB接続モード / スタンドアロンモードの2つのMCPサーバーを持ち、物理スイッチ（GPIO）で番組と電源投入時のみ状態を読み取り、ネットワーク上の発見は mDNS/Bonjour を採用する方針
- 設計思想: 機器（エージェント）が互いの機能を知らせ、要求に応じて自動的に連携する（FireWireバス構想をネットワーク構想でAIエージェント/MCPで実現する）。可視化（visualization）は基本的にユーザー側が作るという方針だが、標準GUIは製品側でも用意する。CLI-firstを徹底する（製品自体のCLI対応と開発プロセスのCLI駆動の両方）

## 2. 前提・現状（本セッションまでの到達点）

- `windows-message-mcp`: Windows/WSL間でメッセージを相互送受信するMCPサーバーを作成・デバッグ・動作確認済み
- `ai-nas-manager`: Python製MCPサーバーの骨組みをWSL上に構築済み、Claude Codeへの登録・接続確認まで完了
- 未着手: Tuner（実機・仮想いずれも）との疎通、NASマネージャー連携、データ抽出・解釈処理、Web UI

## 3. v-01の目的（たたき台コア）

v-01のゴールは次の1点に絞る。

> ai-nas-managerのMCPサーバーが、ネットワーク上のTunerノードを発見し、MCP経由で最小限のコマンド（状態取得程度）を相互通信できることを確認する。

答えは以下の2点。

- MCPサーバー間連携（エージェント⇔エージェント）の基本パターンをこのプロジェクトで確立する
- 以降のバージョンで機能を積み上げていくための土台を作る

実機Tunerはまだ用意しない（詳細仕様未確定）ため、v-01ではTuner役として**仮想Tuner**を設計・実装し、これを相手に疎通確認を行う（詳細は第4章）。

### 3.1 v-01のスコープに含めるもの

1. 仮想Tunerの設計・実装（実機Tunerの代替として使用、詳細は第4章）
2. 既存MCPサーバー骨組みの構成整理・確認
3. Tunerノード（仮想Tuner）のネットワーク発見（mDNS/Bonjour）
4. 発見されたTunerに対するMCP経由の疎通確認（状態取得相当のコマンド）
5. CLIから上記3・4を手動実行できるサブコマンド
6. 最低限のログ出力とエラーハンドリング（Tuner不在・オフライン時に落ちない）
7. 仮想Tunerを用いた動作テスト・結合テスト

### 3.2 v-01のスコープに含まれないもの（v-02以降）

- 実機Tunerとの疎通・実機仕様への適合
- Tunerに対する実操作コマンド（録画開始/停止、チャンネル設定など）
- Linux標準NASマネージャーとの連携
- Tunerから取得したデータの抽出・解釈・加工処理
- Web UI
- 認証・セキュリティ機構
- 複数Tunerの同時管理

## 4. 仮想Tunerの設計方針

v-01時点では実機Tuner（Amlogic搭載）を用意せず、実機と同等のインターフェースで動作する「仮想Tuner」を設計・実装する。ai-nas-managerとの疎通確認の相手として使用する。

### 4.1 目的

- 実機Tunerの詳細仕様（MCPツール仕様、通信プロトコル等）が未確定のため、実機を待たずにai-nas-manager側の開発・検証を進められるようにする
- 実機Tunerが用意でき次第、仮想Tunerと差し替えるだけで動作するよう、インターフェースを実機仕様（想定）に沿わせて設計する
- 動作テストだけでなく、実際にネットワーク上で、mDNSアドバタイズ→discovery→MCP接続という一連の流れを、独立したプロセス間の実通信で確認できるようにする（インプロセスのモックオブジェクトで済まさない）

### 4.2 要件

- 仮想Tunerは`ai-nas-manager`本体とは別の、独立したPythonプロセスとして起動できること（将来的に実機に置き換える部分であることを明確にするため）
- 起動時にmDNS/Bonjourで自身をアドバタイズすること（サービスタイプ名は第11章「未確定事項」で確定させるが、暫定で `_tuner._tcp.local.` とする）
- 実機Tunerのスタンドアロンモード相当のMCPサーバーとして動作し、最低限 `get_status` ツールを実装すること
  - `get_status` の応答は、実機の想定仕様（電源状態、チャンネル、録画状態など）を模したダミー値でよいが、フィールド構成は第8章のMCPツール定義に沿わせる
- CLIまたはスクリプトから起動・停止できること（例: `python -m virtual_tuner` または `ai-nas-manager dev virtual-tuner start`）
- 実機Tunerには USB接続モード・スタンドアロンモードの2モード切替の設計方針があるが、v-01の仮想Tunerはネットワーク経由（スタンドアロンモード相当）のみを実装すればよい。USBモードのシミュレーションはv-01の対象外とする

### 4.3 配置・実装方針

- 実機Tunerの実装（別リポジトリ想定）と混同しないよう、`ai-nas-manager`リポジトリ内に独立ディレクトリ（例: `virtual_tuner/`）として配置する（第6章のディレクトリ構成を参照）
- 仮想Tunerの実装は、`ai-nas-manager`本体のコードに依存しない独立したモジュールとして書くこと（将来的に単独で切り出す可能性を考慮）

## 5. アーキテクチャ方針

- 言語: Python 3.x（既存骨組みのバージョンに準拠）
- MCPサーバー実装: 既存骨組みで使用しているMCP SDK/フレームワークをそのまま踏襲し、新方式を持ち込まない（仮想Tunerも同じSDKを使う）
- CLI: 既存のCLIエントリポイントに、本書で定義するサブコマンドを追加する形で実装する
- ネットワーク発見: mDNS/Bonjour（Python実装は `zeroconf` 等、既存の依存関係・ライセンス方針と整合するものを選定し、選定理由を記録する）
- Tuner側との通信: Tuner（仮想・実機とも）は自律MCPノードであるため、ai-nas-manager側はMCPクライアントとしてTunerのMCPサーバーに接続する構成とする
- 設定: Tunerのサービス名/ポート等は環境変数または設定ファイルで指定可能にし、mDNSで自動発見できない場合のフォールバック手段を用意する

## 6. ディレクトリ構成（想定・要確認）

既存の実際の構成に読み替えることが必要な場合は差し替えること。既存の実際の構成に読み替えることが必要な場合の一例として以下を提案する。

```
ai-nas-manager/
  src/ai_nas_manager/
    __init__.py
    server.py            # 既存: MCPサーバー本体
    cli.py                # 既存 or 新規: CLIエントリポイント
    discovery.py           # 新規: mDNS/Bonjourによるtuner発見
    tuner_client.py         # 新規: Tuner MCPサーバーへのクライアント
    config.py              # 新規 or 既存: 設定読み込み
  virtual_tuner/            # 新規: 仮想Tuner（実機Tunerの代替、独立プロセス）
    __init__.py
    server.py                # 仮想TunerのMCPサーバー本体（get_status等）
    mdns_advertise.py         # mDNSアドバタイズ処理
  tests/
    test_discovery.py
    test_tuner_client.py
    test_virtual_tuner.py
  docs/
    v-01-instructions.md      # 本書
```

## 7. 実装タスク（順序）

1. 既存リポジトリの現状把握（`server.py`/`cli.py`の構成、登録済みMCPツール一覧、`requirements.txt`/`pyproject.toml`）を確認し、本書とのギャップを報告する
2. `virtual_tuner/`: 仮想Tunerを実装する。mDNSアドバタイズ機能と、`get_status`を提供する最小限のMCPサーバーを、独立プロセスとして起動できる形で用意する（第4章参照）
3. `discovery.py`: mDNS/BonjourでTunerサービス（仮想Tunerを含む）を発見する関数を実装する（例: `discover_tuners(timeout_sec: float) -> list[TunerInfo]`、`TunerInfo`はホスト名/IP/ポート/サービス名を含む）
4. `tuner_client.py`: 発見されたTunerのMCPサーバーに接続し、状態取得コマンド（`get_status`）を呼び出すクライアントを実装する
5. `server.py`: ai-nas-manager自身のMCPツールとして `discover_tuners` と `get_tuner_status` を新規登録する
6. `cli.py`: `ai-nas-manager tuner list`（発見結果一覧表示）、`ai-nas-manager tuner status <name>`（状態取得）のサブコマンドを追加する
7. ログ: 発見・接続・エラーを既存のログ方式に合わせて出力する
8. テスト: 仮想Tunerを実際に起動した状態での結合テスト、および必要に応じて単体テストを用意する
9. 第9章の受け入れ基準に従い、仮想Tunerを起動した状態で一連の動作確認する

## 8. MCPツール定義（v-01追加分）

| ツール名 | 提供元 | 入力 | 出力 | 説明 |
|---|---|---|---|---|
| `get_status` | 仮想Tuner（将来は実機Tuner） | なし | 状態dict（例: `power`, `channel`, `recording` などのキー値） | Tunerが自身の状態を返す |
| `discover_tuners` | ai-nas-manager | `timeout_sec`（省略可、既定3.0） | Tuner一覧（`name`, `host`, `port`） | mDNSでネットワーク上のTuner（仮想Tunerを含む）を発見する |
| `get_tuner_status` | ai-nas-manager | `tuner_name` または `host`+`port` | `status`（dict、内容はTuner側`get_status`の応答をそのまま中継） | 指定したTunerの状態を取得する |

`get_status`の応答フィールドは、実機Tunerの仕様が確定していないため、v-01時点では仮の項目でよいが、後で実機に差し替える際に大きな変更を要しないよう、素直なdict構造に留める。

## 9. 受け入れ基準（動作確認手順）

- [ ] 仮想Tunerを起動した状態で `ai-nas-manager tuner list` を実行し、仮想Tunerが1件表示される
- [ ] `ai-nas-manager tuner status <name>` を実行し、仮想Tunerからの応答（状態情報）が表示される
- [ ] 仮想Tunerを停止した状態で `ai-nas-manager tuner list` を実行し、クラッシュせず0件またはタイムアウトとして扱われる
- [ ] Tunerが存在しない（オフライン）の場合にクラッシュせず適切なエラーメッセージが表示される
- [ ] 単体テスト・結合テストがすべてパスする
- [ ] Claude Codeへの登録済みMCPツール一覧に `discover_tuners`・`get_tuner_status` が追加されていることを確認する

## 10. 今後の拡張（v-02以降・参考情報）

- 仮想Tunerから実機Tunerへの切り替え・実機での疎通確認
- Tunerへの実操作コマンド（録画開始/停止、チャンネル設定など）、まず仮想Tuner側でシミュレーションを追加し、その後実機に反映する
- Linux標準NASマネージャーとの連携（ファイル一覧取得・転送など）
- Tunerデータの抽出・解釈・要約処理（AIによる）
- Web UI（標準GUIとして提供）
- 複数Tuner対応、認証・セキュリティ機構

## 11. 未確定事項・要確認事項

- 実機Tuner側MCPサーバーの実際のツール仕様（`get_status`のフィールド構成・レスポンス形式）が確定次第、仮想Tunerの応答もそれに合わせて更新する
- mDNSのサービスタイプ名の命名規約（例: `_tuner._tcp.local.` など）
- 既存骨組みで使用しているMCP SDK（公式Python SDK / FastMCP等）の確認
- `windows-message-mcp`（Windows/WSL間メッセージ交換）が本ゴールとどう関わるか、Tuner（仮想・実機とも）はWSLではなくネットワーク上の別プロセス/実機を想定しているため、通常は直接関与しない見込みだが要確認

---

## 実施結果（2026-08-24 セッション）

v-01は上記の受け入れ基準（第9章）をすべて満たして完了した。加えて、指示書のスコープ外だが以下を追加実装した:

- 番組表(EPG)機能: `virtual_tuner/epg.py`、`get_program_guide`/`list_channels`ツール、
  東京7局(NHK総合/NHK Eテレ/日本テレビ/テレビ朝日/TBS/テレビ東京/フジテレビ)のサンプルデータ
- `epg-renderer`: Windowsネイティブの表示専用MCPサーバー。`render_epg`ツールで
  番組表をチャンネル×時間の格子HTMLとして描画し、ブラウザで表示する
  (現在時刻ライン、クリック詳細パネル付き)

第11章の未確定事項のうち、「windows-message-mcpとの関係」は確認済み: 無関係と確定。
その他（実機Tuner仕様、mDNS命名規約、MCP SDK確認）は引き続き未確定。
MCP SDKは公式Python SDK (`mcp` v2.0.0台、`mcp.server.mcpserver.MCPServer`)を使用した。

詳細な状態・再開手順はリポジトリルートの `CLAUDE.md` を参照。
