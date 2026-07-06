# Sends an HTML email summarizing new job matches, plus a smaller,
# visually secondary section for ambiguous postings (unclear location or
# age, but still possibly relevant).
 
# Credentials are read from environment variables (via a .env file, never
# committed to git). Required variables:
#     EMAIL_ADDRESS    - the Gmail address sending the notification
#     EMAIL_APP_PASSWORD - a Gmail App Password, NOT your real password
#     RECIPIENT_EMAIL  - where to send the notification (can be the same address)
 
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
 
from dotenv import load_dotenv
 
load_dotenv()
 
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 465
 
 
def _match_card_html(job):
    # One highlighted card per confirmed match - bold, colored, clear link.
    return f"""
    <div style="background:#f0fdf4; border-left:4px solid #22c55e; border-radius:8px;
                padding:16px; margin-bottom:12px;">
        <div style="font-size:16px; font-weight:600; color:#14532d; margin-bottom:4px;">
            {job['title']}
        </div>
        <div style="font-size:14px; color:#166534; margin-bottom:8px;">
            {job['company']} &middot; {job['location']}
        </div>
        <a href="{job['link']}" style="display:inline-block; background:#22c55e; color:#ffffff;
           text-decoration:none; padding:6px 14px; border-radius:6px; font-size:13px;">
            View posting →
        </a>
    </div>
    """
 
 
def _ambiguous_row_html(job):
    # One muted row per ambiguous posting - visible but clearly secondary.
    return f"""
    <div style="border-left:3px solid #d4d4d8; padding:8px 12px; margin-bottom:6px;
                background:#fafafa;">
        <span style="font-size:13px; color:#52525b;">
            <strong>{job['title']}</strong> — {job['company']} &middot; {job['location']}
        </span>
        <a href="{job['link']}" style="font-size:12px; color:#71717a; margin-left:8px;">
            (check manually)
        </a>
    </div>
    """
 
 
def build_email_html(new_jobs, ambiguous_jobs):
    match_section = "".join(_match_card_html(job) for job in new_jobs)
    ambiguous_section = "".join(_ambiguous_row_html(job) for job in ambiguous_jobs)
 
    ambiguous_block = ""
    if ambiguous_jobs:
        ambiguous_block = f"""
        <div style="margin-top:24px;">
            <div style="font-size:13px; font-weight:600; color:#71717a;
                        text-transform:uppercase; letter-spacing:0.05em; margin-bottom:8px;">
                Worth a manual look ({len(ambiguous_jobs)})
            </div>
            {ambiguous_section}
        </div>
        """
 
    return f"""
    <div style="font-family:-apple-system, Segoe UI, Roboto, sans-serif; max-width:600px;
                margin:0 auto; padding:20px;">
        <div style="font-size:20px; font-weight:700; color:#18181b; margin-bottom:4px;">
            🎯 {len(new_jobs)} new job match{'es' if len(new_jobs) != 1 else ''}
        </div>
        <div style="font-size:14px; color:#71717a; margin-bottom:20px;">
            From your job monitor
        </div>
        {match_section}
        {ambiguous_block}
    </div>
    """
 
 
def send_notification(new_jobs, ambiguous_jobs):
    # Sends the email only if there's something to report at all — no email
    # fires on a run with nothing new and nothing ambiguous, to avoid
    # inbox noise on every scheduled run.
    if not new_jobs and not ambiguous_jobs:
        print("No new or ambiguous postings — skipping email.")
        return
 
    email_address = os.environ["EMAIL_ADDRESS"]
    email_password = os.environ["EMAIL_APP_PASSWORD"]
    recipient = os.environ["RECIPIENT_EMAIL"]
 
    subject = f"🎯 {len(new_jobs)} new job match{'es' if len(new_jobs) != 1 else ''}" \
              f"{f' + {len(ambiguous_jobs)} to check' if ambiguous_jobs else ''}"
 
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = email_address
    message["To"] = recipient
    message.attach(MIMEText(build_email_html(new_jobs, ambiguous_jobs), "html"))
 
    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
        server.login(email_address, email_password)
        server.sendmail(email_address, recipient, message.as_string())
 
    print(f"Email sent: {subject}")