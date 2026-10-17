claude.mdファイルにGemini CLI連携ガイドを追加しました。

AGENTS.md
必ず日本語で回答してください。
This file provides guidance to Codex (claude.ai/code) when working with code in this repository.

Commands
Installation and Setup
Bash

pip install -r requirements.txt
Running the Bot
Bash

python main.py
Environment Variables Required
DISCORD_BOT_TOKEN: Discord Bot token

GEMINI_API_KEY: Google Gemini API key

Gemini CLI 連携ガイド
目的
ユーザーが 「Geminiと相談しながら進めて」 （または同義語）と指示した場合、Codex は以降のタスクを Gemini CLI と協調しながら進める。
Gemini から得た回答はそのまま提示し、Codex 自身の解説・統合も付け加えることで、両エージェントの知見を融合する。

トリガー
正規表現: /Gemini.*相談しながら/

例:

「Geminiと相談しながら進めて」

「この件、Geminiと話しつつやりましょう」

基本フロー
PROMPT 生成
Codex はユーザーの要件を 1 つのテキストにまとめ、環境変数 $PROMPT に格納する。

Gemini CLI 呼び出し

Bash

gemini <<EOF$PROMPT
EOF

---

## Architecture Overview

This is a multi-functional Discord bot that integrates Google Gemini AI with various features. The bot uses a modular architecture with separate cogs for different functionalities.

### Core Structure
- **main.py**: Entry point that initializes the bot, loads cogs, and runs a Flask server for health checks
- **cogs/**: Contains modular bot functionality organized as Discord.py cogs
 - **gemini_chat.py**: Main AI chat functionality with conversation history, reference mode, and English learning mode
 - **auto_delete.py**: Message auto-deletion functionality
 - **voice_control.py**: Voice channel mute/unmute controls

### Key Features
- **Conversation History**: Maintains last 100 messages for context in AI responses
- **Reference Mode**: Includes file contents in AI prompts for contextualized responses
- **English Learning Mode**: Three conversation styles (friendly/business/slang) with Japanese feedback
- **Always Reply Mode**: Automatic responses to all messages in configured channels
- **Slash Commands**: All functionality accessible through Discord slash commands

### Configuration Files
- **always_reply_channels.json**: Channels where bot always responds
- **reference_mode_config.json**: Reference mode settings and file paths
- **english_mode_config.json**: English learning mode configurations
- **voice_memo_config.json**: Voice memo transcription settings

### Security Considerations
- API keys are stored in environment variables, not in code
- Reference files should not contain sensitive information as they're included in AI responses

### Threading and Async
- Main bot runs in asyncio event loop
- Flask server runs in a separate thread for health checks

### Dependencies
- discord.py: Discord API integration
- google-generativeai: Gemini AI integration
- flask: Health check server
- python-dotenv: Environment variable management
- google-cloud-firestore: Conversation history and settings

## AUTOCOOK - 機能要件定義システム

### 概要
新機能開発時に要件定義を確実に参照するためのシステムです。

### 使用方法
1. 新機能の要件定義は `autocook` ファイルに記載する
2. 機能開発時に「autocookファイルを読み込んで」と指示されたら必ず読み込む
3. 要件定義に従って正確に実装する
4. 実装中も定期的に要件定義を確認する

### 重要事項
- **autocookファイル読み込み指示があった場合は必須で読み込むこと**
- 要件定義から逸脱しないよう細心の注意を払うこと
- 実装完了後は該当機能にステータスを追記すること

### ファイル場所
- `/autocook` - メインの要件定義ファイル