#!/usr/bin/env python3
"""Build exp/fixtures/translate_1000.json — 1000 chuỗi game giả lập để đo
batch dịch + khử trùng lặp (mục A, đợt bổ sung 13:52).

Nội dung: chuỗi UI/menu/item/dialogue/log/desc kiểu game thật (9 Kings),
có trùng lặp thật giữa các key (cùng value xuất hiện ở nhiều key), có
chuỗi nằm trong từ điển offline (Play/Quit/...) để đo glossary-first,
có value nhiều dòng (segment = dòng, không phải key).

Meta sidecar translate_1000_meta.json ghi: tổng key, số value duy nhất,
số value trùng, danh sách value thuộc từ điển offline (tính bằng chính
GLOSSARY_EN_VI của pb3/sdk/translate.py — không đoán tay).

Chạy: python3 build_translate_1000_fixture.py (từ exp/)
"""
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "..", "site")
sys.path.insert(0, os.path.join(SITE, "pb3"))
from sdk.translate import GLOSSARY_EN_VI  # noqa: E402

rng = random.Random(20261116)

# --- Pool chuỗi UI ngắn (một phần nằm trong từ điển offline) ---
UI_GLOSSARY = ["Play", "Quit", "Settings", "Save", "Load", "Options",
               "Exit", "Back", "Cancel", "Confirm", "OK", "Yes", "No",
               "Buy", "Pause", "Resume", "Restart", "Shop", "Help",
               "Sound", "Music", "Language", "Close", "Delete", "Retry"]
UI_OTHER = ["Start Game", "New Game", "Continue Game", "How to Play",
            "Select Level", "Tap to Start", "Game Over", "Level Complete",
            "Choose Your King", "Battle Pass", "Daily Reward", "Claim All",
            "Watch Ad", "Invite Friends", "Rate Us", "More Games",
            "Are you sure?", "Connection Lost", "Try Again",
            "Victory!", "Defeat", "Draw!", "Well Done!", "New Record!"]
ITEMS = ["Iron Sword", "Healing Potion", "Dragon Shield", "Fire Bow",
         "Mana Crystal", "Golden Key", "Silver Coin", "Magic Scroll",
         "Steel Armor", "Wooden Staff", "Thunder Hammer", "Ice Dagger",
         "Phoenix Feather", "Ancient Rune", "King's Crown", "Royal Banner",
         "War Horn", "Torch", "Rope", "Herb", "Bread", "Apple",
         "Leather Boots", "Chain Mail", "Crossbow", "Battle Axe"]
DIALOG = ["Hello, traveler.", "The king is waiting for you.",
          "We need more gold for the army.",
          "The northern gate has fallen!",
          "Beware the dragon in the mountains.",
          "I have a quest for you, brave one.",
          "Thank you for saving our village.",
          "The treasure is buried under the old oak.",
          "No one has returned from the dark forest.",
          "Your sword looks sharp today.",
          "The war will end soon, I hope.",
          "Follow me, I know a shortcut.",
          "Do you have any potions to spare?",
          "The merchant will be here at dawn.",
          "Stay quiet, the guards are near.",
          "This land belongs to the nine kings.",
          "I lost my ring in the river.",
          "The wizard knows the answer.",
          "Winter is coming to the valley.",
          "Let us drink to victory!"]
DESC = ["A sturdy blade forged in the fires of the northern mountains.",
        "Restores 50 health points when used in battle.",
        "A shield blessed by the dragon priests of old.",
        "Increases attack speed by 15 percent for 30 seconds.",
        "A mysterious crystal that glows when danger is near.",
        "Opens any locked door in the castle, but breaks after one use.",
        "The finest armor money can buy in the capital city.",
        "A humble meal that restores a small amount of health.",
        "Crafted by the royal blacksmith for the king's guard.",
        "Its edge never dulls, no matter how many battles it sees."]
LOG = ["Player entered the throne room for the first time today and spoke "
       "with the advisor about the upcoming war council meeting.",
       "The army marched north at dawn, carrying supplies for a siege "
       "that could last for many long weeks in the cold mountains.",
       "A messenger arrived from the eastern kingdom bearing news of a "
       "new alliance and a request for military aid against the invaders.",
       "The treasury reported a surplus of gold after the autumn harvest, "
       "allowing the king to fund the construction of a new granary.",
       "Scouts spotted enemy cavalry near the river crossing and warned "
       "the garrison to prepare the defenses before nightfall."]


