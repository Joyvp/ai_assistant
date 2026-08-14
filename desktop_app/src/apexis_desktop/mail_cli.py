"""``apexis away`` / ``apexis home`` / ``apexis email`` — the commands."""

from __future__ import annotations

from apexis_desktop import away, mail


DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
CYAN = "\033[36m"
OFF = "\033[0m"


# -- away / home -----------------------------------------------------------


def go_away(note: str = "") -> int:
    away.leave(note)

    print()
    print(f"  {CYAN}●{OFF} {BOLD}Away.{OFF}")
    if note:
        print(f"    {DIM}{note}{OFF}")
    print()

    if mail.is_configured():
        print(f"  {DIM}Finished jobs will be emailed to {mail.owner()}.{OFF}")
    else:
        print(f"  {YELLOW}Email isn't set up, so nothing can reach you.{OFF}")
        print(f"  {DIM}Set it up with:  apexis email setup{OFF}")

    print(f"  {DIM}Say you're back with:  apexis home{OFF}")
    print()
    return 0


def come_home() -> int:
    previous = away.arrive()

    print()
    if not previous.get("away"):
        print(f"  {DIM}You weren't marked away.{OFF}\n")
        return 0

    print(f"  {GREEN}●{OFF} {BOLD}Welcome back.{OFF}")
    print()

    waiting = mail.outbox()
    if waiting:
        word = "message" if len(waiting) == 1 else "messages"
        print(f"  {YELLOW}{len(waiting)} {word} waiting for your approval{OFF}")
        print(f"  {DIM}apexis email outbox{OFF}")
        print()
    return 0


def status() -> int:
    print()
    if away.is_away():
        print(f"  {CYAN}●{OFF} away {DIM}— left {away.elapsed()}{OFF}")
    else:
        print(f"  {GREEN}●{OFF} home {DIM}— APEXIS won't email you{OFF}")
    print()
    return 0


# -- email -----------------------------------------------------------------


def show() -> int:
    print()
    print(f"  {BOLD}Email{OFF}")
    print()

    if mail.is_configured():
        print(f"  sends from  {mail.sender()}")
        print(f"  writes to   {mail.owner()} {DIM}(you){OFF}")
        print(f"  server      {DIM}{mail.host()}:{mail.port()}{OFF}")
    else:
        print(f"  {YELLOW}not set up{OFF}")
        print(f"  {DIM}missing: {', '.join(mail.missing())}{OFF}")
        print()
        print(f"  {DIM}apexis email setup{OFF}")

    print()
    print(f"  {DIM}Rules:{OFF}")
    print(f"    {DIM}· writes to you only while you're away{OFF}")
    print(f"    {DIM}· anyone else goes to the outbox for your approval{OFF}")
    print()

    waiting = mail.outbox()
    if waiting:
        print(f"  {YELLOW}{len(waiting)} in the outbox{OFF} "
              f"{DIM}— apexis email outbox{OFF}\n")
    return 0


def setup() -> int:
    print()
    print(f"  {BOLD}Setting up email{OFF}")
    print()
    print(f"  {DIM}Make a NEW Gmail for APEXIS — not your personal one.{OFF}")
    print(f"  {DIM}If the password ever leaks, nothing of yours is at risk.{OFF}")
    print()
    print(f"  {BOLD}1.{OFF} Create the account at {CYAN}accounts.google.com/signup{OFF}")
    print(f"  {BOLD}2.{OFF} Turn on 2-step verification "
          f"{DIM}(required for the next step){OFF}")
    print(f"  {BOLD}3.{OFF} Make an App Password at "
          f"{CYAN}myaccount.google.com/apppasswords{OFF}")
    print(f"     {DIM}16 characters. This is NOT your normal password.{OFF}")
    print()
    print(f"  {BOLD}4.{OFF} Then run these three, one at a time:")
    print()
    print(f"     {BOLD}apexis email from apexis.yourname@gmail.com{OFF}")
    print(f"     {BOLD}apexis email password abcdefghijklmnop{OFF}")
    print(f"     {BOLD}apexis email to your.real.address@gmail.com{OFF}")
    print()
    print(f"  {DIM}Then check it works:  apexis email test{OFF}")
    print()
    return 0


def set_value(field: str, value: str) -> int:
    labels = {
        "sender": "sends from",
        "password": "password",
        "owner": "writes to you at",
    }

    if field in {"sender", "owner"} and not mail.looks_like_address(value):
        print(f"\n  {RED}{value!r} doesn't look like an email address{OFF}\n")
        return 1

    mail.set_setting(field, value)

    shown = "•" * 16 if field == "password" else value
    print(f"\n  {GREEN}saved{OFF}  {labels[field]} {BOLD}{shown}{OFF}")
    print(f"  {DIM}stored in {mail.CONFIG_PATH} (readable only by you){OFF}")

    if mail.is_configured():
        print(f"\n  {GREEN}Email is ready.{OFF} {DIM}Try:  apexis email test{OFF}")
    else:
        print(f"\n  {DIM}still missing: {', '.join(mail.missing())}{OFF}")
    print()
    return 0


