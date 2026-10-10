"""
一次性迁移脚本：把旧的 system_trigger/ 和 user_trigger/ 任务迁移到新的分组目录结构。

分组规则（按任务 name 匹配，从上到下先命中先归）:
  - taptap-download : name 含 "DWS_TapTap热门游戏" / "DWS_TapTap游戏基础信息" 或 == "DWS补录数据"
  - taptap-ads      : name 含 "DWS_TapApp首页广告" / "DWS_TapApp关键词广告" / "TapAndroid" / "TapIOS"
                      或 (含 "TapPC" 且 含 "广告")
  - taptap-pc-online: name 含 "DWS_TapPC" / "TapPC热玩榜" / "TaptapPC" 或 == "TaptapApp热门游戏排行榜采集任务"
  - steam           : name 含 "Steam"
  - hk-finance      : name 含 "东方财富" / "香港" / "交易所股票"
  - torchlight      : name 含 "火炬之光" 且不含 "Steam"

用法:
    python scripts/migrate_groups.py             # dry-run，只打印 旧路径→新路径 映射
    python scripts/migrate_groups.py --apply      # 真正移动文件并清理旧目录
"""

import argparse
import shutil
import sys
from collections import Counter
from pathlib import Path

import yaml

# Windows 控制台默认 GBK，强制 UTF-8 避免中文输出乱码
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "config" / "tasks"
OLD_SYSTEM = TASKS_DIR / "system_trigger"
OLD_USER = TASKS_DIR / "user_trigger"

GROUP_DESCRIPTIONS = {
    "taptap-download": "TapTap 下载榜 / 热门游戏 / 游戏基础信息采集与聚合",
    "taptap-ads": "TapApp / TapPC / TapAndroid / TapIOS 广告采集与 DWS 计算",
    "taptap-pc-online": "TapPC 在线人数 / 热玩榜 / 统计与下载榜采集",
    "steam": "Steam 游戏峰值玩家 / 评价 / 畅销榜采集",
    "hk-finance": "港股 / 东方财富 / 交易所数据采集",
    "torchlight": "火炬之光赛季明细采集",
}


def classify(name: str):
    """按 name 归类，返回 group 名或 None（未匹配）。"""
    if (
        "DWS_TapTap热门游戏" in name
        or "DWS_TapTap游戏基础信息" in name
        or name == "DWS补录数据"
    ):
        return "taptap-download"
    if (
        "DWS_TapApp首页广告" in name
        or "DWS_TapApp关键词广告" in name
        or "TapAndroid" in name
        or "TapIOS" in name
        or ("TapPC" in name and "广告" in name)
    ):
        return "taptap-ads"
    if (
        "DWS_TapPC" in name
        or "TapPC热玩榜" in name
        or "TaptapPC" in name
        or name == "TaptapApp热门游戏排行榜采集任务"
    ):
        return "taptap-pc-online"
    if "Steam" in name:
        return "steam"
    if "东方财富" in name or "香港" in name or "交易所股票" in name:
        return "hk-finance"
    if "火炬之光" in name and "Steam" not in name:
        return "torchlight"
    return None


def read_name(yaml_file: Path):
    """读取 YAML 的 name 字段（可能为引号包裹或顶层 list）。"""
    try:
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  !! 读取失败 {yaml_file}: {e}")
        return None
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return None
    return data.get("name")


def collect_mapping():
    """扫描旧目录，返回 [(old_path, group, trigger_type, name, new_path)] 与错误列表。"""
    mapping = []
    errors = []
    for old_dir, trigger_type in ((OLD_SYSTEM, "system"), (OLD_USER, "user")):
        if not old_dir.is_dir():
            continue
        for yaml_file in sorted(old_dir.glob("*.yaml")):
            name = read_name(yaml_file)
            if not name:
                errors.append((str(yaml_file), "缺少 name 字段"))
                continue
            group = classify(name)
            if not group:
                errors.append((str(yaml_file), f"未匹配任何分组: name={name!r}"))
                continue
            sub = "manual" if trigger_type == "user" else ""
            new_path = TASKS_DIR / group / sub / f"{name}.yaml"
            mapping.append((yaml_file, group, trigger_type, name, new_path))
    return mapping, errors


def main():
    parser = argparse.ArgumentParser(description="迁移任务到分组目录结构")
    parser.add_argument("--apply", action="store_true", help="真正执行移动（默认 dry-run）")
    args = parser.parse_args()

    mapping, errors = collect_mapping()

    if errors:
        print("!! 存在无法处理的文件:")
        for fp, why in errors:
            print(f"  - {fp}: {why}")
        sys.exit(1)

    names = [m[3] for m in mapping]
    dup_names = [n for n, c in Counter(names).items() if c > 1]
    new_paths = [str(m[4]) for m in mapping]
    dup_targets = [p for p, c in Counter(new_paths).items() if c > 1]

    # 打印映射
    print("=" * 100)
    print(f"共 {len(mapping)} 个任务（system={sum(1 for m in mapping if m[2]=='system')}, "
          f"user={sum(1 for m in mapping if m[2]=='user')}）")
    print("=" * 100)
    for old_path, group, trigger_type, name, new_path in mapping:
        print(f"[{trigger_type:6s}] {group:16s}  {old_path.relative_to(ROOT)}  →  {new_path.relative_to(ROOT)}")
    print("=" * 100)

    # 校验
    ok = True
    if dup_names:
        print(f"!! 重复 name: {dup_names}")
        ok = False
    if dup_targets:
        print(f"!! 重复目标路径: {dup_targets}")
        ok = False
    per_group = Counter(m[1] for m in mapping)
    for g in GROUP_DESCRIPTIONS:
        print(f"  {g:16s}: {per_group.get(g, 0)} 个任务")
    missing_groups = [g for g in GROUP_DESCRIPTIONS if per_group.get(g, 0) == 0]
    if missing_groups:
        print(f"  注意: 以下分组无任务: {missing_groups}")

    if not args.apply:
        print("\n[dry-run] 未做任何改动。确认无误后加 --apply 执行。")
        return 0 if ok else 2

    if not ok:
        print("\n!! 校验未通过，拒绝执行移动。")
        return 2

    # 真正执行
    moved = 0
    for old_path, group, trigger_type, name, new_path in mapping:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        group_dir = TASKS_DIR / group
        # README.md 组元数据说明
        readme = group_dir / "README.md"
        if not readme.exists():
            readme.write_text(
                f"# {group}\n\n{GROUP_DESCRIPTIONS.get(group, '')}\n\n"
                f"组内任务：组根 *.yaml 为 system 定时任务，manual/*.yaml 为 user 手动任务。\n",
                encoding="utf-8",
            )
        # script/ 占位目录
        script_dir = group_dir / "script"
        script_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_path), str(new_path))
        moved += 1

    # 删除空的旧目录
    for old_dir in (OLD_SYSTEM, OLD_USER):
        if old_dir.is_dir():
            try:
                old_dir.rmdir()  # 只删空目录
                print(f"已删除空旧目录: {old_dir.relative_to(ROOT)}")
            except OSError:
                print(f"旧目录非空，保留: {old_dir.relative_to(ROOT)}")

    print(f"\n迁移完成：共移动 {moved} 个文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
