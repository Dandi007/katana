"""credctl — mint / revoke / list credentials in a credstore file.

Usage::

    python -m katana_remote.credctl --file /data/katana-remote/credentials.json \
        mint --principal alice --tenant alice \
        --domains memory,wiki,work-folder --scopes read,query,mutate,command \
        [--expires-days N]

    python -m katana_remote.credctl --file ... revoke --token ktn_xxx
    python -m katana_remote.credctl --file ... revoke --hash sha256:xxxx
    python -m katana_remote.credctl --file ... list

The plaintext token is printed exactly once by ``mint``; the file only ever
holds hashes.
"""

from __future__ import annotations

import argparse
import sys
import time

from katana_remote.auth import CredentialEntry, hash_token
from katana_remote.credstore import generate_token, load_entries, save_entries
from katana_remote.scopes import ALL_SCOPES, SCOPE_ALL


def _csv(value: str) -> set[str]:
    return {v.strip() for v in value.split(",") if v.strip()}


def _cmd_mint(args) -> int:
    scopes = _csv(args.scopes)
    bad = scopes - ALL_SCOPES - {SCOPE_ALL}
    if bad:
        print(f"error: unknown scopes: {sorted(bad)}", file=sys.stderr)
        return 2
    token = generate_token()
    entry = CredentialEntry(
        token_hash=hash_token(token),
        principal_id=args.principal,
        tenant=args.tenant,
        domains=_csv(args.domains),
        scopes=scopes,
        expires_at=(time.time() + args.expires_days * 86400) if args.expires_days else None,
    )
    entries = load_entries(args.file)
    entries.append(entry)
    save_entries(args.file, entries)
    print(token)
    print(
        f"# minted: principal={entry.principal_id} tenant={entry.tenant} "
        f"domains={sorted(entry.domains)} scopes={sorted(entry.scopes)} "
        f"hash={entry.token_hash[:23]}…  (plaintext shown once — store it now)",
        file=sys.stderr,
    )
    return 0


def _cmd_revoke(args) -> int:
    target_hash = args.hash if args.hash else hash_token(args.token)
    entries = load_entries(args.file)
    hit = [e for e in entries if e.token_hash == target_hash]
    if not hit:
        print("error: no credential with that token/hash", file=sys.stderr)
        return 1
    for e in hit:
        e.revoked = True
    save_entries(args.file, entries)
    print(f"revoked {len(hit)} credential(s): {target_hash[:23]}…")
    return 0


def _status(entry: CredentialEntry) -> str:
    if entry.revoked:
        return "revoked"
    if entry.expires_at is not None and time.time() > entry.expires_at:
        return "expired"
    return "active"


def _cmd_list(args) -> int:
    entries = load_entries(args.file)
    if not entries:
        print("(empty)")
        return 0
    for e in entries:
        print(
            f"{e.token_hash[:23]}…  {_status(e):8s}  principal={e.principal_id}  "
            f"tenant={e.tenant}  domains={','.join(sorted(e.domains)) or '-'}  "
            f"scopes={','.join(sorted(e.scopes)) or '-'}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="credctl", description=__doc__)
    parser.add_argument("--file", required=True, help="credstore JSON file path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_mint = sub.add_parser("mint", help="mint a new token (plaintext printed once)")
    p_mint.add_argument("--principal", required=True)
    p_mint.add_argument("--tenant", required=True)
    p_mint.add_argument("--domains", required=True, help="csv, e.g. memory,wiki,work-folder")
    p_mint.add_argument("--scopes", required=True, help="csv, e.g. read,query,mutate,command or *")
    p_mint.add_argument("--expires-days", type=float, default=None)
    p_mint.set_defaults(func=_cmd_mint)

    p_revoke = sub.add_parser("revoke", help="revoke by plaintext token or hash")
    g = p_revoke.add_mutually_exclusive_group(required=True)
    g.add_argument("--token")
    g.add_argument("--hash")
    p_revoke.set_defaults(func=_cmd_revoke)

    p_list = sub.add_parser("list", help="list credentials (hashes only)")
    p_list.set_defaults(func=_cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
