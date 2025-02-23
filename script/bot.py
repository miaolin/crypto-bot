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
        self.patterns = {"rugged": [], "pumped": [], "new_pairs": [], "fake_volume": []}

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
        self.conn.commit()

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

    def fetch_pocker_universe_data(self, pair_address: str, volume: float, liquidity: float) -> bool:
        """模拟调用 Pocker Universe API 检测虚假交易量"""
        url = self.config["pocker_universe_api"]
        payload = {
            "pair_address": pair_address,
            "volume_24h": volume,
            "liquidity_usd": liquidity
        }
        try:
            # 模拟 API 调用
            # response = requests.post(url, json=payload)
            # result = response.json().get("is_fake", False)
            
            # 模拟逻辑：如果交易量/流动性比例过高且交易次数不足，认为是虚假
            fake_pattern = self.config["patterns"]["fake_volume"]
            transaction_count = 100  # 假设从其他数据源获取，简化模拟
            ratio = volume / liquidity if liquidity > 0 else float('inf')
            result = ratio > fake_pattern["volume_liquidity_ratio"] and transaction_count < fake_pattern["min_transactions"]
            return result
        except Exception as e:
            print(f"Pocker Universe API 请求失败: {e}")
            return False

    def apply_filters(self, pair: Dict) -> bool:
        """应用过滤器和黑名单"""
        filters = self.config["filters"]
        blacklists = self.config["blacklists"]

        symbol = pair["baseToken"]["symbol"]
        dev_address = pair.get("maker", {}).get("address", "")
        if (symbol in blacklists["tokens"] or 
            dev_address in blacklists["developers"] or 
            symbol in blacklists["fake_volume_tokens"]):
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

        # 检查虚假交易量
        if self.fetch_pocker_universe_data(analysis["pair_address"], volume_24h, liquidity):
            analysis["type"] = "fake_volume"
            self.config["blacklists"]["fake_volume_tokens"].append(analysis["symbol"])
        else:
            analysis["type"] = self.detect_patterns(analysis)
        
        return analysis

    def detect_patterns(self, analysis: Dict) -> str:
        """检测模式：rugged, pumped 或 new_pair"""
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

    def save_analysis(self, analysis: Dict):
        """保存分析结果到数据库"""
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
            
            # 保存更新后的黑名单到 config 文件
            with open("config.json", "w") as f:
                json.dump(self.config, f, indent=4)
            
            time.sleep(self.config["check_interval"])

    def __del__(self):
        """关闭数据库连接"""
        self.conn.close()

if __name__ == "__main__":
    bot = DexScreenerBot()
    try:
        bot.run()
    except KeyboardInterrupt:
        print("机器人已停止")