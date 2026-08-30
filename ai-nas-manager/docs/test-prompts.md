# テストプロンプト集

このファイルの目的: v-01で実装した機能を、実際のユーザー発話に近い形で
一通り確認するためのプロンプト集。新しいセッションでMCPツールがきちんと
反映されているか、退行がないかの手動リグレッションテストとして使う。

前提: `virtual_tuner`が起動していること、`media-renderer`/`ai-nas-manager`
がClaude Code/Desktopに登録済みでツールが読み込まれていること(新しい
ツールを追加した直後は再起動が必要)。

## 1. 検索(search_media) — 2026-08-30時点の登録済みデータが対象

| プロンプト | 期待される動き |
|---|---|
| 「人物が映っている映像ある?」 | `search_media("人物")` → carphone_qcif / claire_qcif / foreman_qcif の3件(origin: library) |
| 「交通の映像を探して」 | `search_media("交通")` → bus_cif / highway_cif の2件 |
| 「群衆が映っている映像は?」 | `search_media("群衆")` → pedestrian_area_1080p25 の1件 |
| 「自己平衡ロボットの映像を見せて」 | `search_media("自己平衡")` → GyroBoy(CH1、origin: channel) |
| 「黄色いパーツをアームで投入している場面を見せて」 | `search_media("黄色")` → ColorSorter(CH2)がヒット。続けて`get_fragment_details(2)`で25.0〜38.4秒を特定 |
| 「Bluetoothの設定映像ある?」 | `search_media("Bluetooth")` → BT-Enable.webm(library)がヒット |
| 「存在しないキーワードXYZの映像」 | 空配列。「見つかりませんでした」と回答 |

## 2. 再生・シーク(media_renderer)

| プロンプト | 期待される動き |
|---|---|
| 「tunerの2chを再生して」 | `get_media_location(2)`→UNC変換→`play_channel(...)`。CH2(ColorSorter)が再生される |
| 「ch3を表示して」 | 同様にCH3(Puppy)が再生される。tagがプレイヤー画面上部に表示される |
| 「もう少し後ろの場面に飛んで」「30秒くらいの場面に」 | `seek(30)`。ソースは変えず位置だけ変わる |
| 「今何が再生されてる?」 | `get_playback_status()`。現在のchannel/title/tag/commandが返る |
| 「止めて」 | `stop_media()`。再生が停止する |
| 「黄色いパーツの場面を見せて」(検索+シーク複合) | `search_media`→`get_fragment_details`→`get_media_location`→`play_channel(seek_seconds=25.0)`。該当場面から再生開始 |
| 「自転車の男性が映っている場面、どっちか選ばせて」 | `search_media`等で候補を絞り込み→`render_choices([...])`でサムネイル2件をブラウザに並べて表示。ユーザーがクリック後、`get_selection()`で選択結果を取得し`play_channel(...)`で再生 |

## 3. 自律的な発見・登録(list_pending_media / analyze_video / register_media)

| プロンプト | 期待される動き |
|---|---|
| 「LEGOのvideosフォルダに未処理の映像ある?」 | `list_pending_media(<videos直下>)` → walkthrough_slide*/USB.webm等、core-models以外かつ未登録のものが返る(登録済み分は自動で除外) |
| 「この映像(パス指定)を解析して」 | `analyze_video(path)` → fragment候補+代表フレームパス。Claudeがフレームを見て言語化し、`register_media`で登録するところまで一連で行う |
| 「さっき解析したやつ、検索で出てくるか確認して」 | 登録直後に`search_media`でヒットすることを確認 |

## 4. 番組表(EPG、Tuner系)

| プロンプト | 期待される動き |
|---|---|
| 「番組表を見せて」 | `discover_tuners`→`get_tuner_program_guide`→`render_epg`。7局30番組がブラウザに表示される |
| 「NHK総合の番組表だけ見せて」 | `get_tuner_program_guide(channel="NHK総合")`で絞り込み |

## 5. 画像表示

| プロンプト | 期待される動き |
|---|---|
| 「この画像(パス指定)を見せて」 | UNC変換→`render_picture(path)`。新規タブで画像が開く |

## 6. 段階1の直接呼び出し(境界検出のみ、開発者向け)

| プロンプト | 期待される動き |
|---|---|
| 「この映像は何個のfragmentに分かれる?」 | `analyze_video`のfragments配列の長さを回答。編集済み映像(LEGO)は複数、連続ショット映像(Xiphテストセット)は1個になるはず(13節参照) |

## 既知の制約(2026-08-30時点)

- CH1〜4以外の`get_fragment_details`はエラーになる(channel引数はmedia_catalog専用。
  library由来のfragment詳細はsearch_mediaの結果に直接含まれる)
- `list_pending_media`は指定ディレクトリ直下のみ(非再帰)
- 段階2・3(言語化・統合)の完全自動化は`feature/semantic-tagging-api-automation`
  ブランチに実装済みだが`ANTHROPIC_API_KEY`未設定のため未検証
