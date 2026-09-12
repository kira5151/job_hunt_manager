import sqlite3
from datetime import datetime, date
import smtplib
import os

from email.message import EmailMessage
from dotenv import load_dotenv

import psycopg
from psycopg.rows import dict_row


# =========================================
# 基本設定
# =========================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

ENV_PATH = os.path.join(
    BASE_DIR,
    ".env"
)

DB_PATH = os.path.join(
    BASE_DIR,
    "jobhunt.db"
)

LOG_PATH = os.path.join(
    BASE_DIR,
    "reminder_log.txt"
)


load_dotenv(
    ENV_PATH
)


DATABASE_URL = os.getenv(
    "DATABASE_URL"
)


# =========================================
# PostgreSQLかどうか
# =========================================

def use_postgres():

    return bool(
        DATABASE_URL
    )


# =========================================
# DB接続
# =========================================

def get_db():

    if use_postgres():

        return psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row
        )


    conn = sqlite3.connect(
        DB_PATH
    )

    conn.row_factory = sqlite3.Row

    return conn


# =========================================
# ログ
# =========================================

def write_log(message):

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    text = (
        f"[{now}] {message}\n"
    )

    print(
        text,
        end=""
    )


    # Renderではファイルログに依存せず
    # コンソールログを基本とする
    if not use_postgres():

        with open(
            LOG_PATH,
            "a",
            encoding="utf-8"
        ) as file:

            file.write(
                text
            )


# =========================================
# 締切日時読み取り
# =========================================

def parse_deadline(value):

    if not value:
        return None


    formats = [
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d"
    ]


    for fmt in formats:

        try:

            parsed = datetime.strptime(
                value,
                fmt
            )


            if fmt == "%Y-%m-%d":

                parsed = parsed.replace(
                    hour=23,
                    minute=59
                )


            return parsed


        except ValueError:

            continue


    return None


# =========================================
# 表示用日時
# =========================================

def format_deadline(value):

    parsed = parse_deadline(
        value
    )

    if not parsed:

        return ""

    return parsed.strftime(
        "%Y-%m-%d %H:%M"
    )


# =========================================
# 残り時間
# =========================================

def get_remaining_info(value):

    target = parse_deadline(
        value
    )

    if not target:

        return None


    now = datetime.now()

    difference = (
        target - now
    )


    total_seconds = int(
        difference.total_seconds()
    )


    if total_seconds < 0:

        return {
            "expired": True,
            "seconds": total_seconds,
            "text": "期限切れ"
        }


    if total_seconds < 60:

        text = "あと1分未満"


    elif total_seconds < 3600:

        minutes = (
            total_seconds // 60
        )

        text = (
            f"あと{minutes}分"
        )


    elif total_seconds < 86400:

        hours = (
            total_seconds // 3600
        )

        minutes = (
            total_seconds % 3600
        ) // 60


        if minutes > 0:

            text = (
                f"あと{hours}時間"
                f"{minutes}分"
            )

        else:

            text = (
                f"あと{hours}時間"
            )


    else:

        days = (
            total_seconds // 86400
        )

        hours = (
            total_seconds % 86400
        ) // 3600


        if hours > 0:

            text = (
                f"あと{days}日"
                f"{hours}時間"
            )

        else:

            text = (
                f"あと{days}日"
            )


    return {
        "expired": False,
        "seconds": total_seconds,
        "text": text
    }


# =========================================
# インターンまでの日数
# =========================================

def intern_days_left(value):

    if not value:

        return None


    try:

        intern_date = datetime.strptime(
            value,
            "%Y-%m-%d"
        ).date()


        today = date.today()


        return (
            intern_date - today
        ).days


    except ValueError:

        return None


# =========================================
# リマインド作成
# =========================================