def test() -> int:
    if not mail.is_configured():
        print(f"\n  {RED}not set up yet{OFF} {DIM}— apexis email setup{OFF}\n")
        return 1

    print(f"\n  {DIM}from {mail.sender()}{OFF}")
    print(f"  {DIM}to   {mail.owner()}{OFF}")
    print(f"  {DIM}via  {mail.host()}:{mail.port()}{OFF}")
    print(f"  {DIM}sending...{OFF}")

    reason = mail.try_send(
        mail.owner(),
        "[APEXIS] test",
        "This is APEXIS checking that it can reach you.\n\n"
        "If you're reading this, it can. Nothing else was sent.",
    )

    if not reason:
        print(f"  {GREEN}sent{OFF} {DIM}— check {mail.owner()}{OFF}\n")
        return 0

    print(f"\n  {RED}failed{OFF}")
    print(f"  {reason}")
    print()

    low = reason.lower()
    if "username and password not accepted" in low or "badcredentials" in low:
        print(f"  {BOLD}Most likely:{OFF} the 16-character App Password is wrong,")
        print(f"  {DIM}or it belongs to a different Google account than{OFF}")
        print(f"  {DIM}{mail.sender()}.{OFF}")
        print()
        print(f"  {DIM}Check: is {mail.sender()} the SAME account you made{OFF}")
        print(f"  {DIM}the App Password on? Make a fresh one if unsure:{OFF}")
        print(f"  {CYAN}myaccount.google.com/apppasswords{OFF}")
    elif "application-specific password required" in low:
        print(f"  {BOLD}That was your normal Google password.{OFF}")
        print(f"  {DIM}You need an App Password: "
              f"myaccount.google.com/apppasswords{OFF}")
    elif "could not reach" in low:
        print(f"  {BOLD}Network problem, not a password problem.{OFF}")
        print(f"  {DIM}Is this machine online?{OFF}")
    print()
    return 1


def check() -> int:
    """Inspect what is stored, without printing the secret.

    A password can be wrong in ways that look identical on screen: an extra
    space, a missing character, a smart quote from a copy-paste. Length and
    character class catch all of those without ever showing it.
    """
    print()
    print(f"  {BOLD}What APEXIS has stored{OFF}")
    print()

    sender = mail.sender()
    owner = mail.owner()
    secret = mail.password()

    print(f"  sends from   {sender or DIM + '(not set)' + OFF}")
    print(f"  writes to    {owner or DIM + '(not set)' + OFF}")
    print()

    if not secret:
        print(f"  password     {RED}not set{OFF}")
        print()
        return 1

    length = len(secret)
    ok = length == 16 and secret.isalnum() and secret.islower()

    mark = f"{GREEN}✓{OFF}" if length == 16 else f"{RED}✗{OFF}"
    print(f"  password     {mark} {length} characters "
          f"{DIM}(a Google App Password is exactly 16){OFF}")

    if not secret.isalnum():
        odd = sorted({c for c in secret if not c.isalnum()})
        shown = " ".join(repr(c) for c in odd)
        print(f"               {RED}✗{OFF} contains {shown} "
              f"{DIM}— app passwords are letters only{OFF}")
    elif not secret.islower():
        print(f"               {YELLOW}!{OFF} has capitals "
              f"{DIM}— Google issues them lowercase{OFF}")

    print()
    if ok:
        print(f"  {DIM}The password looks right, so the likely problem is that{OFF}")
        print(f"  {DIM}it was created on a different Google account than{OFF}")
        print(f"  {BOLD}{sender}{OFF}")
        print()
        print(f"  {DIM}Sign in as {sender} specifically, then:{OFF}")
        print(f"  {CYAN}myaccount.google.com/apppasswords{OFF}")
    else:
        print(f"  {DIM}Set it again — spaces are fine:{OFF}")
        print(f"  {BOLD}apexis email password abcd efgh ijkl mnop{OFF}")
    print()
    return 0 if ok else 1


