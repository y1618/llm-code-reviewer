#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
import requests
import fnmatch

MAX_FILES_PER_BATCH = 5  # Maximum number of files to batch together
BATCH_TOKEN_RATIO = 0.3  # Use 30% of context length for batch content (leaving room for prompt overhead)
API_TIMEOUT_SECONDS = 300  # 5 minutes timeout for LLM API calls


class CodeReviewer:
    def __init__(
        self,
        api_url: str,
        model: str,
        context_length: int,
        output_path: str,
        exclude_patterns: List[str],
        code_dir: str,
        review_focus: List[str],
        language: str,
        system_prompt: Optional[str] = None,
        api_key: Optional[str] = None,
        debug: bool = False,
        batch_threshold: int = 10000,
        repo_overview_tokens: int = 0,
        repo_overview_lines: int = 20
    ):
        self.api_url = api_url.rstrip('/')
        self.model = model
        self.context_length = context_length
        self.output_path = output_path
        self.exclude_patterns = exclude_patterns
        self.code_dir = Path(code_dir)
        self.review_focus = review_focus
        self.language = language
        self.system_prompt = system_prompt
        self.api_key = api_key
        self.debug = debug
        self.batch_threshold = batch_threshold
        self.repo_overview_tokens = repo_overview_tokens
        self.repo_overview_lines = repo_overview_lines
        self.results = []
        self.repo_overview_entries: List[Dict[str, Any]] = []
        
        self.supported_extensions = {
            '.py': 'Python',
            '.cpp': 'C++',
            '.cc': 'C++',
            '.cxx': 'C++',
            '.hpp': 'C++ Header',
            '.h': 'C/C++ Header',
            '.c': 'C',
            '.xml': 'XML',
            '.launch': 'ROS Launch',
            '.yaml': 'YAML',
            '.yml': 'YAML',
        }
        
        self.focus_descriptions = {
            'security': {
                'ja': 'セキュリティ脆弱性（SQLインジェクション、XSS、バッファオーバーフロー等）',
                'en': 'Security vulnerabilities (SQL injection, XSS, buffer overflow, etc.)'
            },
            'performance': {
                'ja': 'パフォーマンスの問題（不要なループ、メモリリーク、非効率なアルゴリズム等）',
                'en': 'Performance issues (unnecessary loops, memory leaks, inefficient algorithms, etc.)'
            },
            'pep8': {
                'ja': 'PEP8コーディング規約の違反（Pythonファイルのみ）',
                'en': 'PEP8 coding standard violations (Python files only)'
            },
            'ros2': {
                'ja': 'ROS2固有の問題（ノードの設計、トピック/サービスの使用方法、ライフサイクル管理等）',
                'en': 'ROS2-specific issues (node design, topic/service usage, lifecycle management, etc.)'
            },
            'bugs': {
                'ja': '潜在的なバグとロジックエラー',
                'en': 'Potential bugs and logic errors'
            },
            'maintainability': {
                'ja': '保守性（コードの可読性、複雑度、ドキュメンテーション等）',
                'en': 'Maintainability (code readability, complexity, documentation, etc.)'
            },
            'general': {
                'ja': '一般的なコード品質の問題',
                'en': 'General code quality issues'
            }
        }
        
    def should_exclude(self, file_path: Path) -> bool:
        relative_path = str(file_path.relative_to(self.code_dir))
        
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(file_path.name, pattern):
                return True
        
        exclude_dirs = ['.git', '.svn', '__pycache__', 'node_modules', 'build', 'install', 'log']
        for part in file_path.parts:
            if part in exclude_dirs:
                return True
                
        return False
    
    def find_files(self) -> List[Path]:
        files = []
        for ext in self.supported_extensions.keys():
            for file_path in self.code_dir.rglob(f'*{ext}'):
                if file_path.is_file() and not self.should_exclude(file_path):
                    files.append(file_path)
        return sorted(files)

    def build_repo_overview(self, files: List[Path]):
        if self.repo_overview_tokens <= 0:
            return

        overview_entries = []

        for file_path in files:
            try:
                content = file_path.read_text(encoding='utf-8')
            except Exception:
                continue

            summary_lines = self.summarize_file_for_overview(file_path, content)
            if not summary_lines:
                continue

            relative_path = file_path.relative_to(self.code_dir)
            language = self.supported_extensions.get(file_path.suffix, 'Unknown')
            entry_text = (
                f"File: {relative_path}\n"
                f"Language: {language}\n"
                "Summary:\n"
                + "\n".join(summary_lines)
            )

            overview_entries.append({
                'path': file_path,
                'text': entry_text,
                'tokens': self.estimate_tokens(entry_text)
            })

        overview_entries.sort(key=lambda x: str(x['path']))
        self.repo_overview_entries = overview_entries

        if self.debug and self.repo_overview_entries:
            total_tokens = sum(e['tokens'] for e in self.repo_overview_entries)
            print(
                f"[DEBUG] プロジェクト概要エントリ数: {len(self.repo_overview_entries)} (推定トークン: {total_tokens})",
                file=sys.stderr
            )

    def summarize_file_for_overview(self, file_path: Path, content: str) -> List[str]:
        lines = content.split('\n')
        summary_lines: List[str] = []
        max_lines = max(1, self.repo_overview_lines)

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            if file_path.suffix == '.py':
                if stripped.startswith(('class ', 'def ', '@')):
                    summary_lines.append(stripped)
                elif len(summary_lines) < 3:
                    summary_lines.append(stripped)
            else:
                summary_lines.append(stripped)

            if len(summary_lines) >= max_lines:
                break

        if not summary_lines:
            summary_lines = lines[:max_lines]

        return summary_lines

    def get_repo_overview_context(self, exclude_paths: Optional[List[Path]] = None) -> str:
        if self.repo_overview_tokens <= 0 or not self.repo_overview_entries:
            return ""

        exclude_set = {p.resolve() for p in exclude_paths} if exclude_paths else set()
        collected = []
        total_tokens = 0

        for entry in self.repo_overview_entries:
            if entry['path'].resolve() in exclude_set:
                continue

            if total_tokens + entry['tokens'] > self.repo_overview_tokens:
                break

            collected.append(entry['text'])
            total_tokens += entry['tokens']

        if not collected:
            return ""

        if self.language == 'ja':
            header = "プロジェクト全体の概要:\n"
        else:
            header = "Repository overview:\n"

        return header + "\n\n".join(collected)
    
    def batch_files(self, files: List[Path]) -> List[List[Path]]:
        batches = []
        current_batch = []
        current_tokens = 0
        max_batch_tokens = int(self.context_length * BATCH_TOKEN_RATIO)
        
        if self.debug:
            print(f"[DEBUG] 最大バッチトークン数: {max_batch_tokens}, 最大ファイル数/バッチ: {MAX_FILES_PER_BATCH}", file=sys.stderr)
        
        files_by_dir = {}
        for file_path in files:
            parent = file_path.parent
            if parent not in files_by_dir:
                files_by_dir[parent] = []
            files_by_dir[parent].append(file_path)
        
        for directory in sorted(files_by_dir.keys()):
            dir_files = sorted(files_by_dir[directory])
            
            for file_path in dir_files:
                try:
                    content = file_path.read_text(encoding='utf-8')
                    file_tokens = self.estimate_tokens(content)
                    
                    if file_tokens > self.batch_threshold:
                        if current_batch:
                            batches.append(current_batch)
                            current_batch = []
                            current_tokens = 0
                        batches.append([file_path])
                    else:
                        would_exceed_tokens = current_tokens + file_tokens > max_batch_tokens
                        would_exceed_file_count = len(current_batch) >= MAX_FILES_PER_BATCH
                        
                        if (would_exceed_tokens or would_exceed_file_count) and current_batch:
                            batches.append(current_batch)
                            current_batch = []
                            current_tokens = 0
                        
                        current_batch.append(file_path)
                        current_tokens += file_tokens
                except Exception as e:
                    if self.debug:
                        print(f"[DEBUG] ファイル読み込みエラー {file_path}: {e}", file=sys.stderr)
                    if current_batch:
                        batches.append(current_batch)
                        current_batch = []
                        current_tokens = 0
                    batches.append([file_path])
        
        if current_batch:
            batches.append(current_batch)
        
        return batches
    
    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4
    
    def split_file_content(self, content: str, file_path: Path) -> List[Dict[str, Any]]:
        chunks = []
        lines = content.split('\n')
        
        max_chunk_tokens = self.context_length // 2
        current_chunk = []
        current_start_line = 1
        current_tokens = 0
        
        for i, line in enumerate(lines, 1):
            line_tokens = self.estimate_tokens(line)
            
            if current_tokens + line_tokens > max_chunk_tokens and current_chunk:
                chunks.append({
                    'content': '\n'.join(current_chunk),
                    'start_line': current_start_line,
                    'end_line': i - 1
                })
                current_chunk = []
                current_start_line = i
                current_tokens = 0
            
            current_chunk.append(line)
            current_tokens += line_tokens
        
        if current_chunk:
            chunks.append({
                'content': '\n'.join(current_chunk),
                'start_line': current_start_line,
                'end_line': len(lines)
            })
        
        return chunks
    
    def get_focus_instructions(self) -> str:
        if self.language == 'ja':
            instructions = "以下の観点でコードをレビューしてください:\n"
            for focus in self.review_focus:
                if focus in self.focus_descriptions:
                    instructions += f"- {self.focus_descriptions[focus]['ja']}\n"
        else:
            instructions = "Review the code focusing on the following aspects:\n"
            for focus in self.review_focus:
                if focus in self.focus_descriptions:
                    instructions += f"- {self.focus_descriptions[focus]['en']}\n"
        
        return instructions
    
    def generate_review_prompt(self, file_path: Path, content: str, chunk_info: Optional[Dict] = None) -> str:
        language = self.supported_extensions.get(file_path.suffix, 'Unknown')
        repo_overview = self.get_repo_overview_context([file_path])

        if self.language == 'ja':
            prompt = f"""あなたは{language}とROS2開発に精通したエキスパートコードレビュアーです。
以下のコードをレビューし、具体的で実行可能なフィードバックを提供してください。

ファイル: {file_path.relative_to(self.code_dir)}
言語: {language}
"""
        else:
            prompt = f"""You are an expert code reviewer specializing in {language} and ROS2 development.
Review the following code and provide specific, actionable feedback.

File: {file_path.relative_to(self.code_dir)}
Language: {language}
"""

        if repo_overview:
            prompt += f"\n{repo_overview}\n"

        if chunk_info:
            if self.language == 'ja':
                prompt += f"行: {chunk_info['start_line']}-{chunk_info['end_line']}\n"
            else:
                prompt += f"Lines: {chunk_info['start_line']}-{chunk_info['end_line']}\n"
        
        prompt += f"""
コード:
```
{content}
```

{self.get_focus_instructions()}

"""
        
        if self.language == 'ja':
            prompt += """以下の正確なJSON形式でのみ応答してください:
{
  "reviews": [
    {"line": <行番号>, "severity": "error|warning|info", "message": "詳細なメッセージ"},
    ...
  ],
  "summary": "全体的な評価の簡潔な要約"
}

行番号を正確に指定し、明確で実行可能なフィードバックを提供してください。
すべてのメッセージは日本語で記述してください。"""
        else:
            prompt += """Respond ONLY with a valid JSON object in this exact format:
{
  "reviews": [
    {"line": <line_number>, "severity": "error|warning|info", "message": "detailed message"},
    ...
  ],
  "summary": "brief overall assessment"
}

Be specific about line numbers and provide clear, actionable feedback."""
        
        return prompt
    
    def call_llm(self, prompt: str) -> Optional[Dict]:
        try:
            system_message = self.system_prompt if self.system_prompt else (
                "あなたは優秀なコードレビュアーです。" if self.language == 'ja' 
                else "You are an expert code reviewer."
            )
            
            headers = {}
            if self.api_key:
                headers['Authorization'] = f'Bearer {self.api_key}'
            
            if self.debug:
                print(f"[DEBUG] API URL: {self.api_url}/chat/completions", file=sys.stderr)
                print(f"[DEBUG] Model: {self.model}", file=sys.stderr)
                print(f"[DEBUG] プロンプト長: {self.estimate_tokens(prompt)} トークン", file=sys.stderr)
            
            response = requests.post(
                f"{self.api_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 2000
                },
                headers=headers,
                timeout=API_TIMEOUT_SECONDS
            )
            
            if self.debug:
                print(f"[DEBUG] LLM応答ステータス: {response.status_code}", file=sys.stderr)
            
            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                
                if self.debug:
                    print(f"[DEBUG] LLM応答プレビュー: {content[:200]}...", file=sys.stderr)
                
                content = content.strip()
                if content.startswith('```json'):
                    content = content[7:]
                if content.startswith('```'):
                    content = content[3:]
                if content.endswith('```'):
                    content = content[:-3]
                content = content.strip()
                
                try:
                    parsed = json.loads(content)
                    if self.debug:
                        print(f"[DEBUG] JSON解析成功", file=sys.stderr)
                    return parsed
                except json.JSONDecodeError as je:
                    print(f"JSON解析エラー: {je}", file=sys.stderr)
                    if self.debug:
                        print(f"[DEBUG] 解析失敗した内容:\n{content[:500]}", file=sys.stderr)
                    return None
            else:
                print(f"エラー: APIがステータス {response.status_code} を返しました: {response.text}", file=sys.stderr)
                return None
                
        except requests.exceptions.Timeout as e:
            print(f"LLMタイムアウトエラー ({API_TIMEOUT_SECONDS}秒): {e}", file=sys.stderr)
            return None
        except json.JSONDecodeError as je:
            print(f"JSON解析エラー: {je}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"LLM呼び出しエラー: {e}", file=sys.stderr)
            if self.debug:
                import traceback
                traceback.print_exc(file=sys.stderr)
            return None
    
    def review_file(self, file_path: Path):
        try:
            content = file_path.read_text(encoding='utf-8')
        except Exception as e:
            print(f"{file_path}の読み込みエラー: {e}", file=sys.stderr)
            return
        
        chunks = self.split_file_content(content, file_path)
        file_reviews = []
        
        for chunk in chunks:
            prompt = self.generate_review_prompt(file_path, chunk['content'], chunk)
            result = self.call_llm(prompt)
            
            if result and 'reviews' in result:
                for review in result['reviews']:
                    if chunk['start_line'] > 1:
                        review['line'] += chunk['start_line'] - 1
                    file_reviews.append(review)
        
        if file_reviews:
            self.results.append({
                'file': str(file_path.relative_to(self.code_dir)),
                'reviews': file_reviews
            })
    
    def review_batch(self, file_paths: List[Path]):
        if len(file_paths) == 1:
            self.review_file(file_paths[0])
            return

        repo_overview = self.get_repo_overview_context(file_paths)

        if self.language == 'ja':
            combined_content = f"複数の関連ファイルをレビューします（{len(file_paths)}ファイル）:\n\n"
        else:
            combined_content = f"Reviewing {len(file_paths)} related files together:\n\n"
        
        file_contents = []
        for file_path in file_paths:
            try:
                content = file_path.read_text(encoding='utf-8')
                relative_path = file_path.relative_to(self.code_dir)
                file_contents.append({
                    'path': file_path,
                    'relative_path': relative_path,
                    'content': content
                })
                combined_content += f"--- File: {relative_path} ---\n{content}\n\n"
            except Exception as e:
                print(f"{file_path}の読み込みエラー: {e}", file=sys.stderr)
        
        if not file_contents:
            return
        
        language = self.supported_extensions.get(file_paths[0].suffix, 'Unknown')
        focus_instructions = self.get_focus_instructions()
        
        if self.language == 'ja':
            prompt = f"""あなたは{language}とROS2開発に精通したエキスパートコードレビュアーです。
以下の複数の関連ファイルをまとめてレビューし、ファイル間の依存関係や相互作用も考慮してください。

{focus_instructions}

{repo_overview}

{combined_content}

各ファイルごとに問題点を JSON 形式で返してください:
{{"file": "相対パス", "reviews": [{{"line": 行番号, "severity": "error/warning/info", "message": "指摘内容"}}]}}

複数ファイルがある場合は配列で返してください: [{{"file": "...", "reviews": [...]}}, ...]
"""
        else:
            prompt = f"""You are an expert code reviewer specializing in {language} and ROS2 development.
Review the following related files together, considering cross-file dependencies and interactions.

{focus_instructions}

{repo_overview}

{combined_content}

Return issues in JSON format for each file:
{{"file": "relative_path", "reviews": [{{"line": line_number, "severity": "error/warning/info", "message": "issue description"}}]}}

For multiple files, return an array: [{{"file": "...", "reviews": [...]}}, ...]
"""
        
        result = self.call_llm(prompt)
        
        if result:
            if isinstance(result, dict) and 'file' in result:
                if result.get('reviews'):
                    self.results.append(result)
            elif isinstance(result, list):
                for file_result in result:
                    if file_result.get('reviews'):
                        self.results.append(file_result)
            elif isinstance(result, dict) and 'reviews' in result:
                if result.get('reviews'):
                    self.results.append({
                        'file': str(file_paths[0].relative_to(self.code_dir)),
                        'reviews': result['reviews']
                    })
    
    def run(self):
        files = self.find_files()
        total_files = len(files)
        print(f"{total_files}個のファイルが見つかりました")

        self.build_repo_overview(files)

        batches = self.batch_files(files)
        total_batches = len(batches)
        
        if self.debug:
            print(f"[DEBUG] {total_batches}個のバッチを作成しました", file=sys.stderr)
            for i, batch in enumerate(batches, 1):
                batch_tokens = sum(self.estimate_tokens(f.read_text(encoding='utf-8', errors='ignore')) for f in batch)
                print(f"[DEBUG] バッチ {i}: {len(batch)}ファイル, 約{batch_tokens}トークン", file=sys.stderr)
        
        processed_files = 0
        for batch_idx, batch in enumerate(batches, 1):
            batch_progress = (batch_idx / total_batches) * 100
            file_progress = (processed_files / total_files) * 100
            
            if len(batch) == 1:
                print(f"\n[{processed_files + 1}/{total_files} ({file_progress:.1f}%)] レビュー中: {batch[0].relative_to(self.code_dir)}")
            else:
                print(f"\n[バッチ {batch_idx}/{total_batches} ({batch_progress:.1f}%)] {len(batch)}ファイルをまとめてレビュー中:")
                for f in batch:
                    print(f"  - {f.relative_to(self.code_dir)}")
            
            self.review_batch(batch)
            processed_files += len(batch)
        
        output = {
            'total_files': total_files,
            'files_with_issues': len(self.results),
            'results': self.results
        }
        
        with open(self.output_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        
        print(f"\n✓ レビュー完了。結果を保存しました: {self.output_path}")
        print(f"  レビューしたファイル数: {total_files}")
        print(f"  バッチ数: {total_batches}")
        print(f"  問題が見つかったファイル数: {len(self.results)}")


def main():
    parser = argparse.ArgumentParser(
        description='LLMベースのコードレビューツール（ROS2プロジェクト向け）'
    )
    parser.add_argument(
        '--api-url',
        default='http://192.168.50.136:1234/v1',
        help='LLM APIのベースURL (デフォルト: http://192.168.50.136:1234/v1)'
    )
    parser.add_argument(
        '--model',
        default='qwen/qwen3-coder-30b',
        help='モデル名 (デフォルト: qwen/qwen3-coder-30b)'
    )
    parser.add_argument(
        '--context-length',
        type=int,
        default=262144,
        help='モデルのコンテキスト長（トークン数） (デフォルト: 262144)'
    )
    parser.add_argument(
        '--code-dir',
        default='/code',
        help='レビュー対象のコードディレクトリ (デフォルト: /code)'
    )
    parser.add_argument(
        '--output',
        default='/code/review-results.json',
        help='出力ファイルパス (デフォルト: /code/review-results.json)'
    )
    parser.add_argument(
        '--exclude',
        action='append',
        default=[],
        help='除外パターン（複数指定可能）'
    )
    parser.add_argument(
        '--review-focus',
        action='append',
        choices=['security', 'performance', 'pep8', 'ros2', 'bugs', 'maintainability', 'general'],
        default=[],
        help='レビューの焦点（複数指定可能）: security, performance, pep8, ros2, bugs, maintainability, general'
    )
    parser.add_argument(
        '--language',
        choices=['ja', 'en'],
        default='ja',
        help='出力言語 (デフォルト: ja)'
    )
    parser.add_argument(
        '--system-prompt',
        help='カスタムシステムプロンプト'
    )
    parser.add_argument(
        '--prompt-file',
        help='システムプロンプトを含むファイルのパス'
    )
    parser.add_argument(
        '--api-key',
        help='API認証キー（OpenWebUI等で必要な場合）'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='デバッグモードを有効化（詳細なログ出力）'
    )
    parser.add_argument(
        '--batch-threshold',
        type=int,
        default=10000,
        help='ファイルをバッチ処理する閾値（トークン数）。この値より小さいファイルはまとめてレビューされます (デフォルト: 10000)'
    )
    parser.add_argument(
        '--repo-overview-tokens',
        type=int,
        default=0,
        help='各レビューリクエストに含めるリポジトリ概要の最大トークン数。0を指定すると無効化 (デフォルト: 0)'
    )
    parser.add_argument(
        '--repo-overview-lines',
        type=int,
        default=20,
        help='リポジトリ概要の各ファイルで抜粋する最大行数 (デフォルト: 20)'
    )
    
    args = parser.parse_args()
    
    if not args.exclude:
        args.exclude = [
            '*.pyc',
            '*.pyo',
            '__pycache__/*',
            '.svn/*',
            '.git/*',
            'build/*',
            'install/*',
            'log/*',
        ]
    
    if not args.review_focus:
        args.review_focus = ['bugs', 'performance', 'maintainability']
    
    system_prompt = args.system_prompt
    if args.prompt_file:
        try:
            with open(args.prompt_file, 'r', encoding='utf-8') as f:
                system_prompt = f.read().strip()
        except Exception as e:
            print(f"プロンプトファイルの読み込みエラー: {e}", file=sys.stderr)
            sys.exit(1)
    
    reviewer = CodeReviewer(
        api_url=args.api_url,
        model=args.model,
        context_length=args.context_length,
        output_path=args.output,
        exclude_patterns=args.exclude,
        code_dir=args.code_dir,
        review_focus=args.review_focus,
        language=args.language,
        system_prompt=system_prompt,
        api_key=args.api_key,
        debug=args.debug,
        batch_threshold=args.batch_threshold,
        repo_overview_tokens=args.repo_overview_tokens,
        repo_overview_lines=args.repo_overview_lines
    )
    
    reviewer.run()


if __name__ == '__main__':
    main()
