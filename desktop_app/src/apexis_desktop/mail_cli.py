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

    print(f"\n  {DIM}sending a test to {mail.owner()}...{OFF}")

    ok = mail.notify(
        "[APEXIS] test",
        "This is APEXIS checking that it can reach you.\n\n"
        "If you're reading this, it can. Nothing else was sent.",
    )

    if ok:
        print(f"  {GREEN}sent{OFF} {DIM}— check {mail.owner()}{OFF}\n")
        return 0

    print(f"  {RED}failed{OFF}")
    print(f"  {DIM}For Gmail the password must be an App Password, "
          f"not your normal one.{OFF}\n")
    return 1


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

    if action in {"from", "password", "to"}:
        if not value:
            print(f"\n  {DIM}usage: apexis email {action} VALUE{OFF}\n")
            return 1
        field = {"from": "sender", "password": "password", "to": "owner"}[action]
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
