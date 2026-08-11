"""
Email Tools - Option B: Can email strangers but asks confirmation (chill anti-Ultron)
"""
import os, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

class EmailTools:
    def __init__(self, memory_core=None):
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.email_user = os.getenv("EMAIL_USER")
        self.email_pass = os.getenv("EMAIL_PASS")
        self.notify_email = os.getenv("NOTIFY_EMAIL")  # Your personal email, safe default
        self.memory = memory_core

    def is_configured(self):
        return all([self.smtp_host, self.email_user, self.email_pass, self.notify_email])

    def _is_stranger(self, to_email):
        # Stranger = not your personal notify email
        return to_email.lower() != self.notify_email.lower() if self.notify_email else True

    def send_email(self, to_email, subject, body, attachment_path=None):
        # Option B logic
        is_stranger = self._is_stranger(to_email)

        if is_stranger:
            # Chill but anti-Ultron: ask confirmation for strangers
            print(f"\n[Email Guard] You want to email stranger: {to_email}")
            print(f"Subject: {subject}")
            confirm = input(f"Confirm email stranger {to_email}? This will be logged. [y/n]: ").lower().strip()
            if confirm not in ["y", "yes"]:
                return f"[BLOCKED] Email to stranger {to_email} cancelled by user"

        if not self.is_configured():
            return "[Email not configured] Set EMAIL vars in .env"

        try:
            msg = MIMEMultipart()
            msg['From'] = self.email_user
            msg['To'] = to_email
            msg['Subject'] = f"[APEXIS] {subject}"
            msg.attach(MIMEText(body, 'plain'))

            if attachment_path and os.path.exists(attachment_path):
                with open(attachment_path, "rb") as f:
                    part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
                    part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
                    msg.attach(part)

            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.starttls()
            server.login(self.email_user, self.email_pass)
            server.send_message(msg)
            server.quit()

            log_msg = f"Emailed {'STRANGER' if is_stranger else 'YOU'} {to_email}: {subject} with attachment={bool(attachment_path)}"
            if self.memory:
                self.memory.log_internet("email_sent", log_msg)

            return f"[Emailed] {log_msg}"

        except Exception as e:
            return f"[Email Error] {e}"

    # Convenience: email YOU with file (your Option from Q8)
    def email_you_with_file(self, subject, body, file_path):
        return self.send_email(self.notify_email, subject, body, file_path)
