# media_renderer 設計 (v-01拡張案)

ステータス: 実装済み・実機確認済み(2026-08-25)。
CH1〜CH12のダミー動画再生・停止・画像表示・リーダー/フォロワー方式・
Claude Code/Desktop両方への登録まで一通り動作確認済み。
残タスクは8節参照。

## 1. ゴール

- Windows側に新規MCPサーバー `media_renderer` を追加し、picture/movieの再生を担当させる。
- ai-nas-manager (WSL側) を拡張し、「CH1〜CH12」の仮想メディアチャンネルを用意する。
  各チャンネルに仮想的なmovie streamを割り当て、チャンネル指定で再生可能にする。
- Claudeが両者を橋渡しする(既存のwindows-message-mcp / epg-renderer 連携と同じ設計原則):
  各MCPサーバーは互いの存在を知らない。ai-nas-managerはWSL上のパスを返すだけ、
  media_rendererはWindows上のパス(UNC)を受け取って表示するだけ。変換と指示はClaudeが行う。
- 動画配信方式の最終目標: 将来的にはWSL側からYouTube Liveと同種のセグメント配信方式
  (HLS)でストリーミングすること、および外部YouTube Live等の実URLをチャンネルとして
  埋め込むことの両方を見据える。v-01ではローカルファイル直接参照のみ実装するが、
  インターフェース(3.3節)はこの2つを後から追加できる形にしておく。

## 2. 全体アーキテクチャ

```
[ユーザー] --自然言語--> [Claude]
                            |  get_media_location(channel)
                            v
                   [ai-nas-manager MCP] (WSLネイティブ)
                     media_catalog.py : CH1-12 -> WSL上のファイルパス
                            |
                            | (WSL絶対パスを返す)
                            v
                        [Claude] --WSLパス→UNCパス変換(file方式の場合)--
                            |  play_channel(source_type, source_value, channel, title)
                            |  stop_media()
                            v
                  [media_renderer MCP] (Windowsネイティブ)
                     既定ブラウザでプレイヤーページを表示・制御
```

- ai-nas-managerは「どこに何があるか」を教えるだけで、実際のレンダリングには一切関与しない。
- media_rendererはWSLの存在を知らず、渡されたUNCパスをただ再生するだけ。
- 両者を繋ぐのはClaude(自然言語理解とパス変換)。既存の設計原則をそのまま踏襲する。

## 3. ai-nas-manager 側の拡張

### 3.1 仮想メディアチャンネル (media_catalog.py, 新規)

`virtual_tuner`のダミーEPGと同じ位置づけ。実コンテンツが用意できるまでの代役として、
CH1〜CH12にそれぞれ仮想movie streamを割り当てる。

- 実体は `ai-nas-manager/media/` 配下の動画ファイル(仮ファイル)。
- 実装時の検討事項: 実ファイルをどう用意するか
  - 候補A(推奨・**v-01ではffmpegが必要**): ffmpegで合成したダミークリップ
    (カラーバー+チャンネル番号のテキスト焼き込み、30秒程度をループ再生)を12本生成。
    ライセンス問題がなく、`virtual_tuner`のダミーEPGとトーンが揃う。
    現時点でWSL側にffmpegは未インストール(2026-08-25確認、`which ffmpeg`該当なし)。
    実装に着手する際は`apt install ffmpeg`等でのインストールが前段作業として必要。
    3.3節で触れた「実機Tunerに移行後はWSL側のトランスコードが不要になる可能性」は
    あくまで**実機置き換え後**の将来の話であり、virtual_tunerによるダミー実装が
    続く間(v-01)はffmpegでのダミークリップ生成が前提になる。両者を混同しないこと。
    コーデックは**H.264(映像)+AAC(音声)のMP4コンテナ**に固定する
    (Chrome/Edgeで確実に再生できる組み合わせ。4.2節の検証結果を参照)。
  - 候補B: 少数のサンプル動画(パブリックドメイン等)を用意し、複数チャンネルで使い回す。
- チャンネル定義は `{channel: int, title: str, source: {type: "file", path: str}}` のリストとして
  Pythonモジュール内にハードコード(v-01スコープ。設定ファイル化は将来検討)。
  `source`を`path`単体ではなく種別付きにする理由は3.3節を参照。

