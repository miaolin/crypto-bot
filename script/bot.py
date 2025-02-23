import requests
import json
import sqlite3
from datetime import datetime
import time
import pandas as pd
from typing import Dict, List

class DexScreenerBot:
    def __init__(self, config_path: str = "script/config.json"):
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        
        self.conn = sqlite3.connect(self.config["db_name"])
        self.create_tables()
        self.patterns = {"rugged": [], "pumped": [], "new_pairs": [], "fake_volume": [], "bundled": []}
        self.price_history = {}  # 存储价格历史以检测变化

    def create_tables(self):
        """创建数据库表"""
        cursor = self.conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tokens (
                pair_address TEXT PRIMARY KEY,
                chain_id TEXT,
                symbol TEXT,
                liquidity_usd REAL,
                volume_24h REAL,
                price_usd REAL,
                created_at TIMESTAMP,
                last_updated TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS analysis (
                pair_address TEXT,
                analysis_type TEXT,
                timestamp TIMESTAMP,
                details TEXT,
                FOREIGN KEY (pair_address) REFERENCES tokens (pair_address)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                pair_address TEXT,
                action TEXT,
                amount_usd REAL,
                price_usd REAL,
                timestamp TIMESTAMP,
                FOREIGN KEY (pair_address) REFERENCES tokens (pair_address)
            )
        ''')
        self.conn.commit()

    def send_telegram_message(self, message: str):
        """发送 Telegram 通知"""
        url = f"https://api.telegram.org/bot{self.config['telegram_bot_token']}/sendMessage"
        payload = {
            "chat_id": self.config["telegram_chat_id"],
            "text": message,
            "parse_mode": "Markdown"
        }
        try:
            requests.post(url, json=payload)
        except requests.RequestException as e:
            print(f"发送 Telegram 消息失败: {e}")

    def fetch_dex_data(self, chain: str = "ethereum") -> List[Dict]:
        """从 DexScreener API 获取数据"""
        url = f"{self.config['api_url']}/search?q={chain}"
        try:
            response = requests.get(url)
            response.raise_for_status()
            return response.json().get("pairs", [])
        except requests.RequestException as e:
            print(f"DexScreener API 请求失败: {e}")
            return []

    def fetch_rugcheck_report(self, token_address: str) -> Dict:
        """从 RugCheck API 获取报告"""
        url = f"{self.config['rugcheck_api']}/{token_address}/report"
        try:
            response = requests.get(url)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"RugCheck API 请求失败: {e}")
            return {}

    def fetch_pocker_universe_data(self, pair_address: str, volume: float, liquidity: float) -> bool:
        """模拟 Pocker Universe API"""
        fake_pattern = self.config["patterns"]["fake_volume"]
        ratio = volume / liquidity if liquidity > 0 else float('inf')
        return ratio > fake_pattern["volume_liquidity_ratio"] and 100 < fake_pattern["min_transactions"]

    def check_bundled_supply(self, pair: Dict) -> bool:
        """检查是否为捆绑供应（模拟）"""
        holders = pair.get("holders", [])  # 需要额外数据源
        if not holders:
            return False
        total_supply = sum(h["amount"] for h in holders)
        for holder in holders:
            if holder["amount"] / total_supply > 0.5:
                return True
        return False

    def execute_trade(self, pair_address: str, symbol: str, action: str, price_usd: float):
        """通过 Trojan 执行交易（模拟）"""
        url = f"{self.config['trojan_api']}/trade"
        payload = {
            "pair_address": pair_address,
            "action": action,
            "amount_usd": self.config["trading"]["amount_usd"],
            "price_usd": price_usd
        }
        try:
            # 模拟 Trojan API 调用
            # response = requests.post(url, json=payload)
            # response.raise_for_status()
            message = f"*{action.upper()}* {symbol}: ${self.config['trading']['amount_usd']} at ${price_usd}"
            self.send_telegram_message(message)
            
            # 记录交易
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO trades (pair_address, action, amount_usd, price_usd, timestamp)
                VALUES (?, ?, ?, ?, ?)
            ''', (pair_address, action, self.config["trading"]["amount_usd"], price_usd, datetime.now()))
            self.conn.commit()
        except requests.RequestException as e:
            print(f"Trojan 交易失败: {e}")

    def apply_filters(self, pair: Dict) -> bool:
        """应用过滤器和黑名单"""
        filters = self.config["filters"]
        blacklists = self.config["blacklists"]

        symbol = pair["baseToken"]["symbol"]
        dev_address = pair.get("maker", {}).get("address", "")
        if (symbol in blacklists["tokens"] or 
            dev_address in blacklists["developers"] or 
            symbol in blacklists["fake_volume_tokens"] or 
            symbol in blacklists["bundled_tokens"]):
            return False

        liquidity = pair.get("liquidity", {}).get("usd", 0)
        volume_24h = pair.get("volume", {}).get("h24", 0)
        creation_time = pair.get("pairCreatedAt", 0) / 1000
        age_hours = (datetime.now() - datetime.fromtimestamp(creation_time)).total_seconds() / 3600

        return (liquidity >= filters["min_liquidity_usd"] and
                volume_24h >= filters["min_volume_24h"] and
                age_hours <= filters["max_age_hours"])

    def analyze_pair(self, pair: Dict) -> Dict:
        """分析单个交易对"""
        liquidity = pair.get("liquidity", {}).get("usd", 0)
        volume_24h = pair.get("volume", {}).get("h24", 0)
        price = pair.get("priceUsd", 0)
        creation_time = pair.get("pairCreatedAt", 0) / 1000
        
        analysis = {
            "pair_address": pair["pairAddress"],
            "chain_id": pair["chainId"],
            "symbol": pair["baseToken"]["symbol"],
            "liquidity_usd": liquidity,
            "volume_24h": volume_24h,
            "price_usd": float(price) if price else 0,
            "created_at": datetime.fromtimestamp(creation_time),
            "last_updated": datetime.now()
        }

        # RugCheck 检查
        rugcheck_report = self.fetch_rugcheck_report(pair["pairAddress"])
        if rugcheck_report.get("status") != "GOOD":
            analysis["type"] = "unsafe"
            return analysis

        # 检查捆绑供应
        if self.check_bundled_supply(pair):
            analysis["type"] = "bundled"
            self.config["blacklists"]["bundled_tokens"].append(analysis["symbol"])
            return analysis

        # Pocker Universe 检查虚假交易量
        if self.fetch_pocker_universe_data(analysis["pair_address"], volume_24h, liquidity):
            analysis["type"] = "fake_volume"
            self.config["blacklists"]["fake_volume_tokens"].append(analysis["symbol"])
            return analysis

        # 常规模式检测
        analysis["type"] = self.detect_patterns(analysis)

        # 交易逻辑
        if analysis["type"] in ["new_pair", "pumped"]:  # 只对新对或被泵的代币交易
            self.handle_trading(analysis)

        return analysis

    def detect_patterns(self, analysis: Dict) -> str:
        """检测模式"""
        patterns = self.config["patterns"]
        liquidity = analysis["liquidity_usd"]
        volume = analysis["volume_24h"]
        age_hours = (datetime.now() - analysis["created_at"]).total_seconds() / 3600

        if (liquidity < self.config["filters"]["min_liquidity_usd"] * patterns["rugged"]["liquidity_threshold"] and 
            volume > liquidity * patterns["rugged"]["volume_multiplier"]):
            return "rugged"
        elif (volume > liquidity * patterns["pumped"]["volume_multiplier"] and 
              age_hours < patterns["pumped"]["max_age_hours"]):
            return "pumped"
        elif age_hours < patterns["new_pair"]["max_age_hours"]:
            return "new_pair"
        return "normal"

    def handle_trading(self, analysis: Dict):
        """处理交易逻辑"""
        pair_address = analysis["pair_address"]
        symbol = analysis["symbol"]
        current_price = analysis["price_usd"]

        # 获取历史价格
        if pair_address not in self.price_history:
            self.price_history[pair_address] = []
        previous_price = self.price_history[pair_address][-1] if self.price_history[pair_address] else current_price
        self.price_history[pair_address].append(current_price)

        # 计算价格变化
        price_change = (current_price - previous_price) / previous_price if previous_price else 0

        # 买入/卖出逻辑
        trading_config = self.config["trading"]
        if price_change >= trading_config["buy_threshold"]:
            self.execute_trade(pair_address, symbol, "buy", current_price)
        elif price_change <= -trading_config["sell_threshold"]:
            self.execute_trade(pair_address, symbol, "sell", current_price)

    def save_analysis(self, analysis: Dict):
        """保存分析结果"""
        cursor = self.conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO tokens 
            (pair_address, chain_id, symbol, liquidity_usd, volume_24h, price_usd, created_at, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            analysis["pair_address"], analysis["chain_id"], analysis["symbol"],
            analysis["liquidity_usd"], analysis["volume_24h"], analysis["price_usd"],
            analysis["created_at"], analysis["last_updated"]
        ))

        if analysis["type"] != "normal":
            cursor.execute('''
                INSERT INTO analysis (pair_address, analysis_type, timestamp, details)
                VALUES (?, ?, ?, ?)
            ''', (
                analysis["pair_address"], analysis["type"], analysis["last_updated"],
                json.dumps({"liquidity": analysis["liquidity_usd"], "volume": analysis["volume_24h"]})
            ))
            self.patterns[analysis["type"]].append(analysis["symbol"])

        self.conn.commit()

    def run(self):
        """运行机器人"""
        print("机器人启动...")
        self.send_telegram_message("DexScreener 机器人已启动！")
        while True:
            pairs = self.fetch_dex_data()
            for pair in pairs:
                if self.apply_filters(pair):
                    analysis = self.analyze_pair(pair)
                    self.save_analysis(analysis)
                    if analysis["type"] != "normal":
                        print(f"检测到 {analysis['type']}: {analysis['symbol']} - 流动性: ${analysis['liquidity_usd']}, 24h成交量: ${analysis['volume_24h']}")

            print("\n当前模式统计:")
            for pattern, tokens in self.patterns.items():
                print(f"{pattern}: {len(tokens)} 个 - {tokens[-5:]}")
            
            with open("config.json", "w") as f:
                json.dump(self.config, f, indent=4)
            
            time.sleep(self.config["check_interval"])

    def __del__(self):
        """关闭数据库连接"""
        self.conn.close()
        self.send_telegram_message("DexScreener 机器人已停止。")

if __name__ == "__main__":
    bot = DexScreenerBot()
    try:
        bot.run()
    except KeyboardInterrupt:
        print("机器人已停止")