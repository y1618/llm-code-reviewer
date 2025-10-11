#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
import requests
import fnmatch


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
        api_key: Optional[str] = None
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
        self.results = []
        
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
                timeout=120
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result['choices'][0]['message']['content']
                
                content = content.strip()
                if content.startswith('```json'):
                    content = content[7:]
                if content.startswith('```'):
                    content = content[3:]
                if content.endswith('```'):
                    content = content[:-3]
                content = content.strip()
                
                return json.loads(content)
            else:
                print(f"エラー: APIがステータス {response.status_code} を返しました: {response.text}", file=sys.stderr)
                return None
                
        except Exception as e:
            print(f"LLM呼び出しエラー: {e}", file=sys.stderr)
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
    
    def run(self):
        files = self.find_files()
        total_files = len(files)
        print(f"{total_files}個のファイルが見つかりました")
        
        for idx, file_path in enumerate(files, 1):
            progress = (idx / total_files) * 100
            print(f"\n[{idx}/{total_files} ({progress:.1f}%)] レビュー中: {file_path.relative_to(self.code_dir)}")
            self.review_file(file_path)
        
        output = {
            'total_files': total_files,
            'files_with_issues': len(self.results),
            'results': self.results
        }
        
        with open(self.output_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        
        print(f"\n✓ レビュー完了。結果を保存しました: {self.output_path}")
        print(f"  レビューしたファイル数: {total_files}")
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
        api_key=args.api_key
    )
    
    reviewer.run()


if __name__ == '__main__':
    main()