### 3.2 新規MCPツール

既存の`list_media(path)`(ダミー、汎用ファイルスキャン用として温存)とは別に、
チャンネル専用のツールを追加する:

- `list_media_channels() -> list[dict]`
  戻り値: `[{"channel": 1, "title": "CH1"}, ..., {"channel": 12, "title": "CH12"}]`
  (`get_tuner_channels`と同じ役割の、メディア版)

- `get_media_location(channel: int) -> dict`
  戻り値: `{"channel": 5, "title": "CH5", "source": {"type": "file", "path": "/home/hiroshi/test/ai-nas-manager/media/ch05.mp4"}}`
  存在しないチャンネル番号はエラー(`get_tuner_status`の未知Tuner名と同様のパターン)。

### 3.3 メディアソースの抽象化(将来の配信方式を見据えた設計)

動画再生の最終目標は、ローカルファイルの直接参照だけでなく、
**YouTube Liveが使っているようなセグメント配信方式(HLS)でWSL側からWindows側へ
ストリーミングすること**、および**外部YouTube Live等の実URLをチャンネルとして
埋め込むこと**の両方を見据えている。この2つは実装の重さが大きく異なるため、
v-01では**実装はせず、インターフェースだけそれらを受け入れられる形にしておく**。

実機Tunerを見据えた補足(2026-08-25時点の想定、同日中に確度が上がった): 実際に
採用予定のUSB Tuner(Amlogic搭載)はハードウェアエンコーダを持ち、クラウド録画・
リアルタイム再生を既に実現して市場で使われている製品。そのため`hls`方式を実装する
段になっても、**ai-nas-manager(WSL側)がffmpeg等でソフトウェアトランスコードを
行う必要はない可能性が高い**。Tuner自身が既にエンコード済みのストリーム(URL)を
吐き出すのであれば、ai-nas-manager側の役割は`get_tuner_status`等と同じく
「Tunerが提供するストリームURLを問い合わせて中継するだけ」に留まる見込み。
つまり`hls`型の`source.url`は、WSL側で新規に構築する配信サーバーのURLではなく、
**実機Tunerが自前で公開しているストリームURLをそのまま右から左に渡すだけ**に
なる可能性が高い。この場合、前回検証した「WSL→Windowsのネットワーク経路が
十分な速度で運べるか」という懸念はそのまま活きる(Tunerのストリームも同じ経路を
通ってWindows側に届く)一方、「エンコードの負荷や実装コスト」は心配しなくてよい。

さらに重要な補足(ユーザーからの情報、2026-08-25): この実機は**日本の放送規格を
30年以上手がけてきた会社が開発したもの**で、SoCはAmlogicを使用しているが
**ファームウェアは全て自社製**。CLI/MCP対応も自社で行う予定とのこと。つまり:

- 放送規格(字幕放送等を含む)の扱いについて、素人推測ではなく蓄積されたノウハウが
  背景にある会社の製品であり、上記の「ハードウェアエンコード済みストリームを吐く」
  という前提の確度が高い。
- ファームウェア・CLI/MCPインターフェースを自社で完全にコントロールできるため、
  「Tunerがどんな情報を公開するか」は外部SDKの制約を受けず、**必要な機能
  (ストリームURL、字幕データ抽出等)を自分たちの設計で追加できる**。
  これは字幕放送との比較検証構想(`ai-nas-manager/docs/semantic-tagging-experiment.md`
  7節)を実現する上でも有利な条件になる。

**注意: これは実機Tuner導入後の将来の話であり、v-01(virtual_tunerによるダミー実装)には
適用されない。** v-01で`source.type == "file"`用のダミー動画12本を用意するのに
ffmpegは引き続き必要(3.1節)。将来`hls`型のダミー配信サーバーをWSL側に自前で
構築して先行実験する場合も、その配信サーバー自体はffmpegで作ることになる。
「WSL側のffmpegトランスコードが不要になる可能性がある」のは、あくまで
**実機Tunerに置き換わった後**の話に限られる(実機Tunerの実際の出力方式が
分かり次第、この節を更新する)。

