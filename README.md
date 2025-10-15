# LLM Code Reviewer

ローカルLLMを使用してプロジェクトのコードレビューを自動実行するDockerベースのツールです。

## 特徴

- 🤖 ローカルLLM（LM Studio、OpenWebUI対応）を使用した高度なコードレビュー
- 🎯 カスタマイズ可能なレビュー焦点（セキュリティ、パフォーマンス、PEP8等）
- 🌍 多言語対応（日本語・英語、デフォルトは日本語）
- 🔧 カスタムシステムプロンプトのサポート
- 🚀 ROS2プロジェクト向けに最適化（Python、C++対応）
- 📦 Dockerコンテナで実行し、環境を分離
- 📊 大規模ファイルの自動分割（コンテキスト長を考慮）
- 🎨 JSON形式での詳細な結果出力
- 🚫 除外パターンのサポート（SVNリポジトリ対応）
- 📈 リアルタイム進捗表示（パーセント表示）
- 🔗 ファイルバッチング：小さいファイルを自動的にグループ化して、ファイル間の依存関係を考慮したレビュー
- 📚 リポジトリ概要の共有：他ファイルの概要をプロンプトに含め、断片的なレビューを防止
- 🧠 LangGraphによるレビュー・フロー制御：ファイル収集から結果出力までをグラフで管理し、全体文脈を維持

## 必要要件

- Docker
- LM Studio（または互換性のあるLLM API）
- 実行中のLLMモデル（推奨: qwen3-coder-30b）

## インストール

### Dockerイメージのビルド

```bash
docker build -t llm-code-reviewer .
```

## 使用方法

### 基本的な使い方

```bash
docker run -v /path/to/your/code:/code llm-code-reviewer
```

### カスタム設定での使用

```bash
docker run -v /path/to/your/code:/code llm-code-reviewer \
  --api-url http://192.168.50.136:1234/v1 \
  --model qwen/qwen3-coder-30b \
  --context-length 262144 \
  --output /code/review-results.json \
  --repo-overview-tokens 1500 \
  --repo-overview-lines 25
```

### レビュー焦点のカスタマイズ

```bash
# セキュリティとパフォーマンスに焦点を当てたレビュー
docker run -v /path/to/your/code:/code llm-code-reviewer \
  --review-focus security \
  --review-focus performance

# PEP8チェックを含む包括的なレビュー
docker run -v /path/to/your/code:/code llm-code-reviewer \
  --review-focus pep8 \
  --review-focus bugs \
  --review-focus maintainability
```

### カスタムシステムプロンプトの使用

```bash
# コマンドラインで直接指定
docker run -v /path/to/your/code:/code llm-code-reviewer \
  --system-prompt "あなたは20年の経験を持つシニアエンジニアです。厳格にレビューしてください。"

# ファイルから読み込み
docker run -v /path/to/your/code:/code \
  -v /path/to/prompt.txt:/prompt.txt \
  llm-code-reviewer \
  --prompt-file /prompt.txt
```

### 英語でのレビュー

```bash
docker run -v /path/to/your/code:/code llm-code-reviewer \
  --language en
```

### 除外パターンの指定

```bash
docker run -v /path/to/your/code:/code llm-code-reviewer \
  --exclude "*.pyc" \
  --exclude "build/*" \
  --exclude "install/*"
```

### OpenWebUI APIの使用

```bash
docker run -v /path/to/your/code:/code llm-code-reviewer \
  --api-url http://your-openwebui-server:3000/v1 \
  --api-key your-api-key-here \
  --model your-model-name
```

### ファイルバッチングの調整

デフォルトでは、10000トークン（約40KB）以下のファイルは同じディレクトリ内でまとめてレビューされます。
これにより、グローバル変数やファイル間の依存関係を考慮したレビューが可能になります。

**バッチサイズの制限：**
- 最大5ファイル/バッチ
- コンテキスト長の30%まで使用（プロンプトオーバーヘッドを考慮）
- APIタイムアウト：5分（大規模バッチに対応）