def build_uniques():
    """~650 value duy nhất: pool viết tay + biến thể có số/hậu tố."""
    uniques = []
    uniques += UI_GLOSSARY + UI_OTHER + ITEMS + DIALOG + DESC + LOG
    # Biến thể item: "Iron Sword of Fire", "Iron Sword +2", ...
    suffixes = ["of Fire", "of Ice", "of Thunder", "of the Bear",
                "+1", "+2", "+3", "II", "III"]
    for it in ITEMS:
        for suf in rng.sample(suffixes, 4):
            uniques.append(it + " " + suf)
    # Biến thể level/số: "Level 12 cleared!", "Wave 7 incoming!"...
    for i in range(1, 121):
        uniques.append("Level %d cleared!" % i)
    for i in range(1, 81):
        uniques.append("Wave %d incoming!" % i)
    for i in range(1, 61):
        uniques.append("You earned %d gold." % (i * 10))
    for i in range(1, 41):
        uniques.append("Enemy wave %d defeated." % i)
    for i in range(1, 61):
        uniques.append("Quest %d: Defeat the bandit leader." % i)
    for i in range(1, 41):
        uniques.append("You found %d coins." % (i * 5))
    for i in range(1, 31):
        uniques.append("Day %d: The kingdom is at peace." % i)
    for i in range(1, 31):
        uniques.append("Floor %d of the tower." % i)
    # Value nhiều dòng (2-3 dòng) — segment tính theo dòng
    for i in range(1, 16):
        uniques.append("Chapter %d\nThe journey begins." % i)
    for i in range(1, 11):
        uniques.append("Tip %d: Save your potions\nfor the boss fight." % i)
    rng.shuffle(uniques)
    return uniques[:650]


def main():
    uniques = build_uniques()
    assert len(uniques) == 650, len(uniques)
    # 1000 key = 650 unique + 350 trùng (lấy lại từ 650 unique đó)
    dups = [rng.choice(uniques) for _ in range(350)]
    values = uniques + dups
    rng.shuffle(values)
    assert len(values) == 1000

    prefixes = (["menu_"] * 120 + ["ui_"] * 100 + ["button_"] * 60 +
                ["title_"] * 40 + ["item_"] * 180 + ["dialog_"] * 150 +
                ["story_"] * 80 + ["log_"] * 100 + ["desc_"] * 100 +
                ["misc_"] * 70)
    assert len(prefixes) == 1000
    rng.shuffle(prefixes)
    data = {}
    for i, (pre, val) in enumerate(zip(prefixes, values)):
        data["%s%04d" % (pre, i)] = val

    out = os.path.join(HERE, "fixtures", "translate_1000.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    # Meta: đo bằng chính từ điển offline (không đoán tay)
    glossary_values = sorted({v for v in set(values)
                              if v.strip().casefold() in GLOSSARY_EN_VI})
    meta = {
        "total_keys": len(data),
        "unique_values": len(set(values)),
        "duplicate_values": len(values) - len(set(values)),
        "duplicate_rate_values": round(
            (len(values) - len(set(values))) / len(values), 4),
        "glossary_values": glossary_values,
        "glossary_keys": sum(1 for v in values
                             if v.strip().casefold() in GLOSSARY_EN_VI),
        "multiline_values": sum(1 for v in values if "\n" in v),
        "note": ("duplicate_rate_values tính theo VALUE (key-value); "
                 "tỷ lệ khử trùng lặp thật theo SEGMENT (dòng đã che) do "
                 "harness đo và ghi trong translate_batch_results.md."),
    }
    with open(os.path.join(HERE, "fixtures", "translate_1000_meta.json"),
              "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    print("wrote", out, len(data), "keys;",
          "unique values:", meta["unique_values"],
          "; dup rate (values):", meta["duplicate_rate_values"],
          "; glossary keys:", meta["glossary_keys"])


if __name__ == "__main__":
    main()