`source`は将来的に以下のいずれかの形を取りうる、というのが設計上の約束事:

- `{"type": "file", "path": "/home/hiroshi/test/..."}` — v-01で実装する形。
  WSL上のファイルパス。Claudeがpath→UNC変換して`media_renderer`に渡す(6節)。
- `{"type": "hls", "url": "http://<Tunerまたは中継先>/..."}` — 将来。
  実機Tunerが自前で公開するストリームURル(上記補足)、またはWSL側で配信する場合の
  URL。いずれにせよClaudeによるパス変換は不要(URLはそのままWindows側から
  アクセス可能な想定)。
- `{"type": "url", "url": "https://www.youtube.com/..."}` — 将来。
  外部の実配信URL。media_renderer側で埋め込みプレイヤー(iframe等)を出し分ける。

v-01でのスコープ:
- `get_media_location`が返す`source.type`は`"file"`のみ。
- `media_renderer.play_channel`は`source`をそのまま受け取るパラメータ形状にし、
  内部では`type == "file"`のときだけ実装する(他の`type`は未対応エラーを返す)。
  これにより、将来`hls`/`url`対応を追加する際に**ツールのシグネチャ自体は
  変更不要**になる。

**`file`方式(v-01本体)の実現可否を左右する読み込み速度の検証結果(2026-08-25、実機確認済み):**

4.2節で検証したのは画像1枚(数百バイト)の読み込みで、`file://`が「ブラウザの
オリジン制約を越えて開けるか」の確認にはなったが、動画再生に必要な**連続大容量読み込みと
シーク応答性**は未確認だった。これは本節後半で検証するHLS用ネットワーク経路
(WSL2のlocalhost TCP転送)とは別物で、`file://wsl.localhost/...`が実際に使うのは
UNCファイル共有(9Pプロトコル)であり、計測経路が異なるため個別に確認が必要だった。

WSL側に100MBの実ファイルを作成し、Windows側Pythonから
`\\wsl.localhost\Ubuntu\home\hiroshi\test\...`のUNCパスで直接`open()`して測定:

- 連続読み込み: 100MBを0.68秒で読了 = **約146 MB/s(約1172 Mbps)**。
  動画のビットレート(1080pで数Mbps程度)に対し圧倒的な余裕。
- シーク応答性(動画のスクラブ相当): ランダム位置への`seek()`+64KB読み込みを20回、
  最大15.8ms・平均0.8ms。ローカルディスク相当の即応性で、ネットワーク越しにありがちな
  「シークの度に数百ms待たされる」問題は見られなかった。

結論: `file`方式(v-01の主経路)は速度面・応答性面のいずれもボトルネックにならない
ことを確認できた。残る不確定要素は「コーデックがブラウザで再生できるか」のみだが、
これは環境固有の未知数ではなく一般的なブラウザ実装の既知の仕様(H.264+AAC/MP4は
Chrome/Edgeで標準サポート)なので、ダミークリップ生成時にこの組み合わせを使う前提で
問題ない(3.1節のffmpeg生成時にコーデックとして明示する)。

**`hls`方式の実現可否を左右する通信経路の検証結果(2026-08-25、実機確認済み):**

`hls`方式は、file://によるUNC直接参照とは別の経路(WindowsのブラウザからWSL上の
HTTPサーバーへネットワーク越しにセグメントを取得し続ける経路)が必要で、これが
実用的な速度・安定性で動くかどうかがHLS化できるかどうかの分水嶺だった。
そこでWSL側に50MB配信・チャンク連続配信(0.5秒毎×30回、合計6MB/約15秒)を行う
使い捨てのHTTPサーバーを立て、Windows側から`http://localhost:8899/...`
(CLAUDE.mdに記載の「Windows→WSLへのlocalhostアクセスは自動転送」を利用)で
実測した。結果:

- 生スループット: 50MBを2.26秒で受信 = **約22 MB/s(約177 Mbps)**。
  一般的なライブ配信のビットレート(1080pで5〜8Mbps程度)に対して十分過ぎる余裕がある。
