"""
Dashboard - Web UI for monitoring the bot.
Runs on port 4001 (matching your existing setup).
"""

import time
import json
from flask import Flask, render_template_string, jsonify

import config

# Will be set by bot.py
_bot_instance = None

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pump.fun Bot Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'JetBrains Mono', 'Courier New', monospace;
            background: #0a0e17;
            color: #e1e8f0;
            padding: 20px;
        }
        .header {
            text-align: center;
            padding: 20px;
            border-bottom: 1px solid #1e293b;
            margin-bottom: 20px;
        }
        .header h1 { color: #22c55e; font-size: 24px; }
        .header .status {
            margin-top: 8px;
            color: #94a3b8;
            font-size: 14px;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
        }
        .card {
            background: #111827;
            border: 1px solid #1e293b;
            border-radius: 8px;
            padding: 16px;
        }
        .card h3 {
            color: #64748b;
            font-size: 12px;
            text-transform: uppercase;
            margin-bottom: 12px;
        }
        .stat {
            display: flex;
            justify-content: space-between;
            padding: 6px 0;
            border-bottom: 1px solid #1e293b;
        }
        .stat:last-child { border-bottom: none; }
        .stat .label { color: #94a3b8; }
        .stat .value { color: #f1f5f9; font-weight: bold; }
        .stat .value.green { color: #22c55e; }
        .stat .value.red { color: #ef4444; }
        .stat .value.yellow { color: #eab308; }
        .positions { margin-top: 20px; }
        .positions h2 {
            color: #f1f5f9;
            margin-bottom: 12px;
            font-size: 16px;
        }
        .position-card {
            background: #111827;
            border: 1px solid #1e293b;
            border-radius: 8px;
            padding: 12px 16px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .position-card .token {
            font-weight: bold;
            color: #f1f5f9;
        }
        .position-card .pnl {
            font-size: 14px;
            font-weight: bold;
        }
        .trades { margin-top: 20px; }
        .trades h2 {
            color: #f1f5f9;
            margin-bottom: 12px;
            font-size: 16px;
        }
        .trade-row {
            background: #111827;
            border: 1px solid #1e293b;
            border-radius: 6px;
            padding: 8px 12px;
            margin-bottom: 4px;
            font-size: 12px;
            display: flex;
            justify-content: space-between;
        }
        .trade-row .action-buy { color: #22c55e; }
        .trade-row .action-sell { color: #ef4444; }
        .refresh-btn {
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: #1e293b;
            border: 1px solid #334155;
            color: #e1e8f0;
            padding: 10px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-family: inherit;
        }
        .refresh-btn:hover { background: #334155; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🚀 Pump.fun Trading Bot</h1>
        <div class="status" id="status">Loading...</div>
    </div>

    <div class="grid" id="stats-grid"></div>

    <div class="positions">
        <h2>📊 Active Positions</h2>
        <div id="positions-list"></div>
    </div>

    <div class="trades">
        <h2>📋 Recent Trades</h2>
        <div id="trades-list"></div>
    </div>

    <button class="refresh-btn" onclick="loadData()">🔄 Refresh</button>

    <script>
        async function loadData() {
            try {
                const resp = await fetch('/api/status');
                const data = await resp.json();
                renderStatus(data);
                renderPositions(data.positions);
                renderTrades(data.recent_trades);
            } catch(e) {
                document.getElementById('status').textContent = 'Error loading data: ' + e.message;
            }
        }

        function renderStatus(data) {
            const status = data.bot_status || {};
            const stats = data.session_stats || {};

            document.getElementById('status').innerHTML =
                `<span style="color:${status.running ? '#22c55e' : '#ef4444'}">` +
                `${status.running ? '● RUNNING' : '● STOPPED'}</span> | ` +
                `Uptime: ${(status.uptime_hours || 0).toFixed(1)}h | ` +
                `Wallet: ${(status.wallet || '').substring(0, 8)}...`;

            const grid = document.getElementById('stats-grid');
            grid.innerHTML = `
                <div class="card">
                    <h3>Scanner</h3>
                    <div class="stat"><span class="label">Tokens Seen</span><span class="value">${status.scanner_seen || 0}</span></div>
                    <div class="stat"><span class="label">Evaluated</span><span class="value">${status.tokens_evaluated || 0}</span></div>
                    <div class="stat"><span class="label">Passed</span><span class="value green">${status.tokens_passed || 0}</span></div>
                    <div class="stat"><span class="label">Rejected</span><span class="value red">${status.tokens_rejected || 0}</span></div>
                    <div class="stat"><span class="label">Pass Rate</span><span class="value">${(status.pass_rate || 0).toFixed(1)}%</span></div>
                </div>
                <div class="card">
                    <h3>Trading</h3>
                    <div class="stat"><span class="label">Total Buys</span><span class="value">${stats.total_buys || 0}</span></div>
                    <div class="stat"><span class="label">Success Rate</span><span class="value">${(stats.buy_success_rate || 0).toFixed(0)}%</span></div>
                    <div class="stat"><span class="label">SOL Spent</span><span class="value">${(stats.total_sol_spent || 0).toFixed(4)}</span></div>
                    <div class="stat"><span class="label">SOL Received</span><span class="value">${(stats.total_sol_received || 0).toFixed(4)}</span></div>
                    <div class="stat"><span class="label">Net P&L</span><span class="value ${(stats.net_pnl_sol || 0) >= 0 ? 'green' : 'red'}">${(stats.net_pnl_sol || 0).toFixed(4)} SOL</span></div>
                </div>
                <div class="card">
                    <h3>Positions</h3>
                    <div class="stat"><span class="label">Active</span><span class="value yellow">${status.active_positions || 0}</span></div>
                    <div class="stat"><span class="label">Max Allowed</span><span class="value">${data.config?.max_positions || 5}</span></div>
                    <div class="stat"><span class="label">Buy Amount</span><span class="value">${data.config?.buy_amount || 0.03} SOL</span></div>
                    <div class="stat"><span class="label">Min Score</span><span class="value">${data.config?.min_score || 72}</span></div>
                </div>
                <div class="card">
                    <h3>Wallet</h3>
                    <div class="stat"><span class="label">Balance</span><span class="value">${(data.balance || 0).toFixed(4)} SOL</span></div>
                    <div class="stat"><span class="label">Reserved</span><span class="value">${data.config?.min_balance || 0.1} SOL</span></div>
                </div>
            `;
        }

        function renderPositions(positions) {
            const list = document.getElementById('positions-list');
            if (!positions || positions.length === 0) {
                list.innerHTML = '<div class="position-card">No active positions</div>';
                return;
            }
            list.innerHTML = positions
                .filter(p => p.status === 'active')
                .map(p => `
                    <div class="position-card">
                        <div>
                            <span class="token">${p.symbol || '?'}</span>
                            <span style="color:#64748b;font-size:12px;margin-left:8px">
                                MC: $${(p.current_mc_usd || 0).toLocaleString()} |
                                ${(p.age_hours || 0).toFixed(1)}h |
                                ${p.trailing_active ? '🟡 Trailing' : ''}
                            </span>
                        </div>
                        <span class="pnl" style="color:${(p.pnl_pct || 0) >= 0 ? '#22c55e' : '#ef4444'}">
                            ${(p.pnl_pct || 0) >= 0 ? '+' : ''}${(p.pnl_pct || 0).toFixed(1)}%
                        </span>
                    </div>
                `).join('');
        }

        function renderTrades(trades) {
            const list = document.getElementById('trades-list');
            if (!trades || trades.length === 0) {
                list.innerHTML = '<div class="trade-row">No trades yet</div>';
                return;
            }
            list.innerHTML = trades.slice(0, 20).map(t => {
                const time = new Date(t.time * 1000).toLocaleTimeString();
                const action = t.action === 'buy' ? 'BUY' : 'SELL';
                const cls = t.action === 'buy' ? 'action-buy' : 'action-sell';
                const status = t.success ? '✅' : '❌';
                return `
                    <div class="trade-row">
                        <span><span class="${cls}">${action}</span> ${t.symbol || '?'} @ $${(t.mc_usd || 0).toLocaleString()}</span>
                        <span>${status} ${t.sol_amount || 0} SOL | Score: ${t.score || '-'} | ${time}</span>
                    </div>
                `;
            }).join('');
        }

        loadData();
        setInterval(loadData, 10000);  // Auto-refresh every 10s
    </script>
</body>
</html>
"""


def create_dashboard_app(bot_instance):
    """Create Flask app for dashboard."""
    global _bot_instance
    _bot_instance = bot_instance

    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template_string(DASHBOARD_HTML)

    @app.route("/api/status")
    def api_status():
        if not _bot_instance:
            return jsonify({"error": "Bot not initialized"})

        import asyncio
        loop = asyncio.new_event_loop()

        try:
            balance = loop.run_until_complete(_bot_instance.trader.get_sol_balance())
        except:
            balance = 0

        return jsonify({
            "bot_status": _bot_instance.get_status(),
            "session_stats": _bot_instance.data_store.get_session_stats(),
            "positions": _bot_instance.position_manager.get_all_positions(),
            "recent_trades": _bot_instance.data_store.get_trade_log(limit=30),
            "balance": balance,
            "config": {
                "buy_amount": config.BUY_AMOUNT_SOL,
                "min_score": config.MIN_SCORE,
                "max_positions": config.MAX_ACTIVE_POSITIONS,
                "min_balance": config.MIN_SOL_BALANCE,
                "min_mc": config.MIN_MC_USD,
                "max_mc": config.MAX_MC_USD,
            }
        })

    @app.route("/api/positions")
    def api_positions():
        if not _bot_instance:
            return jsonify([])
        return jsonify(_bot_instance.position_manager.get_all_positions())

    @app.route("/api/trades")
    def api_trades():
        if not _bot_instance:
            return jsonify([])
        return jsonify(_bot_instance.data_store.get_trade_log(limit=100))

    @app.route("/api/stats")
    def api_stats():
        if not _bot_instance:
            return jsonify({})
        return jsonify(_bot_instance.position_manager.get_stats())

    return app


def run_dashboard(bot_instance):
    """Run the dashboard (blocking - run in thread)."""
    app = create_dashboard_app(bot_instance)
    app.run(
        host=config.DASHBOARD_HOST,
        port=config.DASHBOARD_PORT,
        debug=False,
        use_reloader=False,
    )
