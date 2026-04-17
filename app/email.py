import resend
import os

resend.api_key = os.environ.get("RESEND_API_KEY")
FROM_ADDRESS = os.environ.get("EMAIL_FROM", "onboarding@resend.dev")


def send_password_reset(to_email: str, reset_token: str, name: str):
    reset_url = f"{os.environ.get('FRONTEND_ORIGIN')}/reset-password?token={reset_token}"
    resend.Emails.send({
        "from": FROM_ADDRESS,
        "to": to_email,
        "subject": "Reset your Vantage password",
        "html": f"""
          <h2>Password Reset</h2>
          <p>Hi {name},</p>
          <p>Click below to reset your password.
             This link expires in 30 minutes.</p>
          <a href="{reset_url}"
             style="background:#1a1a1a;color:#fff;padding:12px 24px;
                    border-radius:6px;text-decoration:none;">
            Reset Password
          </a>
          <p>If you didn't request this, ignore this email.</p>
        """,
    })


def send_welcome(to_email: str, name: str, temp_password: str):
    login_url = f"{os.environ.get('FRONTEND_ORIGIN')}/login"
    resend.Emails.send({
        "from": FROM_ADDRESS,
        "to": to_email,
        "subject": "Welcome to Vantage — Your account is ready",
        "html": f"""
          <h2>Welcome to Vantage, {name}!</h2>
          <p>Your account has been created.</p>
          <p>Login: <strong>{to_email}</strong><br>
             Temporary password: <strong>{temp_password}</strong></p>
          <p>You will be asked to change your password on first login.</p>
          <a href="{login_url}"
             style="background:#1a1a1a;color:#fff;padding:12px 24px;
                    border-radius:6px;text-decoration:none;">
            Login Now
          </a>
        """,
    })
