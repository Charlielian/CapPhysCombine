#!/usr/bin/env python3
"""CLI entry for CapPhysCombine (web UI is served by FastAPI)."""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from app.pipelines.core import (
    PHYSICAL_TABLE_AVAILABLE,
    detect_sector_conflicts,
    run_low_efficiency_pipeline,
    run_physical_table_pipeline,
    run_physical_table_sector_fix,
    run_pipeline,
)
from app.pipelines.zero_low_flow import run_zero_low_flow_pipeline


def cli_main() -> None:
    parser = argparse.ArgumentParser(description="容量表&物理表处理工具")
    parser.add_argument(
        "--mode",
        choices=["capacity", "physical", "loweff", "zero_low_flow"],
        default="capacity",
        help="运行模式: capacity / physical / loweff / zero_low_flow",
    )
    parser.add_argument(
        "--physical-dir",
        type=str,
        help="物理表数据根目录（仅 physical 模式）",
    )
    parser.add_argument(
        "--physical-output",
        type=str,
        help="物理表输出文件路径（仅 physical 模式）",
    )
    parser.add_argument(
        "--check-conflicts",
        type=str,
        help="检查物理表扇区冲突，参数为 Excel 路径",
    )
    parser.add_argument(
        "--fix-conflicts",
        type=str,
        help="修正物理表扇区冲突，参数为 Excel 路径",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="启动 FastAPI Web 服务（端口 4008）",
    )

    args = parser.parse_args()

    if args.serve:
        from app.main import main as serve_main

        serve_main()
        return

    if args.check_conflicts:
        if not PHYSICAL_TABLE_AVAILABLE:
            print("错误: 物理表模块未安装")
            return
        try:
            df = pd.read_excel(args.check_conflicts)
            conflicts = detect_sector_conflicts(df)
            if conflicts.empty:
                print("未发现扇区冲突")
            else:
                print(f"发现 {len(conflicts)} 条扇区冲突")
                print(conflicts.head(20).to_string(index=False))
        except Exception as e:
            print(f"检查失败: {e}")
        return

    if args.fix_conflicts:
        if not PHYSICAL_TABLE_AVAILABLE:
            print("错误: 物理表模块未安装")
            return
        try:
            result = run_physical_table_sector_fix(
                input_path=args.fix_conflicts,
                auto_fix=True,
            )
            if result:
                print("扇区冲突修正完成")
                print(f"  冲突记录: {len(result.get('conflict_df', pd.DataFrame()))} 条")
                print(f"  修正记录: {len(result.get('fix_df', pd.DataFrame()))} 条")
        except Exception as e:
            print(f"修正失败: {e}")
        return

    if args.mode == "physical":
        if not PHYSICAL_TABLE_AVAILABLE:
            print("错误: 物理表模块未安装，无法使用此功能")
            return
        from app.pipelines.core import BASE_DIR

        base_dir = args.physical_dir or str(BASE_DIR)
        try:
            run_physical_table_pipeline(
                base_dir=base_dir,
                output_path=args.physical_output,
            )
        except Exception as e:
            print(f"物理表汇总失败: {e}")
            sys.exit(1)
        return

    if args.mode == "loweff":
        try:
            run_low_efficiency_pipeline()
        except Exception as e:
            print(f"低效小区分析失败: {e}")
            sys.exit(1)
        return

    if args.mode == "zero_low_flow":
        try:
            run_zero_low_flow_pipeline()
        except Exception as e:
            print(f"零低流量风险分析失败: {e}")
            sys.exit(1)
        return

    try:
        run_pipeline()
    except Exception as e:
        print(f"容量表合成失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli_main()
