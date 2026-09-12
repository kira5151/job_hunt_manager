from flask import Flask, render_template, request, redirect, flash
import sqlite3
from datetime import datetime, date, timedelta
import smtplib
import os

from email.message import EmailMessage
from dotenv import load_dotenv


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

load_dotenv(
    ENV_PATH
)


app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "job-hunt-manager-secret-key"
)


# =========================================
# データベース接続
# =========================================

def get_db():

    conn = sqlite3.connect(
        DB_PATH
    )

    conn.row_factory = sqlite3.Row

    return conn


# =========================================
# データベース初期化
# =========================================

def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            application_type TEXT,
            intern_date TEXT,
            es_deadline TEXT,
            es_status TEXT,
            spi_deadline TEXT,
            spi_status TEXT,
            selection_status TEXT,
            memo TEXT
        )
    """)

    conn.commit()
    conn.close()


# =========================================
# 締切日時を読み取る
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

            # 古い「日付だけ」のデータは
            # その日の23:59として扱う
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
# datetime-local用に変換
# =========================================

def to_datetime_local(value):

    parsed = parse_deadline(
        value
    )

    if not parsed:
        return ""

    return parsed.strftime(
        "%Y-%m-%dT%H:%M"
    )


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
# 締切までの残り時間
# =========================================

def get_remaining_info(value):

    target = parse_deadline(
        value
    )

    if not target:
        return None

    now = datetime.now()

    difference = target - now

    total_seconds = int(
        difference.total_seconds()
    )

    if total_seconds < 0:

        return {
            "expired": True,
            "seconds": total_seconds,
            "text": "期限切れ"
        }


    # 1分未満
    if total_seconds < 60:

        text = "あと1分未満"


    # 1時間未満
    elif total_seconds < 3600:

        minutes = (
            total_seconds // 60
        )

        text = (
            f"あと{minutes}分"
        )


    # 1日未満
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


    # 1日以上
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
# リマインド一覧作成
# =========================================

def make_reminder_list(companies):

    reminder_list = []

    seventy_two_hours = (
        72 * 60 * 60
    )


    for company in companies:


        # =================================
        # ES
        # 締切まで72時間以内
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
                "company_name": company["company_name"],
                "type": "ES",
                "date": format_deadline(
                    company["es_deadline"]
                ),
                "seconds": es_info["seconds"],
                "remaining_text": es_info["text"]
            })


        # =================================
        # SPI
        # 締切まで72時間以内
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
                "company_name": company["company_name"],
                "type": "SPI",
                "date": format_deadline(
                    company["spi_deadline"]
                ),
                "seconds": spi_info["seconds"],
                "remaining_text": spi_info["text"]
            })


        # =================================
        # インターン
        # 今日・明日
        # =================================

        intern_days = intern_days_left(
            company["intern_date"]
        )

        if (
            intern_days is not None
            and 0 <= intern_days <= 1
        ):

            if intern_days == 0:

                remaining_text = "今日"

            else:

                remaining_text = "明日"


            reminder_list.append({
                "company_name": company["company_name"],
                "type": "インターン",
                "date": company["intern_date"],
                "seconds": intern_days * 86400,
                "remaining_text": remaining_text
            })


    reminder_list.sort(
        key=lambda item: item["seconds"]
    )

    return reminder_list


# =========================================
# メール送信
# =========================================

def send_reminder_email(reminder_list):

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

        return (
            False,
            "GMAIL_ADDRESSが.envに設定されていません。"
        )


    if not gmail_app_password:

        return (
            False,
            "GMAIL_APP_PASSWORDが.envに設定されていません。"
        )


    if not reminder_email:

        return (
            False,
            "REMINDER_EMAILが.envに設定されていません。"
        )


    if len(reminder_list) == 0:

        return (
            False,
            "現在リマインド対象の予定はありません。"
        )


    # =====================================
    # メール本文
    # =====================================

    body = ""

    body += (
        "就活管理アプリからの"
        "リマインドです。\n\n"
    )

    body += (
        "以下の予定が近づいています。\n\n"
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
        "\n忘れずに確認しましょう。"
    )


    # =====================================
    # メール作成
    # =====================================

    message = EmailMessage()

    message["Subject"] = (
        "【就活管理】締切リマインド"
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


    # =====================================
    # Gmail送信
    # =====================================

    try:

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


        return (
            True,
            "リマインドメールを送信しました。"
        )


    except Exception as error:

        print(
            "メール送信エラー：",
            error
        )

        return (
            False,
            "メール送信に失敗しました。ターミナルのエラーを確認してください。"
        )


# =========================================
# トップページ
# =========================================

@app.route("/")
def home():

    keyword = request.args.get(
        "keyword",
        ""
    )

    application_type = request.args.get(
        "application_type",
        ""
    )

    es_status = request.args.get(
        "es_status",
        ""
    )

    spi_status = request.args.get(
        "spi_status",
        ""
    )

    selection_status = request.args.get(
        "selection_status",
        ""
    )


    conn = get_db()


    query = """
        SELECT *
        FROM companies
        WHERE 1=1
    """

    params = []


    if keyword:

        query += (
            " AND company_name LIKE ?"
        )

        params.append(
            f"%{keyword}%"
        )


    if application_type:

        query += (
            " AND application_type = ?"
        )

        params.append(
            application_type
        )


    if es_status:

        query += (
            " AND es_status = ?"
        )

        params.append(
            es_status
        )


    if spi_status:

        query += (
            " AND spi_status = ?"
        )

        params.append(
            spi_status
        )


    if selection_status:

        query += (
            " AND selection_status = ?"
        )

        params.append(
            selection_status
        )


    query += (
        " ORDER BY id DESC"
    )


    companies = conn.execute(
        query,
        params
    ).fetchall()


    all_companies = conn.execute(
        """
        SELECT *
        FROM companies
        """
    ).fetchall()


    conn.close()


    # =====================================
    # ダッシュボード
    # =====================================

    total_count = len(
        all_companies
    )

    es_not_submitted_count = 0

    spi_not_taken_count = 0

    selection_count = 0


    for company in all_companies:

        if company["es_status"] == "未提出":

            es_not_submitted_count += 1


        if company["spi_status"] == "未受験":

            spi_not_taken_count += 1


        if company["selection_status"] == "選考中":

            selection_count += 1


    # =====================================
    # 表示用データ
    # =====================================

    company_list = []


    for company in companies:

        company_data = dict(
            company
        )


        company_data["es_display"] = (
            format_deadline(
                company["es_deadline"]
            )
        )


        company_data["spi_display"] = (
            format_deadline(
                company["spi_deadline"]
            )
        )


        company_data["es_remaining"] = (
            get_remaining_info(
                company["es_deadline"]
            )
        )


        company_data["spi_remaining"] = (
            get_remaining_info(
                company["spi_deadline"]
            )
        )


        company_data["intern_days"] = (
            intern_days_left(
                company["intern_date"]
            )
        )


        company_list.append(
            company_data
        )


    reminder_list = make_reminder_list(
        all_companies
    )


    return render_template(
        "index.html",

        companies=company_list,

        urgent_list=reminder_list,

        total_count=total_count,

        es_not_submitted_count=es_not_submitted_count,

        spi_not_taken_count=spi_not_taken_count,

        selection_count=selection_count,

        keyword=keyword,

        application_type=application_type,

        es_status=es_status,

        spi_status=spi_status,

        selection_status=selection_status
    )


# =========================================
# メール送信
# =========================================

@app.route(
    "/send_reminder",
    methods=["POST"]
)
def send_reminder():

    conn = get_db()

    companies = conn.execute(
        """
        SELECT *
        FROM companies
        """
    ).fetchall()

    conn.close()


    reminder_list = make_reminder_list(
        companies
    )


    success, message = (
        send_reminder_email(
            reminder_list
        )
    )


    if success:

        flash(
            message,
            "success"
        )

    else:

        flash(
            message,
            "error"
        )


    return redirect("/")


# =========================================
# 企業追加
# =========================================

@app.route(
    "/add",
    methods=["GET", "POST"]
)
def add_company():

    if request.method == "POST":

        company_name = request.form[
            "company_name"
        ]

        application_type = request.form[
            "application_type"
        ]

        intern_date = request.form[
            "intern_date"
        ]

        es_deadline = request.form[
            "es_deadline"
        ]

        es_status = request.form[
            "es_status"
        ]

        spi_deadline = request.form[
            "spi_deadline"
        ]

        spi_status = request.form[
            "spi_status"
        ]

        selection_status = request.form[
            "selection_status"
        ]

        memo = request.form[
            "memo"
        ]


        conn = get_db()


        conn.execute("""
            INSERT INTO companies (
                company_name,
                application_type,
                intern_date,
                es_deadline,
                es_status,
                spi_deadline,
                spi_status,
                selection_status,
                memo
            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (

            company_name,

            application_type,

            intern_date,

            es_deadline,

            es_status,

            spi_deadline,

            spi_status,

            selection_status,

            memo

        ))


        conn.commit()

        conn.close()


        return redirect("/")


    return render_template(
        "add.html"
    )


