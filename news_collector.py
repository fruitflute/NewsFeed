import os
import feedparser
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from datetime import datetime
import time

# --- 設定 ---
# RSSフィードのURL
FEEDS = {
    "Hacker News": "https://news.ycombinator.com/rss",
    "TechCrunch": "https://techcrunch.com/feed/",
    "The Verge": "https://www.theverge.com/rss/index.xml",
}
# 取得する記事の数
MAX_ENTRIES_PER_FEED = 3
# 要約の最大文字数
MAX_SUMMARY_LENGTH = 300 # Geminiへの指示で調整

# --- APIキーとメールアドレスを環境変数から取得 ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
TO_EMAIL = os.getenv("TO_EMAIL_ADDRESS")
FROM_EMAIL = os.getenv("FROM_EMAIL_ADDRESS")

def get_article_text(url):
    """URLから記事の本文を抽出する"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "lxml")
        
        # 主要なコンテンツが含まれていそうなタグを優先的に探す
        main_content = soup.find("article") or soup.find("main") or soup.find("body")
        
        if main_content:
            # 不要な要素（ヘッダー、フッター、ナビゲーション、広告など）を削除
            for tag in main_content.find_all(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form']):
                tag.decompose()
            
            paragraphs = main_content.find_all("p")
            text = " ".join([p.get_text() for p in paragraphs])
            return text.strip()
        return ""
    except requests.RequestException as e:
        print(f"記事取得エラー: {url}, 理由: {e}")
        return ""
    except Exception as e:
        print(f"予期せぬエラー（記事取得中）: {url}, 理由: {e}")
        return ""

def summarize_text_with_gemini(text):
    """Gemini APIを使ってテキストを要約する"""
    if not text:
        return "記事の本文を取得できませんでした。"
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-2.5-pro')
        prompt = f"以下のニュース記事を日本語で{MAX_SUMMARY_LENGTH}字程度の箇条書きで要約してください。重要なポイントを3つに絞ってください。\n\n---\n{text[:8000]}" # 長すぎるテキストは切り詰める
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Gemini APIエラー: {e}")
        return "要約の生成中にエラーが発生しました。"

def build_html_content(summaries):
    """要約リストからHTMLメールの本文を作成する"""
    today = datetime.now().strftime('%Y年%m月%d日')
    html = f"<html><body><h1>{today}のニュースサマリー</h1>"
    
    for item in summaries:
        html += f"<h2><a href='{item['link']}'>{item['title']}</a></h2>"
        html += f"<h4>{item['source']}</h4>"
        summary_with_br = item['summary'].replace('\n', '<br>')
        html += f"<p>{summary_with_br}</p>"
        html += "<hr>"
        
    html += "</body></html>"
    return html

def send_email(html_content):
    """SendGridを使ってHTMLメールを送信する"""
    if not all([SENDGRID_API_KEY, TO_EMAIL, FROM_EMAIL]):
        print("SendGridの設定が不完全です。環境変数を確認してください。")
        return

    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=TO_EMAIL,
        subject=f"今日のニュースサマリー ({datetime.now().strftime('%Y-%m-%d')})",
        html_content=html_content)
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(f"メール送信成功: ステータスコード {response.status_code}")
    except Exception as e:
        print(f"SendGrid APIエラー: {e}")

def main():
    """メインの処理フロー"""
    print("ニュース要約プロセスを開始します...")
    
    if not GEMINI_API_KEY:
        print("エラー: GEMINI_API_KEYが設定されていません。")
        return

    all_summaries = []
    for name, url in FEEDS.items():
        print(f"--- {name}から記事を取得中 ---")
        feed = feedparser.parse(url)
        
        for entry in feed.entries[:MAX_ENTRIES_PER_FEED]:
            print(f"処理中の記事: {entry.title}")
            
            article_text = get_article_text(entry.link)
            time.sleep(1) # サーバーへの負荷を軽減
            
            summary = summarize_text_with_gemini(article_text)
            
            all_summaries.append({
                "source": name,
                "title": entry.title,
                "link": entry.link,
                "summary": summary
            })
            time.sleep(1) # APIへの負荷を軽減

    if all_summaries:
        print("HTMLメールを作成中...")
        html = build_html_content(all_summaries)
        
        print("メールを送信中...")
        send_email(html)
    else:
        print("要約する記事がありませんでした。")
        
    print("ニュース要約プロセスが完了しました。")

if __name__ == "__main__":
    main()