# d2v Focus Preview（VS Code 拡張）

`iida-network-model` の YAML を編集中、**カーソル位置のノード（device）周辺**の
構成図を横のパネルにライブ表示する VS Code 拡張です。
レンダリングは d2v の**決定論経路（LLM 不使用）**を使うため、編集のたびに即時・
無料で再描画されます。

## 仕組み

```
VS Code エディタ (YAML) ──カーソル/本文──▶ 拡張 ──▶ d2v serve
   ▲                                                  │ POST /api/focus/preview
   └────── ノードクリックで定義行へジャンプ ◀── SVG ──┘
```

## 前提

拡張はローカルで動く d2v の Web サーバ（FastAPI）に接続します。先にサーバを
起動してください。

```bash
# リポジトリルートで
uv sync --extra web         # 初回のみ
python main.py serve        # 既定 http://127.0.0.1:8000
```

別ポートで動かす場合は、拡張設定 `d2v.serverUrl` を合わせてください。

## ビルドと実行（開発）

```bash
cd editor
npm install
npm run compile
```

VS Code で `editor/` を開き、F5（Run Extension）で拡張開発ホストを起動します。
`iida-network-model` の YAML（例: `examples/sample_topology_small.yaml`）を開き、
コマンドパレットから **「d2v: フォーカスプレビューを開く」** を実行します。

## 使い方

- YAML 内でカーソルを動かすと、その device 周辺の図が自動更新されます。
- `physical-connection` の中にカーソルを置くと、その**両端 2 台**が対象になります。
- パネル上部の **ホップ**スライダーで表示範囲（1〜3 ホップ）を切り替えられます。
- **追従**チェックで自動更新の ON/OFF を切り替えられます（`d2v.toggleFollow` でも可）。
- 図の**ノードをクリック**すると、対応する device の定義行へジャンプします。
- 編集途中で YAML が未完成のときは直前の図を保持し、「解析待ち」を表示します。

## 編集支援機能

プレビューに加えて、YAML 編集を助ける機能を提供します。

- **design lint（波線）**: 保存・オープン時にサーバの `/api/lint` を叩き、宙ぶらりん
  リンク・重複・IP 整合・単一障害点（SPOF）などの設計上の問題を該当行に波線表示します。
- **補完**: `device-id:` / `interface-id:` の値を、ドキュメント内の既存定義から補完します
  （`physical-connection` の endpoint 記述で特に有効）。
- **スニペット**: `d2v-device` / `d2v-interface` / `d2v-connection` を入力すると、
  device / interface / 接続のテンプレートを展開します。

## 設定

| 設定キー | 既定値 | 説明 |
|----------|--------|------|
| `d2v.serverUrl` | `http://127.0.0.1:8000` | d2v serve のベース URL |
| `d2v.hops` | `1` | 何ホップ先まで表示するか（1〜3） |
| `d2v.follow` | `true` | カーソル追従の ON/OFF |
| `d2v.debounceMs` | `250` | 更新までの待ち時間（ミリ秒） |