def doctor() -> int:
    """Test the connection one step at a time, so the failure has a location.

    'It failed' is not a diagnosis. Reaching the server, starting TLS and
    authenticating are three separate things that fail for three unrelated
    reasons, and only the last one is about the password.
    """
    import smtplib
    import socket
    import ssl

    print()
    print(f"  {BOLD}Email doctor{OFF}")
    print()

    sender = mail.sender()
    secret = mail.password()

    if not sender or not secret:
        print(f"  {RED}nothing configured yet{OFF}\n")
        return 1

    print(f"  account  {sender}")
    print(f"  password {len(secret)} chars, "
          f"{'lowercase letters only' if secret.isalpha() and secret.islower() else 'MIXED - suspicious'}")
    print()

    # Step 1: can we even reach Gmail?
    print(f"  {DIM}1. reaching {mail.host()}:{mail.port()}...{OFF}")
    try:
        server = smtplib.SMTP(mail.host(), mail.port(), timeout=20)
    except (OSError, socket.timeout) as exc:
        print(f"     {RED}✗ cannot reach the server{OFF}")
        print(f"     {DIM}{exc}{OFF}")
        print(f"     {DIM}This is a network or firewall problem, not a password one.{OFF}\n")
        return 1
    print(f"     {GREEN}✓ connected{OFF}")

    # Step 2: encryption
    print(f"  {DIM}2. starting encryption...{OFF}")
    try:
        server.starttls(context=ssl.create_default_context())
    except smtplib.SMTPException as exc:
        print(f"     {RED}✗ TLS failed: {exc}{OFF}\n")
        server.close()
        return 1
    print(f"     {GREEN}✓ encrypted{OFF}")

    # Step 3: the actual login
    print(f"  {DIM}3. logging in as {sender}...{OFF}")
    try:
        server.login(sender, secret)
    except smtplib.SMTPAuthenticationError as exc:
        detail = ""
        try:
            detail = exc.smtp_error.decode("utf-8", "replace")
        except Exception:
            detail = str(exc)
        print(f"     {RED}✗ rejected{OFF}")
        print(f"     {DIM}{detail.strip()}{OFF}")
        server.close()
        print()
        print(f"  {BOLD}The connection is fine. Google is refusing this "
              f"account.{OFF}")
        print()
        print(f"  {DIM}Things that cause this even with a fresh App Password:{OFF}")
        print(f"    {DIM}· 2-Step Verification is not actually on for "
              f"{sender}{OFF}")
        print(f"    {DIM}· the account has never been opened in a browser{OFF}")
        print(f"    {DIM}· the account is new and Google has not released "
              f"SMTP for it yet{OFF}")
        print()
        print(f"  {BOLD}Fallback that always works:{OFF} use your main Gmail as")
        print(f"  {DIM}the sender. An App Password still limits it to mail "
              f"only.{OFF}")
        print(f"    {BOLD}apexis email from your.main@gmail.com{OFF}")
        print()
        return 1
    except smtplib.SMTPException as exc:
        print(f"     {RED}✗ {exc}{OFF}\n")
        server.close()
        return 1

    print(f"     {GREEN}✓ logged in{OFF}")
    server.quit()

    print()
    print(f"  {GREEN}{BOLD}Everything works.{OFF} "
          f"{DIM}Send one with: apexis email test{OFF}")
    print()
    return 0


def show_outbox() -> int:
    waiting = mail.outbox()

    print()
    if not waiting:
        print(f"  {DIM}outbox is empty{OFF}\n")
        return 0

    print(f"  {BOLD}Waiting for your approval{OFF}")
    print()
    for index, item in enumerate(waiting):
        print(f"  {BOLD}{index}{OFF}  to {item.to}")
        print(f"     {DIM}{item.subject}{OFF}")
        preview = item.body.strip().splitlines()
        if preview:
            print(f"     {DIM}{preview[0][:60]}...{OFF}")
        print()

    print(f"  {DIM}apexis email approve 0   {OFF}{DIM}send it{OFF}")
    print(f"  {DIM}apexis email drop 0      {OFF}{DIM}throw it away{OFF}")
    print()
    return 0


def approve(index_text: str) -> int:
    try:
        index = int(index_text)
    except ValueError:
        print(f"\n  {RED}{index_text!r} is not a number{OFF}\n")
        return 1

    waiting = mail.outbox()
    if not 0 <= index < len(waiting):
        print(f"\n  {RED}no message {index} in the outbox{OFF}\n")
        return 1

    item = waiting[index]
    print(f"\n  {DIM}sending to {item.to}...{OFF}")

    try:
        mail.send_to(item.to, item.subject, item.body, approved=True)
    except mail.MailError as exc:
        print(f"  {RED}{exc}{OFF}\n")
        return 1

    mail.discard(index)
    print(f"  {GREEN}sent to {item.to}{OFF}\n")
    return 0


def drop(index_text: str) -> int:
    try:
        index = int(index_text)
    except ValueError:
        print(f"\n  {RED}{index_text!r} is not a number{OFF}\n")
        return 1

    removed = mail.discard(index)
    if removed is None:
        print(f"\n  {RED}no message {index} in the outbox{OFF}\n")
        return 1

    print(f"\n  {DIM}discarded — nothing was sent to {removed.to}{OFF}\n")
    return 0


def main(action: str = "show", value: str | None = None) -> int:
    if action in {None, "show"}:
        return show()
    if action == "setup":
        return setup()
    if action == "test":
        return test()
    if action == "outbox":
        return show_outbox()
    if action == "check":
        return check()
    if action == "doctor":
        return doctor()

    if action in {"from", "password", "to"}:
        if not value:
            print(f"\n  {DIM}usage: apexis email {action} VALUE{OFF}\n")
            return 1
        field = {"from": "sender", "password": "password", "to": "owner"}[action]
        if field == "password":
            # Gmail shows app passwords in four spaced groups.
            value = value.replace(" ", "")
        return set_value(field, value)

    if action == "approve":
        if not value:
            print(f"\n  {DIM}usage: apexis email approve 0{OFF}\n")
            return 1
        return approve(value)

    if action == "drop":
        if not value:
            print(f"\n  {DIM}usage: apexis email drop 0{OFF}\n")
            return 1
        return drop(value)

    print(f"\n  {RED}don't know how to '{action}'{OFF}\n")
    return 1
