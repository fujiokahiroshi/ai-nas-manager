# PC App 修正記録（2026-09-13）

## 概要

Windows版 AI NAS Manager PC Appのメディア選択、リアルタイム解析、Scene要約、メニュー表示を修正した。回帰テストは30件すべて成功している。

## 今日の修正項目

### 映像・画像表示

- HTMLの \`hidden\` 属性より \`display:block\` が優先される問題を修正した。
- 映像選択時は映像だけ、画像選択時は画像だけを表示する。

### 別ファイルの選択

- 選択ごとに \`media_revision\` を増加させる。
- Windowsのファイルダイアログ後にHTTP応答が切れても、選択世代が増えていれば新しいメディアを復元する。
- 世代が変わっていない場合は、以前の映像を新しい選択として誤復元しない。
- 解析中に別ファイルを選んだ場合は解析サブプロセスを停止し、スレッド終了後にソースを切り替える。
- 中止した古い解析結果が、新しい画面を後から上書きしないようにした。

### 再生とリアルタイム解析の同期

- 映像の \`pause\` で解析ワーカーを一時停止する。
- \`play\` または \`playing\` で解析を再開する。
- 停止時間を実時間解析の基準時刻から除外し、再開時の時刻ずれを防ぐ。
- 初回読み込み時にも解析モードと一時停止状態を取得する。

### Scene要約

- Gemma出力がトークン上限で途切れた場合に1回再試行する。
- 初回は320 token、再試行は640 tokenとしてJSONの完結率を上げた。

### UIメニュー

- カード画像がメニューより前面に出るスタッキング順の問題を修正した。
- ヘッダーを \`z-index: 100\`、メニューを \`z-index: 101\` にした。
- メニューに最大高さと内部スクロールを追加した。
- 560px以下では全幅メニューに切り替える。

## AI NAS Manager本体の起動

\`\`\`powershell
cd "C:/path/to/ai-nas-manager/ai-nas-manager"
.\start_pc_app.ps1
\`\`\`

ブラウザーを自動で開かない場合:

\`\`\`powershell
.\start_pc_app.ps1 -NoBrowser
\`\`\`

正常に起動済みのPC Appをそのまま使う場合:

\`\`\`powershell
.\start_pc_app.ps1 -KeepExisting
\`\`\`

PC Appは標準で <http://127.0.0.1:8788> を使用する。

## LM Studioとローカルサーバー

PC Appの標準設定:

- API URL: \`http://127.0.0.1:1234\`
- OpenAI互換API: \`http://127.0.0.1:1234/v1\`
- モデル識別子: \`gemma4-12b-qat\`
- 推奨設定: Context Length \`16384\`、Parallel Requests \`1\`

LM StudioのDeveloper / Local Server画面でポート1234のサーバーを開始する。CLIでは次を実行する。

\`\`\`powershell
lms server start
lms load google/gemma-4-12b-qat --identifier gemma4-12b-qat --context-length 16384 --parallel 1 --gpu max -y
\`\`\`

状態確認:

\`\`\`powershell
lms server status
lms ps
Invoke-RestMethod http://127.0.0.1:1234/v1/models
\`\`\`

\`/v1/models\` の \`data[].id\` に \`gemma4-12b-qat\` があれば、PC AppのGemma解析に使用できる。

## トラブル対処

- \`Could not resolve host: github.com\`: GitHubのDNSまたはネットワークの問題。本体が展開済みなら再cloneせず、本体の \`start_pc_app.ps1\` を直接実行する。
- \`127.0.0.1:1234\` へ接続できない: \`lms server status\` と \`lms ps\` を確認する。
- モデルIDがない: 上記の \`lms load\` を実行し、識別子を \`gemma4-12b-qat\` にする。
- \`8788\` が使用中: 起動スクリプトはPC Appのプロセスかを確認し、解析中でなければ新しい版へ入れ替える。別アプリのプロセスは停止しない。