# =========================================
# 企業編集
# =========================================

@app.route(
    "/edit/<int:id>",
    methods=["GET", "POST"]
)
def edit_company(id):

    conn = get_db()


    if request.method == "POST":

        company_name = request.form[
            "company_name"
        ]

        application_type = request.form[
            "application_type"
        ]

        intern_date = request.form[
            "intern_date"
        ]

        es_deadline = request.form[
            "es_deadline"
        ]

        es_status = request.form[
            "es_status"
        ]

        spi_deadline = request.form[
            "spi_deadline"
        ]

        spi_status = request.form[
            "spi_status"
        ]

        selection_status = request.form[
            "selection_status"
        ]

        memo = request.form[
            "memo"
        ]


        conn.execute("""
            UPDATE companies

            SET
                company_name = ?,
                application_type = ?,
                intern_date = ?,
                es_deadline = ?,
                es_status = ?,
                spi_deadline = ?,
                spi_status = ?,
                selection_status = ?,
                memo = ?

            WHERE id = ?
        """, (

            company_name,

            application_type,

            intern_date,

            es_deadline,

            es_status,

            spi_deadline,

            spi_status,

            selection_status,

            memo,

            id

        ))


        conn.commit()

        conn.close()


        return redirect("/")


    company = conn.execute(
        """
        SELECT *
        FROM companies
        WHERE id = ?
        """,
        (id,)
    ).fetchone()


    conn.close()


    if company is None:

        return redirect("/")


    company_data = dict(
        company
    )


    company_data[
        "es_deadline_input"
    ] = to_datetime_local(
        company["es_deadline"]
    )


    company_data[
        "spi_deadline_input"
    ] = to_datetime_local(
        company["spi_deadline"]
    )


    return render_template(
        "edit.html",
        company=company_data
    )