- 継続配信の安定性: 6MBを17.05秒で受信(送信側は0.5秒間隔×30回=約15秒を想定)、
  読み取り間の最大ギャップは0.51秒・平均0.16秒で、サーバーの送信間隔とほぼ一致し
  異常な停止(stall)や接続断は発生しなかった。

結論: Windows→WSLのネットワーク経路(localhost転送)は、HLSのような継続的な
HTTPストリーミングを実用速度で運べることを確認できた。`hls`方式は
「将来やるとしても経路がボトルネックにならない」ことが分かった段階であり、
実際のffmpegエンコード・セグメント生成・m3u8プレイリスト管理はまだ未実装
(v-01のスコープ外のまま)。検証に使用したコード(`stream_test_server.py`/
`stream_test_client.py`)はスクラッチ用の一時ファイルであり、本実装には
含めない(検証後に削除済み)。

## 4. media_renderer 側 (Windows, 新規コンポーネント)

epg-rendererと同じ構成を踏襲: Python, Windowsネイティブ, venvはローカルディスクに配置
(`C:\Users\yukik\media-renderer-venv\.venv`)、ソースはリポジトリ内 `media-renderer/server.py`。

### 4.1 MCPツール

- `play_channel(source_type: str, source_value: str, channel: int | None = None, title: str | None = None) -> str`
  `source_type`は`"file"`(v-01で実装、`source_value`はUNCパス)/`"hls"`/`"url"`
  (将来、`source_value`はURL、v-01では未対応エラーを返す)。
  プレイヤーページが未起動なら新規に開き、既に開いていればチャンネル切り替え
  (同じタブ内で動画を差し替え)。3.3節の`source`をClaudeがそのまま(fileの場合は
  UNC変換して)渡す想定。

- `stop_media() -> str`
  再生を停止する。

- `render_picture(path: str) -> str`
  指定パス(UNC)の画像を表示する。epg-rendererの`render_epg`と同じ「都度新規タブ」方式で十分
  (静止画は「停止」概念が不要なため)。

- `seek(position_seconds: float) -> str`(2026-08-25追加)
  ソースを切り替えずに、再生中の位置だけを変更する。
  `ai-nas-manager.get_fragment_details`のstartをそのまま渡せる。

- `get_playback_status() -> dict`(2026-08-25追加)
  現在の再生状態(source/channel/title/tag/command)を返す。ユーザーがブラウザ側で
  直接操作した場合の状態変化も反映される。Claudeが一方的に指示を送るだけでなく、
  状態を問い合わせられるようにするための追加(詳細: semantic-tagging-experiment.md)。

- `render_choices(options: list[dict]) -> str`(2026-08-30追加)
  再生する前に候補をユーザーに選ばせたい場合に使う。各要素は
  `{thumbnail_path, label, source_value, tag?, seek_seconds?, channel?}`。
  呼ぶ度に新しいタブ(`chooser.html`、`player.html`とは別ページ)でサムネイル
  グリッドを表示し、ユーザーがクリックした候補をハイライトする。

- `get_selection() -> dict`(2026-08-30追加)
  `render_choices`でユーザーがクリックした結果を取得する。未選択なら
  `{"selected": None}`、選択済みなら`{"selected": {index, thumbnail_path, label,
  source_value, tag, seek_seconds, channel}}`を返す。そのまま`play_channel`に
  渡せる形にしてある。

`render_choices`/`get_selection`のポーリング方式は`play_channel`の`/state`と同じ
発想だが、状態と内部エンドポイントは別系統(`/choices`, `/internal/render_choices`)
にした。プレイヤーのタブ生存判定(4.4節)とは独立で、`render_choices`は常に
新規タブを開く(`render_picture`と同じ「都度新規タブ」方式。選択操作は毎回単発の
やり取りで、タブを使い回す必要性が薄いため)。

`play_channel`は`seek_seconds: float | None = None`引数も追加(2026-08-25)。
指定するとその秒数の位置から再生を開始する。

`play_channel`は`thumbnail_path: str | None = None`引数も追加(2026-08-30)。
指定すると代表フレーム画像(UNCパス)を画面右下に220x160の枠で重ねて表示する
(タブ生存判定・リーダー/フォロワー転送は既存の`tag`と同じ経路に相乗り)。