バッチングを無効化する場合：
```bash
docker run -v /path/to/your/code:/code llm-code-reviewer \
  --batch-threshold 999999
```

より小さいファイルのみバッチングする場合：
```bash
docker run -v /path/to/your/code:/code llm-code-reviewer \
  --batch-threshold 5000
```

### リポジトリ概要を活用したクロスファイルレビュー

リポジトリ内の主要ファイルや定義をプロンプトへ共有し、LLMが断片ではなくプロジェクト全体を踏まえてレビューできるようになりました。

```bash
docker run -v /path/to/your/code:/code llm-code-reviewer \
  --repo-overview-tokens 2000 \
  --repo-overview-lines 30
```

`--repo-overview-tokens` ではプロンプトに割り当てる最大トークン数を、`--repo-overview-lines` ではファイルごとの抜粋行数を制御できます。プロジェクトが大きい場合は適宜値を調整してください。

### LangGraphによるレビュー・オーケストレーション

本ツールでは [LangGraph](https://github.com/langchain-ai/langgraph) を使って、以下のステップを明示的なノードとして制御しています。

1. **ファイル収集**：対象ファイルを検出してスコープを確定。
2. **概要生成**：リポジトリ全体の要約を構築し、レビュー時に常に共有。
3. **バッチ作成**：LangGraphの状態にバッチ情報を保持し、クロスファイルレビューを最適化。
4. **レビュー実行**：各バッチに対して概要とコンテキストを付与しながらレビュー。
5. **結果出力**：最終ノードでJSON出力と進捗レポートを完結。

LangGraphを採用したことで、ワークフローがグラフとして可視化可能になり、処理の一部を差し替えたり、追加の検証ステップを挿入する拡張が容易になりました。

リポジトリ概要はLangGraphの状態として保持されるため、すべてのレビュー・ノードが同じプロジェクトコンテキストを参照しながら指摘内容を判断できます。

## コマンドライン引数

| 引数 | デフォルト値 | 説明 |
|------|-------------|------|
| `--api-url` | `http://192.168.50.136:1234/v1` | LLM APIのベースURL |
| `--model` | `qwen/qwen3-coder-30b` | 使用するモデル名 |
| `--context-length` | `262144` | モデルのコンテキスト長（トークン数） |
| `--code-dir` | `/code` | レビュー対象のコードディレクトリ |
| `--output` | `/code/review-results.json` | 結果の出力ファイルパス |
| `--exclude` | （複数指定可） | 除外パターン（グロブ形式） |
| `--review-focus` | `bugs, performance, maintainability` | レビューの焦点（複数指定可） |
| `--language` | `ja` | 出力言語（`ja` または `en`） |
| `--system-prompt` | - | カスタムシステムプロンプト |
| `--prompt-file` | - | システムプロンプトを含むファイルのパス |
| `--api-key` | - | API認証キー（OpenWebUI等で必要な場合） |
| `--debug` | `False` | デバッグモードを有効化（詳細なログ出力） |
| `--batch-threshold` | `10000` | バッチ処理の閾値（トークン数）。この値より小さいファイルはまとめてレビュー |
| `--repo-overview-tokens` | `0` | 各レビューリクエストに添付するリポジトリ概要の最大トークン数（0で無効） |
| `--repo-overview-lines` | `20` | 概要に含める各ファイルの抜粋最大行数 |

### レビュー焦点のオプション

| オプション | 説明 |
|-----------|------|
| `security` | セキュリティ脆弱性（SQLインジェクション、XSS、バッファオーバーフロー等） |
| `performance` | パフォーマンスの問題（不要なループ、メモリリーク、非効率なアルゴリズム等） |
| `pep8` | PEP8コーディング規約の違反（Pythonファイルのみ） |
| `ros2` | ROS2固有の問題（ノードの設計、トピック/サービスの使用方法等） |
| `bugs` | 潜在的なバグとロジックエラー |
| `maintainability` | 保守性（コードの可読性、複雑度、ドキュメンテーション等） |
| `general` | 一般的なコード品質の問題 |

## 出力形式

結果はJSON形式で出力されます：

- `risk_score` は 1〜10 の整数で、不具合の危険度を表します（10: 修正必須、1: 様子見で問題なし）。

```json
{
  "total_files": 10,
  "files_with_issues": 3,
  "results": [
    {
      "file": "src/example.py",
      "reviews": [
        {
          "line": 42,
          "severity": "warning",
          "risk_score": 7,
          "message": "潜在的なnullポインタ参照の可能性があります。line 42の変数がNoneでないことを確認してください。"
        },
        {
          "line": 15,
          "severity": "info",
          "risk_score": 3,
          "message": "PEP8: 関数名は小文字とアンダースコアを使用してください（myFunction → my_function）"
        }
      ]
    }
  ]
}
```

## サポートされるファイル形式

- Python (`.py`)
- C++ (`.cpp`, `.cc`, `.cxx`, `.hpp`, `.h`)
- C (`.c`, `.h`)
- ROS2 Launch (`.launch`)
- YAML (`.yaml`, `.yml`)
- XML (`.xml`)

## デフォルトの除外パターン

以下のパターンはデフォルトで除外されます：

- `*.pyc`, `*.pyo`
- `__pycache__/*`
- `.svn/*`, `.git/*`
- `build/*`, `install/*`, `log/*`

## LM Studioの設定

1. LM Studioを起動
2. モデルをロード（推奨：qwen3-coder-30b）
3. ローカルサーバーを起動
4. サーバーのIPアドレスとポートを確認（例：`http://192.168.50.136:1234`）
5. このツールから接続

## OpenWebUIの設定

1. OpenWebUIサーバーを起動
2. APIキーを取得（設定画面から）
3. 使用するモデルを選択
4. このツールから`--api-url`と`--api-key`を指定して接続

## 進捗表示

実行中は、以下のような進捗表示が出力されます：

```
10個のファイルが見つかりました

[1/10 (10.0%)] レビュー中: src/example.py
[バッチ 2/5 (40.0%)] 3ファイルをまとめてレビュー中:
  - src/utils.py
  - src/helper.py
  - src/config.py
[5/10 (50.0%)] レビュー中: src/main.cpp
...
```

小さいファイルは自動的にバッチ処理され、関連ファイルを一緒にレビューします。

## 使用例

### 基本的なレビュー

```bash
docker run -v ~/my-ros2-project:/code llm-code-reviewer
```

### セキュリティとPEP8に焦点を当てたレビュー

```bash
docker run -v ~/my-ros2-project:/code llm-code-reviewer \
  --review-focus security \
  --review-focus pep8
```

### カスタムプロンプトでの厳格なレビュー

```bash
docker run -v ~/my-ros2-project:/code llm-code-reviewer \
  --system-prompt "あなたは経験豊富なROS2エンジニアです。バグ、セキュリティ問題、パフォーマンスの問題を見逃さず、厳格にレビューしてください。" \
  --review-focus security \
  --review-focus performance \
  --review-focus ros2
```

## トラブルシューティング

### API接続エラー

LM Studioが起動していることと、指定したURLが正しいことを確認してください。また、ネットワーク設定でポートがブロックされていないか確認してください。

### タイムアウトエラー

大きなファイルや複雑なコードの場合、レビューに時間がかかることがあります。現在のAPIタイムアウトは5分（300秒）に設定されています。それでもタイムアウトが発生する場合は、`reviewer.py`の`API_TIMEOUT_SECONDS`定数を調整してください。

### メモリ不足

大規模プロジェクトの場合、十分なGPUメモリが必要です。qwen3-coder-30bモデルには大容量のGPUメモリ（推奨128GB以上）が必要です。

### 日本語出力が文字化けする

Docker環境のロケール設定を確認してください。UTF-8がサポートされていることを確認してください。

## 貢献

プルリクエストを歓迎します。大きな変更の場合は、まずissueを開いて変更内容を議論してください。

## ライセンス

MIT License

## 作者

Devin AI (@y1618)
