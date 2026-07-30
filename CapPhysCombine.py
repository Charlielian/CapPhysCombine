#!/usr/bin/env python3
"""CapPhysCombine CLI — 按功能分子命令的统一入口。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


def _add_legacy_mode(parser: argparse.ArgumentParser) -> None:
    """兼容旧 --mode 参数。"""
    parser.add_argument(
        "--mode",
        choices=["capacity", "physical", "loweff", "zero-low-flow", "serve", "web"],
        default=None,
        help=argparse.SUPPRESS,
    )


def cmd_serve(args: argparse.Namespace) -> int:
    from app.main import main as serve_main

    serve_main()
    return 0


def cmd_capacity(args: argparse.Namespace) -> int:
    from app.pipelines.capacity import run_pipeline

    run_pipeline()
    return 0


def cmd_physical(args: argparse.Namespace) -> int:
    from app.pipelines.common import BASE_DIR
    from app.pipelines.physical import run_physical_table_pipeline

    run_physical_table_pipeline(base_dir=str(BASE_DIR))
    return 0


def cmd_nrm_sync(args: argparse.Namespace) -> int:
    from app.pipelines.nrm_sync import run_nrm_sync_pipeline

    stats = run_nrm_sync_pipeline(
        nrm_dir=args.nrm_dir,
        update_freq=not args.skip_freq,
    )
    print(
        f"命中 {stats.get('matched', 0)} 条 | "
        f"PCI更新 {stats.get('updated_pci', 0)} | "
        f"TAC更新 {stats.get('updated_tac', 0)} | "
        f"频点更新 {stats.get('updated_freq', 0)}"
    )
    if stats.get('extract_file'):
        print(f"提取明细: {stats['extract_file']}")
    return 0


def cmd_loweff(args: argparse.Namespace) -> int:
    from app.pipelines.loweff import run_low_efficiency_pipeline

    run_low_efficiency_pipeline()
    return 0


def cmd_zero_low_flow(args: argparse.Namespace) -> int:
    from app.pipelines.zero_low_flow import run_zero_low_flow_pipeline

    run_zero_low_flow_pipeline()
    return 0


def cmd_sector_check(args: argparse.Namespace) -> int:
    from app.pipelines.common import BASE_DIR
    from app.pipelines.sector import detect_sector_conflicts

    path = Path(args.input) if args.input else BASE_DIR / "物理表汇总结果.xlsx"
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 1
    df = pd.read_excel(path)
    conflicts = detect_sector_conflicts(df)
    print(f"冲突行数: {len(conflicts)}")
    if args.output:
        conflicts.to_excel(args.output, index=False)
        print(f"已写出: {args.output}")
    return 0


def cmd_sector_fix(args: argparse.Namespace) -> int:
    from app.pipelines.common import BASE_DIR
    from app.pipelines.sector import run_physical_table_sector_fix

    path = Path(args.input) if args.input else BASE_DIR / "物理表汇总结果.xlsx"
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 1
    result = run_physical_table_sector_fix(
        input_path=str(path),
        output_dir=str(Path(args.output_dir) if args.output_dir else BASE_DIR),
        auto_fix=True,
    )
    print(
        f"冲突: {len(result.get('conflict_df', pd.DataFrame()))}, "
        f"修正: {len(result.get('fix_df', pd.DataFrame()))}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="CapPhysCombine",
        description="容量表 / 物理表 / 低效小区 / 零低流量 统一 CLI",
    )
    _add_legacy_mode(parser)
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", aliases=["web"], help="启动 Web 服务")
    p_serve.set_defaults(func=cmd_serve)

    p_cap = sub.add_parser("capacity", help="合成容量表")
    p_cap.set_defaults(func=cmd_capacity)

    p_phy = sub.add_parser("physical", help="物理表汇总")
    p_phy.set_defaults(func=cmd_physical)

    p_nrm = sub.add_parser("nrm-sync", help="网管配置同步 PCI/TAC/频点")
    p_nrm.add_argument("--nrm-dir", help="网管配置目录（含 2.6G.xlsx / 700M.xlsx / sdr.xlsx）")
    p_nrm.add_argument("--skip-freq", action="store_true", help="只同步 PCI/TAC，不覆盖频点")
    p_nrm.set_defaults(func=cmd_nrm_sync)

    p_low = sub.add_parser("loweff", help="低效小区分析")
    p_low.set_defaults(func=cmd_loweff)

    p_zlf = sub.add_parser("zero-low-flow", help="4G 零低流量风险监控")
    p_zlf.set_defaults(func=cmd_zero_low_flow)

    p_sec = sub.add_parser("sector", help="扇区冲突工具")
    sec_sub = p_sec.add_subparsers(dest="sector_cmd", required=True)

    p_check = sec_sub.add_parser("check", help="检测扇区冲突")
    p_check.add_argument("--input", "-i", help="物理表 Excel 路径")
    p_check.add_argument("--output", "-o", help="冲突明细输出路径")
    p_check.set_defaults(func=cmd_sector_check)

    p_fix = sec_sub.add_parser("fix", help="自动修正扇区冲突")
    p_fix.add_argument("--input", "-i", help="物理表 Excel 路径")
    p_fix.add_argument("--output-dir", help="输出目录")
    p_fix.set_defaults(func=cmd_sector_fix)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    # 旧用法: python CapPhysCombine.py --mode capacity
    if args.mode and not args.command:
        legacy = {
            "capacity": cmd_capacity,
            "physical": cmd_physical,
            "loweff": cmd_loweff,
            "zero-low-flow": cmd_zero_low_flow,
            "serve": cmd_serve,
            "web": cmd_serve,
        }
        return legacy[args.mode](args)

    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
