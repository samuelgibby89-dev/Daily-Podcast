#!/usr/bin/env python3
"""Send the morning push notification.

Run after the episode is published:

    python notify.py --date 2026-11-10 --title "..." --body "..."

Needs three things in the environment, all set once as repository secrets:

    VAPID_PUBLIC_KEY    also published to the site as push.json so the
                        browser can subscribe against it
    VAPID_PRIVATE_KEY   signs the push; never leaves Actions
    PUSH_SUBSCRIPTION   the JSON blob the app hands you when you tap the bell

Generate the key pair once, locally, with:

    python -c "from py_vapid import Vapid01; v=Vapid01(); v.generate_keys(); \
print('public :', v.public_key_urlsafe_base64()); \
print('private:', v.private_key_urlsafe_base64())"

A missing subscription is not an error: you simply haven't enabled alerts yet,
and the build should not fail over it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="")
    parser.add_argument("--image", default="")
    args = parser.parse_args()

    subscription = os.getenv("PUSH_SUBSCRIPTION", "").strip()
    public_key = os.getenv("VAPID_PUBLIC_KEY", "").strip()
    private_key = os.getenv("VAPID_PRIVATE_KEY", "").strip()
    subject = os.getenv("VAPID_SUBJECT", "mailto:noreply@example.com").strip()

    if not subscription:
        print("  · no PUSH_SUBSCRIPTION set — skipping the notification")
        return 0
    if not (public_key and private_key):
        print("  ! VAPID keys missing — skipping the notification", file=sys.stderr)
        return 0

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        print("  ! pywebpush not installed — skipping", file=sys.stderr)
        return 0

    # One subscription or several; a bare object is the common case.
    try:
        parsed = json.loads(subscription)
    except json.JSONDecodeError as exc:
        print(f"  ! PUSH_SUBSCRIPTION is not valid JSON ({exc})", file=sys.stderr)
        return 0
    subs = parsed if isinstance(parsed, list) else [parsed]

    payload = json.dumps({
        "title": "The Morning Brief",
        "body": args.body or args.title,
        "date": args.date,
        "image": args.image or None,
    })

    sent = 0
    for sub in subs:
        endpoint = (sub or {}).get("endpoint", "")
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},
                timeout=20,
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                # The browser threw the subscription away -- re-enable alerts
                # in the app and replace the secret.
                print(f"  ! subscription expired ({status}); re-enable alerts "
                      f"in the app and update PUSH_SUBSCRIPTION")
            else:
                print(f"  ! push failed ({status}): {str(exc)[:140]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! push failed: {str(exc)[:140]}")

    print(f"  + notification sent to {sent} of {len(subs)} subscription(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