def make_reminder_list(companies):

    reminder_list = []

    seventy_two_hours = (
        72 * 60 * 60
    )


    for company in companies:


        # =================================
        # ES
        # =================================

        es_info = get_remaining_info(
            company["es_deadline"]
        )


        if (
            company["es_status"] != "提出済"
            and es_info is not None
            and not es_info["expired"]
            and es_info["seconds"] <= seventy_two_hours
        ):

            reminder_list.append({

                "company_name":
                    company["company_name"],

                "type":
                    "ES",

                "date":
                    format_deadline(
                        company["es_deadline"]
                    ),

                "seconds":
                    es_info["seconds"],

                "remaining_text":
                    es_info["text"]

            })


        # =================================
        # SPI
        # =================================

        spi_info = get_remaining_info(
            company["spi_deadline"]
        )


        if (
            company["spi_status"] != "受験済"
            and spi_info is not None
            and not spi_info["expired"]
            and spi_info["seconds"] <= seventy_two_hours
        ):

            reminder_list.append({

                "company_name":
                    company["company_name"],

                "type":
                    "SPI",

                "date":
                    format_deadline(
                        company["spi_deadline"]
                    ),

                "seconds":
                    spi_info["seconds"],

                "remaining_text":
                    spi_info["text"]

            })


        # =================================
        # インターン
        # 翌日のみ
        # =================================

        intern_days = intern_days_left(
            company["intern_date"]
        )


        if intern_days == 1:

            reminder_list.append({

                "company_name":
                    company["company_name"],

                "type":
                    "インターン",

                "date":
                    company["intern_date"],

                "seconds":
                    86400,

                "remaining_text":
                    "明日がインターン開催日です"

            })


    reminder_list.sort(
        key=lambda item: item["seconds"]
    )


    return reminder_list


# =========================================
# メール送信
# =========================================

def send_email(reminder_list):

    gmail_address = os.getenv(
        "GMAIL_ADDRESS"
    )

    gmail_app_password = os.getenv(
        "GMAIL_APP_PASSWORD"
    )

    reminder_email = os.getenv(
        "REMINDER_EMAIL"
    )


    if gmail_app_password:

        gmail_app_password = (
            gmail_app_password
            .replace(" ", "")
        )


    if not gmail_address:

        raise ValueError(
            "GMAIL_ADDRESSが設定されていません。"
        )


    if not gmail_app_password:

        raise ValueError(
            "GMAIL_APP_PASSWORDが設定されていません。"
        )


    if not reminder_email:

        raise ValueError(
            "REMINDER_EMAILが設定されていません。"
        )


    body = ""

    body += (
        "就活管理アプリから"
        "今日のリマインドです。\n\n"
    )

    body += (
        "以下の予定を確認してください。\n\n"
    )


    for item in reminder_list:

        body += (
            "------------------------------\n"
        )

        body += (
            f"企業名："
            f"{item['company_name']}\n"
        )

        body += (
            f"項目："
            f"{item['type']}\n"
        )

        body += (
            f"日時："
            f"{item['date']}\n"
        )

        body += (
            f"{item['remaining_text']}\n"
        )


    body += (
        "------------------------------\n"
    )

    body += (
        "\n就活管理アプリで"
        "状況を確認しましょう。"
    )


    message = EmailMessage()

    message["Subject"] = (
        "【就活管理】今日のリマインド"
    )

    message["From"] = (
        gmail_address
    )

    message["To"] = (
        reminder_email
    )

    message.set_content(
        body
    )


    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465
    ) as server:

        server.login(
            gmail_address,
            gmail_app_password
        )

        server.send_message(
            message
        )


# =========================================
# メイン
# =========================================

def main():

    write_log(
        "リマインド確認を開始しました。"
    )


    try:

        conn = get_db()


        companies = conn.execute(
            """
            SELECT *
            FROM companies
            """
        ).fetchall()


        conn.close()


        reminder_list = (
            make_reminder_list(
                companies
            )
        )


        if len(reminder_list) == 0:

            write_log(
                "本日のリマインド対象はありません。"
            )

            return


        send_email(
            reminder_list
        )


        write_log(
            f"リマインドメールを送信しました。 "
            f"対象：{len(reminder_list)}件"
        )


    except Exception as error:

        write_log(
            f"エラー：{error}"
        )


if __name__ == "__main__":

    main()