"""从 Valve 官方 datafeed 刷新中文英雄名映射。

新版本加英雄时跑一次：
    .venv/bin/python -m scripts.refresh_heroes

OpenDota 的 /matches/{id} 只给 hero_id，且 constants 接口只有英文名，
所以中文名走 Valve 自己的 datafeed。注意公司网络封了 raw.githubusercontent.com，
不要改回 dotaconstants 那条路。
"""

import json
from pathlib import Path

import httpx

SOURCE = "https://www.dota2.com/datafeed/herolist?language=schinese"
TARGET = Path(__file__).parents[1] / "data" / "heroes_zh.json"


def main() -> None:
    response = httpx.get(SOURCE, timeout=30.0)
    response.raise_for_status()
    heroes = response.json()["result"]["data"]["heroes"]
    mapping = {str(hero["id"]): hero["name_loc"] for hero in heroes}
    TARGET.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=0, sort_keys=True),
        encoding="utf-8",
    )
    print(f"已写入 {len(mapping)} 个英雄 → {TARGET}")


if __name__ == "__main__":
    main()
