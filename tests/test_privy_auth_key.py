# tests/test_privy_auth_key.py
# Diagnostic test for PRIVY_AUTH_KEY loading.
#
# Run locally:
#   source venv/bin/activate && python3 tests/test_privy_auth_key.py
#
# Run in Docker:
#   docker compose exec tagwise-bot python3 tests/test_privy_auth_key.py

import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

RAW_KEY = os.getenv("PRIVY_AUTH_KEY", "")
PRIVY_APP_ID = os.getenv("PRIVY_APP_ID", "")
PRIVY_APP_SECRET = os.getenv("PRIVY_APP_SECRET", "")


def sep(title=""):
    print("\n" + "-" * 60)
    if title:
        print("  " + title)
        print("-" * 60)


def try_load_pem(label, pem):
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        key = load_pem_private_key(pem.encode("utf-8"), password=None)
        print("  [OK]  cryptography load_pem_private_key: " + type(key).__name__)
        return True
    except Exception as e:
        print("  [FAIL] cryptography load_pem_private_key: " + str(e))
        return False


def try_privy_update(label, key_value):
    try:
        from privy import PrivyAPI
        client = PrivyAPI(app_id=PRIVY_APP_ID, app_secret=PRIVY_APP_SECRET)
        client.update_authorization_key(key_value)
        print("  [OK]  update_authorization_key accepted")
        return True, client
    except Exception as e:
        print("  [FAIL] update_authorization_key: " + str(e))
        return False, None


WALLET_ID = "jt7rhyni7z52ed39x0xi2p02"  # the wallet ID from your logs

def try_signed_request(label, client):
    if client is None:
        print("  [SKIP] no client")
        return
    try:
        result = client.wallets.list()
        count = len(result.data) if hasattr(result, "data") else "?"
        print("  [OK]  wallets.list(): count=" + str(count))
    except Exception as e:
        print("  [FAIL] wallets.list(): " + str(e))

def try_rpc_signing(label, client):
    if client is None:
        print("  [SKIP] no client")
        return
    try:
        # Minimal dummy typed-data sign — this exercises the actual authorization
        # signature path that wallets.rpc() uses and that has been failing.
        import time as _time
        dummy_typed_data = {
            "types": {"EIP712Domain": [{"name": "name", "type": "string"}], "Test": [{"name": "value", "type": "string"}]},
            "domain": {"name": "Test"},
            "primary_type": "Test",
            "message": {"value": "hello"}
        }
        resp = client.wallets.rpc(
            wallet_id=WALLET_ID,
            method="eth_signTypedData_v4",
            params={"typed_data": dummy_typed_data},
        )
        sig = getattr(getattr(resp, "data", resp), "signature", None)
        print("  [OK]  wallets.rpc(eth_signTypedData_v4): sig=" + str(sig)[:20] + "...")
    except Exception as e:
        print("  [FAIL] wallets.rpc(eth_signTypedData_v4): " + str(e))


sep("RAW KEY FROM ENV")
print("  Length : " + str(len(RAW_KEY)))
print("  Prefix : " + repr(RAW_KEY[:50]))
print("  Suffix : " + repr(RAW_KEY[-30:]))

sep("Format 1 - raw value (no transformation)")
fmt1 = RAW_KEY
try_load_pem("raw", fmt1)
ok, client = try_privy_update("raw", fmt1)
try_signed_request("raw", client)
try_rpc_signing("raw", client)

sep("Format 2 - replace literal backslash-n with real newlines")
fmt2 = RAW_KEY.replace("\\n", "\n")
print("  Preview: " + repr(fmt2[:80]))
try_load_pem("slash-n->newline", fmt2)
ok, client = try_privy_update("slash-n->newline", fmt2)
try_signed_request("slash-n->newline", client)
try_rpc_signing("slash-n->newline", client)

sep("Format 3 - strip 'wallet-auth:' prefix -> PKCS8 PEM")
if RAW_KEY.startswith("wallet-auth:"):
    b64 = RAW_KEY[len("wallet-auth:"):].strip()
    b64 = b64.replace("\n", "").replace("\r", "").replace(" ", "")
    wrapped = "\n".join(textwrap.wrap(b64, 64))
    fmt3 = "-----BEGIN PRIVATE KEY-----\n" + wrapped + "\n-----END PRIVATE KEY-----\n"
    print("  PEM preview: " + repr(fmt3[:120]))
    ok3 = try_load_pem("PKCS8 PEM", fmt3)
    ok, client = try_privy_update("PKCS8 PEM", fmt3)
    try_signed_request("PKCS8 PEM", client)
    try_rpc_signing("PKCS8 PEM", client)
else:
    print("  (key does not start with wallet-auth:, skipping)")

sep("Format 4 - strip 'wallet-auth:' prefix -> EC PEM (SEC1)")
if RAW_KEY.startswith("wallet-auth:"):
    b64 = RAW_KEY[len("wallet-auth:"):].strip()
    b64 = b64.replace("\n", "").replace("\r", "").replace(" ", "")
    wrapped = "\n".join(textwrap.wrap(b64, 64))
    fmt4 = "-----BEGIN EC PRIVATE KEY-----\n" + wrapped + "\n-----END EC PRIVATE KEY-----\n"
    print("  PEM preview: " + repr(fmt4[:120]))
    try_load_pem("EC PEM", fmt4)
    ok, client = try_privy_update("EC PEM", fmt4)
    try_signed_request("EC PEM", client)
    try_rpc_signing("EC PEM", client)
else:
    print("  (key does not start with wallet-auth:, skipping)")

sep("Format 5 - current _normalize_privy_auth_key() result")
try:
    from bot.trading.privy_service import _normalize_privy_auth_key
    fmt5 = _normalize_privy_auth_key(RAW_KEY)
    print("  Output: " + repr(fmt5[:120]))
    try_load_pem("normalize_result", fmt5)
    ok, client = try_privy_update("normalize_result", fmt5)
    try_signed_request("normalize_result", client)
    try_rpc_signing("normalize_result", client)
except ImportError as e:
    print("  Could not import: " + str(e))

sep("DONE")
