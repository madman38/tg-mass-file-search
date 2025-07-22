# tg-mass-file-search

A Flask-based web app that allows users to search for documents (PDFs, etc.) in Telegram channels containing keywords using the Telegram API.

> [!CAUTION]
> This app has not been tested for a production environment and is designed for personal, local use.

## Features

- Search for documents in public Telegram channels containing certain keywords
- Download documents directly from Telegram via links

## Prerequisites

- Python 3.7+
- Telegram API credentials (API_ID and API_HASH)
- Valid Telegram account

## Getting Telegram API Credentials

1. Visit https://my.telegram.org/apps
2. Create a new application
3. Note down your API_ID and API_HASH

## Installation

1. Clone the repository:
```bash
git clone https://github.com/madman38/tg-mass-file-search.git
cd tg-mass-file-search
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Edit the `.env` file with your Telegram credentials:
```bash
# Telegram API configuration
API_ID=your_api_id
API_HASH=your_api_hash
SESSION_NAME=session_name
PHONE_NUMBER=phone_number # example: +901234567890
```

## Setup Telegram Session

Run the authentication script:
```bash
python auth.py
```

Follow the prompts to complete Telegram authentication. This will create a session file.

## Configuration

Environment variables in `.env`:
```bash
# Search configuration
CHANNEL_SEARCH_LIMIT_PER_KEYWORD=10
MESSAGES_SEARCH_LIMIT_PER_CHANNEL=30

# Flask configuration
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_ENV=development
```

## Running the Application

Start the Flask server:
```bash
python app.py
```

Access the web interface at: `http://localhost:5000`

## Usage

1. Enter your search query in the web interface
2. View matching documents from Telegram channels

