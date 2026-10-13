"""Create (or update) a WhatsApp channel from Meta's free test number, without Embedded Signup.

    # token on stdin (preferred: nothing in the process list or shell history)
    docker exec -i vicero-prod-api-1 python scripts/add_whatsapp_test_channel.py \\
        --agent-id <uuid> --phone-number-id 1356911374173965 --waba-id 3368583013326109
    # ...then paste the token and press Ctrl-D

    # or from the environment
    WHATSAPP_TEST_TOKEN=... python scripts/add_whatsapp_test_channel.py --agent-id <uuid> ...

The token is never taken from argv and is never printed or logged. The test token lasts 24 hours:
run the script again with a new one to refresh it (it is idempotent: same number, same row).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

DEFAULT_PHONE_NUMBER_ID = "1356911374173965"
DEFAULT_WABA_ID = "3368583013326109"


def read_token(stdin: object = None, environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    token = env.get("WHATSAPP_TEST_TOKEN", "").strip()
    if token:
        return token
    stream = sys.stdin if stdin is None else stdin
    if getattr(stream, "isatty", lambda: False)():
        print("Paste the access token, then press Ctrl-D (it is not echoed back):", file=sys.stderr)
    return str(stream.read()).strip()  # type: ignore[attr-defined]


async def run(args: argparse.Namespace, token: str) -> int:
    # Imported here so `--help` works without the app's settings.
    from app.channels import whatsapp_test
    from app.core.errors import AppError
    from app.db.session import SessionFactory

    try:
        async with SessionFactory() as session:
            channel, created = await whatsapp_test.add_test_channel(
                session,
                agent_id=args.agent_id,
                phone_number_id=args.phone_number_id,
                waba_id=args.waba_id,
                display_name=args.display_name,
                token=token,
            )
            await session.commit()
    except AppError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    print(f"{'created' if created else 'updated'} WhatsApp channel {channel.id} for number {args.phone_number_id}")
    print("send a message to the test number from a phone that is on the recipient list in Meta's API Setup page")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent-id", type=uuid.UUID, required=True)
    parser.add_argument("--phone-number-id", default=DEFAULT_PHONE_NUMBER_ID)
    parser.add_argument("--waba-id", default=DEFAULT_WABA_ID)
    parser.add_argument("--display-name", default="WhatsApp test")
    args = parser.parse_args(argv)
    token = read_token()
    if not token:
        print("error: no token (pipe it on stdin or set WHATSAPP_TEST_TOKEN)", file=sys.stderr)
        return 2
    return asyncio.run(run(args, token))


if __name__ == "__main__":
    raise SystemExit(main())
