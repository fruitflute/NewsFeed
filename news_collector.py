import os
import feedparser
import requests
from bs4 import BeautifulSoup
import google.generativeai as genai
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from datetime import datetime
import time
import re

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
    """Gemini APIを使ってテキストを要約する（リトライとレート制限対応付き）"""
    if not text:
        return "記事の本文を取得できませんでした。"

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash') # 無料利用枠でより多くのリクエストを処理できる可能性があるモデルに変更
    prompt = f"以下のニュース記事を日本語で{MAX_SUMMARY_LENGTH}字程度の箇条書きで要約してください。重要なポイントを3つに絞ってください。\n\n---\n{text[:8000]}"

    retries = 3
    backoff_factor = 5  # 秒

    for i in range(retries):
        try:
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.7, # 創造性を少し持たせる
                ),
                safety_settings=[
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                ]
            )
            # response.text の前に response.parts が存在するか確認
            if response.parts:
                return response.text
            else:
                # レスポンスが空の場合のハンドリング
                try:
                    finish_reason = response.prompt_feedback.block_reason
                except Exception:
                    finish_reason = "不明"
                error_message = f"Geminiから有効な応答がありませんでした。理由: {finish_reason}"
                print(error_message)
                if i < retries - 1:
                    time.sleep(backoff_factor * (2 ** i))
                    continue
                else:
                    return f"要約の生成に失敗しました: {error_message}"

        except Exception as e:
            error_message = f"Gemini APIエラー: {e}"
            print(error_message)
            # 429 (ResourceExhausted) エラーの場合、待機時間を長くする
            if "429" in str(e):
                 # エラーメッセージからretry_delayを抽出する試み
                try:
                    match = re.search(r"retry_delay {\s*seconds: (\d+)\s*}", str(e))
                    if match:
                        wait_time = int(match.group(1)) + 1 # 1秒追加
                        print(f"レート制限超過。{wait_time}秒待機します...")
                        time.sleep(wait_time)
                    else:
                        # 固定時間待機
                        wait_time = 60
                        print(f"レート制限超過。{wait_time}秒待機します...")
                        time.sleep(wait_time)
                except Exception:
                    wait_time = 60
                    print(f"レート制限超過。{wait_time}秒待機します...")
                    time.sleep(wait_time)

            # その他のエラーの場合は通常のバックオフ
            elif i < retries - 1:
                wait_time = backoff_factor * (2 ** i)
                print(f"エラーのため{wait_time}秒待機して再試行します...")
                time.sleep(wait_time)
            else:
                return f"要約の生成に失敗しました: {error_message}"

    return "要約の生成に失敗しました（リトライ上限超過）。"


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
            
            summary = summarize_text_with_gemini(article_text)
            
            all_summaries.append({
                "source": name,
                "title": entry.title,
                "link": entry.link,
                "summary": summary
            })
            # 1分あたりのリクエスト数を考慮し、各記事の処理後に十分な待機時間を設ける
            print("次の記事の処理まで31秒待機します...")
            time.sleep(31)


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
