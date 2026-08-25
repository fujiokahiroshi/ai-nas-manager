# 映像セマンティックタグ付けの予備実験

ステータス: 予備実験・PoC段階。MCPツールとしては未公開(server.pyから未参照)。

## 目的

media_rendererが「movieを再生する」機能なのに対し、こちらは**movieの中身を理解する**
ための実験。将来的にAI NAS managerが「この動画で何が起きているか説明して」
「野球している動画を探して」のような意味検索・要約に対応するための土台。

検討したいこと: **各映像にどういうタグ付けが必要か**、という設計そのもの。

## 想定パイプライン

1. 単純な映像を用意する
2. 映像をフラグメント(時間区間)に分割する
3. 各フラグメントに意味づけ(タグ付け)する
4. フラグメントのタグ・映像データを連動させ、映像全体のセマンティックを決定する

## 現在の実装(PoC)

- `ai-nas-manager/semantic_fragments.py` — シーン説明文(将来的には映像解析AIが
  生成する想定、現状は人手を想定)から、ルールベースのキーワードマッチングで
  主体・タグ・物語上の役割(opening/activity/closing)を抽出する
  `build_semantic_fragment()`。複数シーンを統合し全体要約を作る`build_digest()`。
- `ai-nas-manager/segment_alignment.py` — 映像断片(`VideoFragment`)・音声断片
  (`AudioFragment`)・意味断片(`SemanticFragment`)を、時間の重なりを根拠に
  対応づける`align_fragments()`。重なり幅の比率を信頼度として付与。
- テスト(`tests/test_segment_alignment.py`, `tests/test_semantic_fragments.py`)は
  いずれも「3人の子供が野球をしている」という単一のサンプルシーンで動作確認。

## サンプル映像データ

`ai-nas-manager/scripts/generate_semantic_sample_media.sh`(ffmpeg依存、要
`apt install ffmpeg fonts-noto-cjk`)で、パイプライン第1段階「単純な映像を用意する」
のためのサンプルを5本生成できる(`ai-nas-manager/media/semantic_samples/`、
gitignore対象・都度再生成する想定)。あえて野球以外のシーンも混ぜているのは、
現状の`semantic_fragments.py`が野球専用に近いハードコードのため、他のシーンで
どこまで汎用的に動くかを検証しやすくするため。

- `sample01_cooking.mp4` — 台所で大人が料理をしている
- `sample02_dog_walk.mp4` — 公園で子供が犬の散歩をしている
- `sample03_reading.mp4` — 子供が静かに絵本を読んでいる
- `sample04_soccer.mp4` — 子供たちが公園でサッカーをしている
- `sample05_baseball.mp4` — 子供たちが公園で野球をしている(既存デモと同系統)

各10秒、H.264+AAC/MP4。シーン説明文を映像内にテキストとして焼き込んでいるだけの
プレースホルダーで、実際の映像認識で生成したものではない
(「既知の制約」参照)。日本語テキストの表示には`fonts-noto-cjk`が必要
(DejaVu等の欧文フォントだと文字化け(豆腐)になる)。

## 既知の制約(2026-08-25時点)

- 実際の動画ファイルからVideoFragment/AudioFragmentを生成する仕組みは未実装
  (シーン説明文・書き起こしを用意する前段=画像認識/音声認識との接続が必要)。
- `semantic_fragments.py`のキーワードマッチングは野球サンプル専用に近い
  ハードコードを含み、汎用的なタグ体系にはまだなっていない。
- `ai-nas-manager/server.py`からは呼ばれておらず、MCPツールとして未公開。

## 経緯についての補足

このコードはClaude Desktopの別セッション(Cowork)で作成されたもので、
その会話の詳細(タグ体系についての具体的な検討過程など)は復元できなかった。
このファイルは、その経緯・意図が今後また失われないよう、要点を記録したもの。