# =========================================
# 状況自動保存
# =========================================

@app.route(
    "/update_status/<int:id>",
    methods=["POST"]
)
def update_status(id):

    field = request.form.get(
        "field"
    )

    value = request.form.get(
        "value"
    )


    allowed_values = {

        "es_status": [
            "未提出",
            "提出済"
        ],

        "spi_status": [
            "未受験",
            "受験済"
        ],

        "selection_status": [
            "応募前",
            "応募済",
            "選考中",
            "合格",
            "不合格"
        ]
    }


    if field not in allowed_values:

        flash(
            "変更できない項目です。",
            "error"
        )

        return redirect(
            request.referrer or "/"
        )


    if value not in allowed_values[field]:

        flash(
            "変更できない値です。",
            "error"
        )

        return redirect(
            request.referrer or "/"
        )


    conn = get_db()


    conn.execute(
        f"""
        UPDATE companies
        SET {field} = ?
        WHERE id = ?
        """,
        (
            value,
            id
        )
    )


    conn.commit()

    conn.close()


    return redirect(
        request.referrer or "/"
    )


# =========================================
# 削除
# =========================================

@app.route(
    "/delete/<int:id>",
    methods=["POST"]
)
def delete_company(id):

    conn = get_db()


    conn.execute(
        """
        DELETE FROM companies
        WHERE id = ?
        """,
        (id,)
    )


    conn.commit()

    conn.close()


    return redirect("/")


# =========================================
# 起動
# =========================================

if __name__ == "__main__":

    init_db()

    app.run(
        debug=True
    )