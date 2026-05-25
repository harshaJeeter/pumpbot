"""
Trader - Executes buy and sell transactions on pump.fun and Jupiter.
Handles transaction building, signing, and confirmation.
"""

import asyncio
import base64
import time
import logging
from typing import Optional, Dict, Tuple

import httpx
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.pubkey import Pubkey
import base58

import config

logger = logging.getLogger(__name__)


class Trader:
    """
    Handles all transaction execution:
    - Buy via PumpPortal API (fastest for pump.fun)
    - Sell via Jupiter Swap API V2 (best liquidity)
    - Fallback: sell via PumpPortal
    """

    def __init__(self):
        self._keypair: Optional[Keypair] = None
        self._init_wallet()
        self._last_buy_times: list = []  # timestamps of recent buys
        self._pending_txs: Dict[str, float] = {}  # tx_sig -> timestamp

    def _init_wallet(self):
        """Initialize wallet from private key."""
        pk = config.WALLET_PRIVATE_KEY
        if not pk:
            logger.warning("No WALLET_PRIVATE_KEY set - trading disabled!")
            return

        try:
            # Support both base58 and byte array format
            if pk.startswith("["):
                # Byte array format
                import json
                key_bytes = bytes(json.loads(pk))
                self._keypair = Keypair.from_bytes(key_bytes)
            else:
                # Base58 format
                key_bytes = base58.b58decode(pk)
                self._keypair = Keypair.from_bytes(key_bytes)

            logger.info(f"Wallet initialized: {self._keypair.pubkey()}")
        except Exception as e:
            logger.error(f"Failed to init wallet: {e}")

    @property
    def wallet_pubkey(self) -> Optional[str]:
        if self._keypair:
            return str(self._keypair.pubkey())
        return config.WALLET_PUBLIC_KEY

    async def get_sol_balance(self) -> float:
        """Get current SOL balance of wallet."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    config.HELIUS_RPC_URL,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getBalance",
                        "params": [self.wallet_pubkey],
                    }
                )
                if resp.status_code == 200:
                    data = resp.json()
                    lamports = data.get("result", {}).get("value", 0)
                    return lamports / 1e9
        except Exception as e:
            logger.error(f"Balance check failed: {e}")
        return 0.0

    async def get_token_balance(self, mint: str) -> int:
        """Get token balance for a specific mint."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    config.HELIUS_RPC_URL,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTokenAccountsByOwner",
                        "params": [
                            self.wallet_pubkey,
                            {"mint": mint},
                            {"encoding": "jsonParsed"}
                        ],
                    }
                )
                if resp.status_code == 200:
                    data = resp.json()
                    accounts = data.get("result", {}).get("value", [])
                    total = 0
                    for acc in accounts:
                        info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                        token_amount = info.get("tokenAmount", {})
                        total += int(token_amount.get("amount", 0))
                    return total
        except Exception as e:
            logger.error(f"Token balance check failed for {mint}: {e}")
        return 0

    def can_buy(self) -> Tuple[bool, str]:
        """Check if we're allowed to buy right now."""
        if not self._keypair:
            return False, "No wallet configured"

        # Rate limit
        now = time.time()
        hour_ago = now - 3600
        recent_buys = [t for t in self._last_buy_times if t > hour_ago]
        if len(recent_buys) >= config.MAX_BUYS_PER_HOUR:
            return False, f"Rate limit: {len(recent_buys)}/{config.MAX_BUYS_PER_HOUR} buys this hour"

        return True, "OK"

    async def buy_token(self, mint: str, sol_amount: float = None) -> Dict:
        """
        Buy a token on pump.fun.
        Uses PumpPortal for fastest execution.
        Returns: {success, tx_signature, error, sol_spent}
        """
        if not self._keypair:
            return {"success": False, "error": "No wallet", "tx_signature": ""}

        sol_amount = sol_amount or config.BUY_AMOUNT_SOL

        # Pre-flight checks
        balance = await self.get_sol_balance()
        if balance < sol_amount + config.MIN_SOL_BALANCE:
            return {
                "success": False,
                "error": f"Insufficient SOL: {balance:.4f} (need {sol_amount + config.MIN_SOL_BALANCE:.4f})",
                "tx_signature": "",
            }

        # Method 1: PumpPortal Lightning API
        result = await self._buy_via_pumpportal(mint, sol_amount)
        if result["success"]:
            self._last_buy_times.append(time.time())
            return result

        # Method 2: Build tx locally with pump.fun SDK
        logger.warning(f"PumpPortal buy failed ({result['error']}), trying local tx...")
        result2 = await self._buy_via_local_tx(mint, sol_amount)
        if result2["success"]:
            self._last_buy_times.append(time.time())
            return result2

        return result  # Return first error

    async def sell_token(self, mint: str, amount_pct: float = 1.0) -> Dict:
        """
        Sell a token. Uses Jupiter for best price.
        amount_pct: 0.0-1.0, portion of holdings to sell.
        Returns: {success, tx_signature, error, sol_received}
        """
        if not self._keypair:
            return {"success": False, "error": "No wallet", "tx_signature": ""}

        # Get token balance
        token_balance = await self.get_token_balance(mint)
        if token_balance <= 0:
            return {"success": False, "error": "No token balance", "tx_signature": ""}

        sell_amount = int(token_balance * amount_pct)
        if sell_amount <= 0:
            return {"success": False, "error": "Sell amount too small", "tx_signature": ""}

        # Method 1: Jupiter Swap API (best for graduated tokens)
        result = await self._sell_via_jupiter(mint, sell_amount)
        if result["success"]:
            return result

        # Method 2: PumpPortal (for tokens still on bonding curve)
        logger.warning(f"Jupiter sell failed ({result['error']}), trying PumpPortal...")
        result2 = await self._sell_via_pumpportal(mint, amount_pct)
        if result2["success"]:
            return result2

        return result

    async def _buy_via_pumpportal(self, mint: str, sol_amount: float) -> Dict:
        """Buy via PumpPortal Lightning Transaction API."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                payload = {
                    "publicKey": self.wallet_pubkey,
                    "action": "buy",
                    "mint": mint,
                    "amount": sol_amount,
                    "denominatedInSol": "true",
                    "slippage": config.SLIPPAGE_BPS / 100,  # PumpPortal uses percentage
                    "priorityFee": 0.001,  # Extra priority fee in SOL
                    "pool": "pump",
                }

                resp = await client.post(
                    "https://pumpportal.fun/api/trade-local",
                    json=payload,
                )

                if resp.status_code == 200:
                    # Returns a serialized transaction to sign
                    tx_bytes = resp.content

                    # Deserialize and sign
                    tx = VersionedTransaction.from_bytes(tx_bytes)
                    # Re-create with signature
                    signed_tx = VersionedTransaction(tx.message, [self._keypair])

                    # Send to RPC
                    tx_sig = await self._send_transaction(signed_tx)
                    if tx_sig:
                        return {
                            "success": True,
                            "tx_signature": tx_sig,
                            "error": "",
                            "sol_spent": sol_amount,
                            "method": "pumpportal",
                        }
                    else:
                        return {"success": False, "error": "TX not confirmed", "tx_signature": ""}
                else:
                    error_text = resp.text[:200]
                    return {"success": False, "error": f"PumpPortal {resp.status_code}: {error_text}", "tx_signature": ""}

        except Exception as e:
            return {"success": False, "error": f"PumpPortal exception: {str(e)[:200]}", "tx_signature": ""}

    async def _buy_via_local_tx(self, mint: str, sol_amount: float) -> Dict:
        """Fallback: build buy transaction locally."""
        # This would use the pump.fun SDK directly
        # For now, return failure - PumpPortal should handle it
        return {"success": False, "error": "Local TX not implemented (use PumpPortal)", "tx_signature": ""}

    async def _sell_via_jupiter(self, mint: str, amount: int) -> Dict:
        """Sell via Jupiter Swap API V2."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Step 1: Get quote
                quote_resp = await client.get(
                    config.JUPITER_QUOTE_API,
                    params={
                        "inputMint": mint,
                        "outputMint": config.SOL_MINT,
                        "amount": str(amount),
                        "slippageBps": str(config.SLIPPAGE_BPS),
                    }
                )

                if quote_resp.status_code != 200:
                    return {"success": False, "error": f"Jupiter quote failed: {quote_resp.status_code}", "tx_signature": ""}

                quote = quote_resp.json()
                out_amount = int(quote.get("outAmount", 0))

                if out_amount <= 0:
                    return {"success": False, "error": "Jupiter returned 0 output", "tx_signature": ""}

                # Step 2: Get swap transaction
                swap_resp = await client.post(
                    config.JUPITER_SWAP_API,
                    json={
                        "quoteResponse": quote,
                        "userPublicKey": self.wallet_pubkey,
                        "wrapAndUnwrapSol": True,
                        "dynamicComputeUnitLimit": True,
                        "dynamicSlippage": True,
                        "prioritizationFeeLamports": {
                            "priorityLevelWithMaxLamports": {
                                "maxLamports": 1000000,
                                "priorityLevel": "high"
                            }
                        },
                    }
                )

                if swap_resp.status_code != 200:
                    return {"success": False, "error": f"Jupiter swap build failed: {swap_resp.status_code}", "tx_signature": ""}

                swap_data = swap_resp.json()
                swap_tx_b64 = swap_data.get("swapTransaction", "")

                if not swap_tx_b64:
                    return {"success": False, "error": "No swap transaction returned", "tx_signature": ""}

                # Step 3: Deserialize, sign, send
                tx_bytes = base64.b64decode(swap_tx_b64)
                tx = VersionedTransaction.from_bytes(tx_bytes)
                signed_tx = VersionedTransaction(tx.message, [self._keypair])

                tx_sig = await self._send_transaction(signed_tx)
                if tx_sig:
                    sol_received = out_amount / 1e9
                    return {
                        "success": True,
                        "tx_signature": tx_sig,
                        "error": "",
                        "sol_received": sol_received,
                        "method": "jupiter",
                    }
                else:
                    return {"success": False, "error": "Jupiter TX not confirmed", "tx_signature": ""}

        except Exception as e:
            return {"success": False, "error": f"Jupiter sell exception: {str(e)[:200]}", "tx_signature": ""}

    async def _sell_via_pumpportal(self, mint: str, amount_pct: float) -> Dict:
        """Sell via PumpPortal (for tokens still on bonding curve)."""
        try:
            # PumpPortal accepts percentage as string like "100%" for full sell
            pct_str = f"{int(amount_pct * 100)}%"

            async with httpx.AsyncClient(timeout=30) as client:
                payload = {
                    "publicKey": self.wallet_pubkey,
                    "action": "sell",
                    "mint": mint,
                    "amount": pct_str,
                    "denominatedInSol": "false",
                    "slippage": config.SLIPPAGE_BPS / 100,
                    "priorityFee": 0.001,
                    "pool": "pump",
                }

                resp = await client.post(
                    "https://pumpportal.fun/api/trade-local",
                    json=payload,
                )

                if resp.status_code == 200:
                    tx_bytes = resp.content
                    tx = VersionedTransaction.from_bytes(tx_bytes)
                    signed_tx = VersionedTransaction(tx.message, [self._keypair])

                    tx_sig = await self._send_transaction(signed_tx)
                    if tx_sig:
                        return {
                            "success": True,
                            "tx_signature": tx_sig,
                            "error": "",
                            "method": "pumpportal_sell",
                        }
                    else:
                        return {"success": False, "error": "PumpPortal sell TX not confirmed", "tx_signature": ""}
                else:
                    return {"success": False, "error": f"PumpPortal sell {resp.status_code}", "tx_signature": ""}

        except Exception as e:
            return {"success": False, "error": f"PumpPortal sell exception: {str(e)[:200]}", "tx_signature": ""}

    async def _send_transaction(self, signed_tx: VersionedTransaction) -> Optional[str]:
        """Send a signed transaction to the Solana network."""
        try:
            tx_bytes = bytes(signed_tx)
            tx_b64 = base64.b64encode(tx_bytes).decode("utf-8")

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    config.HELIUS_RPC_URL,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "sendTransaction",
                        "params": [
                            tx_b64,
                            {
                                "encoding": "base64",
                                "skipPreflight": False,
                                "preflightCommitment": "confirmed",
                                "maxRetries": 3,
                            }
                        ],
                    }
                )

                if resp.status_code == 200:
                    data = resp.json()
                    if "result" in data:
                        tx_sig = data["result"]
                        logger.info(f"TX sent: {tx_sig}")

                        # Wait for confirmation
                        confirmed = await self._confirm_transaction(tx_sig)
                        if confirmed:
                            return tx_sig
                        else:
                            logger.warning(f"TX not confirmed: {tx_sig}")
                    elif "error" in data:
                        error = data["error"]
                        logger.error(f"TX send error: {error}")
                else:
                    logger.error(f"RPC returned {resp.status_code}")

        except Exception as e:
            logger.error(f"Send TX exception: {e}")

        return None

    async def _confirm_transaction(self, signature: str, timeout: int = 30) -> bool:
        """Wait for transaction confirmation."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        config.HELIUS_RPC_URL,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getSignatureStatuses",
                            "params": [[signature], {"searchTransactionHistory": True}],
                        }
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        statuses = data.get("result", {}).get("value", [])
                        if statuses and statuses[0]:
                            status = statuses[0]
                            if status.get("confirmationStatus") in ("confirmed", "finalized"):
                                if status.get("err") is None:
                                    return True
                                else:
                                    logger.error(f"TX failed on-chain: {status['err']}")
                                    return False
            except Exception:
                pass

            await asyncio.sleep(2)

        return False