実装時のハマりどころ: `<img>`をCSSの`max-width`/`max-height`のみでサイズ指定すると、
`getBoundingClientRect()`は正しい非ゼロサイズを返し`display:block`/`visibility:visible`も
正しいのに、実際の画面には一切描画されない(Edge/Chromium、position:fixedの`<video>`と
共存する場合に再現)。`width`/`height`を明示し`object-fit:contain`を使う実装に変更したところ
描画された。原因はサイズが不定形(max-*のみ)な`<img>`の実際のペイントに関する
Chromium側の癖と推測されるが未特定。今後`<img>`をposition:fixedで重ねる際は
`width`/`height`を明示すること。

### 4.2 実装方式: play/stopの制御をどう実現するか(検証済み)

`webbrowser.open()`は「新しいタブを開く」ことしかできず、既に開いているタブの内容を
Pythonから後から書き換える手段がない。しかしチャンネル切り替えや停止には、
既に開いているプレイヤーページを外部から制御する必要がある。そのため以下の方式を第一候補とする:

**第一候補: file://ページ + ローカル制御HTTPサーバー(ポーリング)**

1. `play_channel`の初回呼び出し時、一時HTMLファイル(`%TEMP%\media-renderer\player.html`)を
   生成して`webbrowser.open()`で一度だけ開く。このページは`<video>`タグと、
   1秒間隔で `http://127.0.0.1:<port>/state` をポーリングするJSを持つ。
2. media_renderer内で小さなHTTPサーバー(標準ライブラリ`http.server`等)を起動し、
   `/state`に現在の再生指示(`{"path": "...", "command": "play"|"stop", "seq": N}`)を
   JSONで返す。`play_channel`/`stop_media`はこの内部状態を更新するだけ。
   CORS対応で `Access-Control-Allow-Origin: *` を付与し、file://オリジンのページからの
   fetchを許可する。
3. ページ側JSは`seq`が変化したら`command`に応じて`video.src`を差し替えて`play()`、
   または`video.pause()`する。

このプロセス内蔵HTTPサーバーは配信(ストリーミング)用途ではなく、あくまで
「今何をすべきか」を伝える制御チャネルに限定する(動画本体のバイト列は流さない)。
実際の映像データはブラウザが`<video src="file://wsl.localhost/...">`で直接UNC経由読み込みする
(既存epg-rendererのfile://方式を踏襲)。

**検証結果 (2026-08-25、実機確認済み):**

デフォルトブラウザ(Edge)で以下2点を確認し、いずれも成功した。フォールバック
(自前ストリーミングサーバーへの格上げ)は不要と判断する。

- file://で開いたローカルページ(`%TEMP%`上)から、別ホスト名のUNCパス
  `file://wsl.localhost/Ubuntu/home/hiroshi/test/...` の画像を`<img>`で読み込めた
  (動画そのものの読み込み速度・シーク応答性は3.3節で別途検証済み)。
- 同じfile://ページから`http://127.0.0.1:<port>/state`への`fetch()`が、
  サーバー側`Access-Control-Allow-Origin: *`により成功した(CORSブロックなし)。

検証に使用したテストコード(`control_server.py` / `bridge_test.html`)はスクラッチ用の
一時ファイルであり、本実装には含めない(検証後に削除済み)。

### 4.3 複数インスタンス対応(リーダー/フォロワー方式) — 必須

Claude CodeとClaude Desktopを同時に起動すると、MCP登録上は同じ`media_renderer`が
別プロセスとして2つ以上同時に立ち上がりうる(`windows-message-mcp`で既に経験済みの問題、
`src/index.ts`)。何も対策しないと、2つ目以降のプロセスは制御HTTPサーバーのポートbindに
失敗し、そのプロセス経由での`play_channel`/`stop_media`が機能しなくなる
(=Claude CodeとClaude Desktopのどちらか一方でしかmedia_rendererが使えない)。

`windows-message-mcp`と全く同じリーダー/フォロワー方式をそのまま流用する:

1. 起動時に固定ポート(例: `39231`、環境変数`MEDIA_RENDERER_HTTP_PORT`で上書き可能)へ
   `127.0.0.1`でbindを試みる。
   - 成功したプロセスが**リーダー**になる。プレイヤー状態(現在のpath/command/seq、
   プレイヤータブが開いているかのフラグ)を実際に保持し、ブラウザタブのopenも
   リーダーだけが行う。`/state`(ページからのポーリング用)もリーダーが応答する。
   - `EADDRINUSE`で失敗したプロセスは**フォロワー**になる。自分ではHTTPサーバーを
     持たず、状態も持たない。
2. フォロワーが`play_channel`/`stop_media`を呼ばれたら、`/internal/play`・`/internal/stop`
   のような内部エンドポイントへHTTPでリーダーに転送する(ツール呼び出し元は
   意識しなくてよい)。
3. 結果として、どちらのプロセス経由でツールが呼ばれても実体は常に1つのリーダーに
   集約され、ブラウザタブも常に1つしか開かれない(フォロワーが誤って別タブを
   開くこともなくなる)。

`windows-message-mcp`との違い: あちらはWSL側からもアクセスさせる必要があるため
`0.0.0.0`でbindしているが、media_rendererの制御サーバーはリーダー/フォロワー間通信も
ブラウザからのpollingもすべてWindows上のlocalhost内で完結するため、`127.0.0.1`限定で
よい(外部露出を避けられる分、windows-message-mcpより安全側に倒せる)。

### 4.4 タブ生存確認(確定方式)

以前は「要検討」としていたが、実装時の手戻りを避けるため方式を確定する。

**新規に`/heartbeat`エンドポイントは作らない。既存の`/state`ポーリング自体を
生存確認に流用する。**

- リーダーは`/state`へのGETを受けるたびに`last_seen = now()`を更新する
  (ページがポーリングを続けている限り、1秒間隔で自然に更新され続ける)。
- `play_channel`/`stop_media`(内部転送されたものも含む)が呼ばれた時点で、
  `now() - last_seen`を見る:
  - 5秒以内(ポーリング間隔1秒の5倍。通常の遅延では超えない余裕を持たせた閾値)
    → ページは生きていると判断し、`webbrowser.open()`は呼ばず状態更新のみ行う。
  - 5秒を超えている、または`last_seen`が未設定(初回) → ページは閉じられている
    (または未起動)と判断し、`webbrowser.open()`で新規タブを開いてから状態を更新する。

既知の残存リスク: 閾値ぎりぎりのタイミングでタブが生きたまま`play_channel`が
呼ばれると、稀に2枚目のタブが開いてしまう可能性はゼロではない
(例: システムスリープ復帰直後など)。発生確率は非常に低く、発生しても
「タブが2枚になる」程度の実害(古い方を手動で閉じれば復旧)のため、
v-01ではこれ以上の対策(多重起動検知など)は行わない。

### 4.5 自動再生ポリシー対応(確定方式)

Chrome/Edgeは「ユーザー操作(クリック等)を伴わない音声付き動画の自動再生」を
既定でブロックする(file://オリジンでも例外ではない)。この設計の`play_channel`は
ポーリングで受け取ったコマンドに応じてJSが`video.play()`を呼ぶだけで、
ブラウザから見ればユーザー操作を伴わない自動再生そのものであり、
**対策なしでは初回再生が失敗する(音が出ない、または再生自体がブロックされる)**。

対策: プレイヤーページの初回表示時に「▶ クリックして開始」のオーバーレイを出し、
ユーザーに一度だけクリックしてもらう。Chromium系ブラウザは同一ページ内で
一度でもユーザー操作を受けると、そのページが存在し続ける限り(タブを閉じるまで)
以降の`video.play()`呼び出し(ポーリング起点の自動実行を含む)を許可し続ける仕様
のため、これで以降のチャンネル切り替え・再生は自動化できる。

運用上の意味: `play_channel`をClaude経由で呼んでも、**プレイヤーページを新規に
開いた直後の1回だけは、ユーザーがブラウザ側で「開始」をクリックする必要がある**。
これはツールの戻り値メッセージで明示する(例: 「ブラウザでプレイヤーを開きました。
初回のみ画面の「開始」ボタンをクリックしてください」)。4.4節のタブ生存判定により
「新規タブを開いた」ケースでのみこの案内を出せば、2回目以降の`play_channel`呼び出しは
無言で切り替わる。

## 5. Claudeの橋渡しフロー例

**「CH5を再生して」**
1. `ai-nas-manager.get_media_location(channel=5)` → `{"source": {"type": "file", "path": "/home/hiroshi/test/ai-nas-manager/media/ch05.mp4"}, ...}`
2. `source.type == "file"`なので、WSLパス→UNCパス変換: `/home/hiroshi/test/...` → `\\wsl.localhost\Ubuntu\home\hiroshi\test\...`
   (`hls`/`url`の場合は変換不要、URLをそのまま渡す。3.3節)
3. `media_renderer.play_channel(source_type="file", source_value=<UNCパス>, channel=5, title="CH5")`
4. (初回・新規タブを開いた場合のみ)戻り値に「初回はブラウザ側で開始ボタンを
   クリックしてください」という案内が含まれるので、Claudeはそれをユーザーに伝える
   (4.5節)。

**「止めて」**
1. `media_renderer.stop_media()` (ai-nas-manager側は関与しない)

**「この画像を見せて」(チャンネル外、任意パス)**
1. ユーザーが指定 or Claudeが特定したWSLパスをUNC変換
2. `media_renderer.render_picture(path=<UNCパス>)`

## 6. パス変換ルール

`/home/hiroshi/test/...` → `\\wsl.localhost\Ubuntu\home\hiroshi\test\...`
(スラッシュをバックスラッシュに置換し、`\\wsl.localhost\Ubuntu`を前置)
`source.type == "file"`のときのみ適用する。この変換はClaude側の責務とし、
ai-nas-manager/media_rendererのどちらにも相手側のパス形式の知識を持たせない
(既存の設計原則を維持)。

## 7. MCP登録

`windows-message-mcp` / `ai-nas-manager` / `epg-renderer`と同様、Claude Code
(`~/.claude.json`)とClaude Desktop(`claude_desktop_config.json`)の両方に
`media_renderer`を個別登録する必要がある。

## 8. 実装ステップ(実績)

1. [x] WSL側に`ffmpeg`をインストール(3.1節)
2. [x] ai-nas-manager: `media_catalog.py` + `list_media_channels`/`get_media_location`ツール追加 + テスト3件(既存9件と合わせ計12件パス)
3. [x] media_renderer: プロジェクト雛形(venv、requirements、server.py) + `render_picture`実装。ブラウザで目視確認済み
4. [x] media_renderer: 制御HTTPサーバーをリーダー/フォロワー方式で実装(4.3節)。
   2プロセス同時起動での実機テストで検証。**この過程でWindows固有のバグを発見・修正**:
   `http.server.HTTPServer`は既定`allow_reuse_address=1`のため、Windowsでは
   2つ目のプロセスもポートbindに成功してしまい、リーダー/フォロワー判定が機能しない
   不具合があった(4.3節の実装に`allow_reuse_address = False`の明示指定を追記済み)。
5. [x] media_renderer: `play_channel`/`stop_media`実装。オーバーレイクリック→実際の
   ダミー動画(H.264+AAC/MP4)再生までブラウザで目視確認済み
6. [x] Claude Code/Desktop両方への登録。別ウィンドウのツール一覧に表示されることを確認済み
7. [x] 疎通確認: `generate_dummy_media.sh`でCH1〜12のダミー動画生成、CH1の実再生を確認
8. [x] MCPツール経由(直接のPython呼び出しではなく、実際のClaude Desktop/Codeから)での
   `play_channel`呼び出しを確認。CH1→CH2のチャンネル切り替えが実機で成功(2026-08-25)。
   タブ生存確認により新規タブを開かずチャンネル切り替えできることも実証済み
9. [ ] CH3〜12は未確認だが、CH2切り替えの成功によりメカニズム自体は実証済みのため
   残りは同一パターンの繰り返しとみなしてよい。`stop_media`の実機確認も未実施
